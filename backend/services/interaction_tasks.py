"""Recoverable semantic work over durable interaction evidence.

Await recover() before accepting requests and shutdown() after handlers stop.
The queue is only a wakeup index: source consumption stays in EvidenceMemory's
atomic commit, so cancellation and process crashes leave recoverable inputs.
"""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import logging
import math

from backend.services.interaction_store import now


logger = logging.getLogger(__name__)


@dataclass
class _Work:
    attempts: int = 0
    ready_at: float = 0.0


class MemoryReconciler:
    def __init__(self, get_memory=None, *, max_attempts=3, retry_delay=0.5,
                 max_retry_delay=30.0, coalesce_delay=0.08, operation_timeout=120.0):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for name, value in (("retry_delay", retry_delay), ("max_retry_delay", max_retry_delay),
                            ("coalesce_delay", coalesce_delay), ("operation_timeout", operation_timeout)):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if retry_delay <= 0 or max_retry_delay < retry_delay or operation_timeout <= 0:
            raise ValueError("retry delays and operation_timeout must be positive; max_retry_delay >= retry_delay")
        if get_memory is None:
            from backend.services.evidence_memory import get_evidence_memory
            get_memory = get_evidence_memory
        self.get_memory = get_memory
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self.coalesce_delay = coalesce_delay
        self.operation_timeout = operation_timeout
        self.pending = {}
        self.workers = {}
        self._wake = {}
        self._active = {}
        self._cooldowns = OrderedDict()
        self._closed = False
        self._recovered = False
        self._recovery_lock = asyncio.Lock()
        self._recovery = dict(state="not_started", interrupted=0, scheduled=0, repaired_analyses=0)
        self._counts = dict(attempts=0, completed=0, retries=0, exhausted=0,
                            cancelled=0, storage_errors=0, deferred=0)
        self._last_error = None

    def _start_worker(self, role_name):
        task = self.workers.get(role_name)
        if not self._closed and (task is None or task.done()):
            self.workers[role_name] = asyncio.create_task(self._run(role_name), name="interaction-memory")

    def schedule(self, role_name, turn_id, *, user_only=False):
        """Wake an input ALREADY committed to the store; return False if stopped.

        Repeated events coalesce without resetting retry budgets. After shutdown,
        the durable source will be picked up by the next process's startup.
        """
        if self._closed:
            self._counts["deferred"] += 1
            return False
        queue = self.pending.setdefault(role_name, OrderedDict())
        key = (turn_id, user_only)
        ready_at = self._cooldowns.get((role_name, *key), 0.0)
        queue.setdefault(key, _Work(ready_at=ready_at))
        self._wake.setdefault(role_name, asyncio.Event()).set()
        self._start_worker(role_name)
        return True

    @staticmethod
    def _analysis(memory, turn_id, status, error=None):
        # Targeted updates fence a clear during cancellation/recovery. Never
        # recreate analysis rows for hidden or memory-cleared history.
        with memory.store.transaction() as db:
            turn = db.execute(
                "SELECT t.version,t.seq,t.history_hidden,r.cleared_through FROM interaction_turns t "
                "JOIN interaction_roles r ON t.role_name=r.role_name WHERE t.turn_id=?", (turn_id,)).fetchone()
            if turn is not None and not turn["history_hidden"] and turn["seq"] > turn["cleared_through"]:
                db.execute(
                    "INSERT INTO evidence_analysis(turn_id,status,error,source_version,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(turn_id) DO UPDATE SET "
                    "status=excluded.status,error=excluded.error,source_version=excluded.source_version,"
                    "updated_at=excluded.updated_at", (turn_id, status, error, turn["version"], now()))
            elif turn is not None and status == "ok":
                # A memory clear can leave a pre-existing running analysis row.
                # Repair that orphan without creating an analysis or a source.
                db.execute("UPDATE evidence_analysis SET status='ok',error=NULL,source_version=?,updated_at=? "
                           "WHERE turn_id=? AND status='running'", (turn["version"], now(), turn_id))

    def _recovery_snapshot(self):
        memory = self.get_memory()
        # Cold startup only, before this process accepts any generation requests.
        # One atomic transition preserves all text, segments and receipt evidence.
        with memory.store.transaction() as db:
            interrupted = db.execute(
                "UPDATE interaction_turns SET status='interrupted',version=version+1 "
                "WHERE status='generating' AND history_hidden=0").rowcount
        repaired = 0
        jobs = []
        # Exactly one startup scan; event scheduling/retries never scan history.
        for role_name in memory.store.roles():
            turns = memory.store.turns(role_name)
            analyses = {row["turn_id"]: row for row in memory.status(role_name)["analyses"]}
            for turn in turns:
                analysis = analyses.get(turn["turn_id"])
                if analysis is None:
                    continue
                pending = analysis["pending_sources"] > 0
                if analysis["status"] == "running":
                    self._analysis(memory, turn["turn_id"], "failed" if pending else "ok",
                                   "Previous process interrupted analysis; retry required" if pending else None)
                    repaired += 1
                if pending:
                    jobs.append((role_name, turn["turn_id"]))
        return jobs, interrupted, repaired

    async def recover(self):
        """Recover before requests; never resume text/audio generation.

        Includes failed/stale/unresolved sources and committed presentation
        receipts. Consumed, hidden and memory-cleared sources are excluded.
        Repeated calls do not interrupt newly generating turns in this process.
        """
        async with self._recovery_lock:
            if self._closed:
                raise RuntimeError("reconciler has shut down")
            if not self._recovered:
                self._recovery["state"] = "running"
                try:
                    jobs, interrupted, repaired = await asyncio.to_thread(self._recovery_snapshot)
                except BaseException:
                    self._recovery["state"] = "failed"
                    raise
                if self._closed:
                    self._recovery["state"] = "deferred"
                    return self.status()
                for role_name, turn_id in jobs:
                    self.schedule(role_name, turn_id)
                self._recovery.update(state="complete", interrupted=interrupted,
                                      scheduled=len(jobs), repaired_analyses=repaired)
                self._recovered = True
            for role_name in self.pending:
                self._start_worker(role_name)
            return self.status()

    async def _persist_failure(self, turn_id, error):
        try:
            await asyncio.to_thread(self._analysis, self.get_memory(), turn_id, "failed", error)
        except Exception as failure:
            self._counts["storage_errors"] += 1
            logger.error("Memory failure status could not be persisted (%s)", type(failure).__name__)

    @staticmethod
    def _restore(queue, key, work):
        newer = queue.get(key)
        if newer is not None:
            work.attempts = max(work.attempts, newer.attempts)
            work.ready_at = max(work.ready_at, newer.ready_at)
        queue[key] = work

    async def _run(self, role_name):
        queue = self.pending[role_name]
        wake = self._wake[role_name]
        loop = asyncio.get_running_loop()
        try:
            await asyncio.sleep(self.coalesce_delay)
            while queue:
                wake.clear()
                ready = next((key for key, work in queue.items() if work.ready_at <= loop.time()), None)
                if ready is None:
                    delay = max(0, min(work.ready_at for work in queue.values()) - loop.time())
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    continue
                turn_id, user_only = ready
                work = queue.pop(ready)
                self._active[role_name] = ready
                try:
                    work.attempts += 1
                    self._counts["attempts"] += 1
                    try:
                        memory = self.get_memory()
                        operation = memory.reconcile_user if user_only else memory.reconcile_turn
                        outcome = await asyncio.wait_for(operation(turn_id), timeout=self.operation_timeout)
                        outcome_status = outcome["status"]
                        if outcome_status not in {"ok", "unchanged", "unresolved", "failed", "stale"}:
                            raise ValueError("unknown reconciliation outcome")
                    except Exception as error:
                        outcome_status = "failed"
                        # Provider exception text can include private input.
                        message = f"Background reconciliation failed: {type(error).__name__}"
                        self._last_error = dict(role_name=role_name, turn_id=turn_id, error_type=type(error).__name__)
                        logger.warning("Memory reconciliation failed (%s)", type(error).__name__)
                        await self._persist_failure(turn_id, message)
                    if outcome_status in {"failed", "stale"}:
                        if work.attempts < self.max_attempts:
                            delay = min(self.max_retry_delay, self.retry_delay * 2 ** (work.attempts - 1))
                            work.ready_at = loop.time() + delay
                            self._restore(queue, ready, work)
                            self._counts["retries"] += 1
                        else:
                            self._counts["exhausted"] += 1
                            cooldown = loop.time() + self.max_retry_delay
                            cooldown_key = (role_name, *ready)
                            self._cooldowns[cooldown_key] = cooldown
                            self._cooldowns.move_to_end(cooldown_key)
                            if len(self._cooldowns) > 256:
                                self._cooldowns.popitem(last=False)
                            # A receipt arriving during the final attempt must
                            # not immediately restart the whole retry cycle.
                            if ready in queue:
                                queue[ready].ready_at = max(queue[ready].ready_at, cooldown)
                            if outcome_status == "stale":
                                await self._persist_failure(turn_id, "Retry limit reached after evidence changed; retry required")
                    else:
                        self._counts["completed"] += 1
                        self._cooldowns.pop((role_name, *ready), None)
                except asyncio.CancelledError:
                    self._restore(queue, ready, work)
                    self._counts["cancelled"] += 1
                    raise
                finally:
                    self._active.pop(role_name, None)
        finally:
            if self.workers.get(role_name) is asyncio.current_task():
                self.workers.pop(role_name, None)
            if not queue:
                self.pending.pop(role_name, None)
                self._wake.pop(role_name, None)

    def status(self):
        """Cheap process diagnostics without history scans or conversation text."""
        current = asyncio.get_running_loop().time()
        return dict(state="stopped" if self._closed else "running", recovery=dict(self._recovery),
                    pending=sum(len(queue) for queue in self.pending.values()),
                    delayed=sum(work.ready_at > current for queue in self.pending.values() for work in queue.values()),
                    active=len(self._active), workers=sum(not task.done() for task in self.workers.values()),
                    counters=dict(self._counts), last_error=dict(self._last_error) if self._last_error else None)

    async def _cancel_workers(self):
        tasks = list(self.workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Also handles tasks cancelled before _run's first instruction.
        for role_name, task in list(self.workers.items()):
            if task.done():
                self.workers.pop(role_name, None)

    async def drain(self, timeout=2.0):
        """Flush; a deadline cancels waits but retains unconsumed durable sources.

        Unlike shutdown this permits further scheduling. A subsequent drain or
        recover also resumes wakeups retained by cancellation.
        """
        for role_name in self.pending:
            self._start_worker(role_name)
        deadline = asyncio.get_running_loop().time() + max(0, timeout)
        try:
            while self.workers:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                _, unfinished = await asyncio.wait(list(self.workers.values()), timeout=remaining)
                if unfinished:
                    await self._cancel_workers()
                    break
                for role_name, task in list(self.workers.items()):
                    if task.done():
                        self.workers.pop(role_name, None)
        except asyncio.CancelledError:
            await self._cancel_workers()
            raise

    async def shutdown(self, timeout=2.0):
        self._closed = True
        await self.drain(timeout)
        return self.status()


_reconciler = None


def get_memory_reconciler():
    global _reconciler
    if _reconciler is None:
        _reconciler = MemoryReconciler()
    return _reconciler
