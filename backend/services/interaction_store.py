"""Durable interaction evidence. Generation is deliberately not presentation.

All writes are short SQLite transactions; no model or network call holds a lock.
The path is injectable, and importing this module never opens private data.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import threading
from datetime import datetime, timezone

from backend.services.history_projection import (
    LEGACY_PLAYBACK_ANNOTATION, project_history_messages, dialogue_context, assistant_segment_projections,
)


MAX_CONTINUATION_CHAIN = 32


def now():
    return datetime.now(timezone.utc).isoformat()


def required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


class InteractionStore:
    def __init__(self, path=None):
        if path is None:
            from config import HISTORY_FILE
            path = Path(HISTORY_FILE).parent / "interaction_memory.sqlite3"
        self.path = str(path)
        # Keep an anchor for injectable :memory: databases; per-operation connections
        # otherwise permit concurrent callers, threads and independent processes.
        self._uri = self.path == ":memory:"
        if self._uri:
            import uuid
            self.path = f"file:interaction-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = self._connect()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS interaction_roles (
                    role_name TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    cleared_through INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS interaction_turns (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT UNIQUE NOT NULL,
                    role_name TEXT NOT NULL, session_id TEXT NOT NULL, user_text TEXT NOT NULL,
                    status TEXT NOT NULL, assistant_text TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    history_hidden INTEGER NOT NULL DEFAULT 0,
                    input_kind TEXT NOT NULL DEFAULT 'user', parent_turn_id TEXT DEFAULT NULL);
                CREATE INDEX IF NOT EXISTS interaction_role_turns
                    ON interaction_turns(role_name, seq);
                CREATE TABLE IF NOT EXISTS interaction_segments (
                    segment_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL, text TEXT NOT NULL,
                    UNIQUE(turn_id, segment_index));
                CREATE TABLE IF NOT EXISTS interaction_receipts (
                    event_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_items (
                    id TEXT PRIMARY KEY, role_name TEXT NOT NULL, content TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL,
                    revision INTEGER NOT NULL, sources TEXT NOT NULL, target_id TEXT,
                    group_id TEXT NOT NULL DEFAULT '', temporal_scope TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS evidence_role ON evidence_items(role_name);
                CREATE TABLE IF NOT EXISTS evidence_audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, operation TEXT NOT NULL, snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_processed (
                    source_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_analysis (
                    turn_id TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT,
                    source_version INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence_scope_fences (
                    role_name TEXT NOT NULL, scope TEXT NOT NULL, through_seq INTEGER NOT NULL,
                    PRIMARY KEY(role_name, scope));
                CREATE TABLE IF NOT EXISTS evidence_operations (
                    signature TEXT PRIMARY KEY, source_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS receipts_by_turn ON interaction_receipts(turn_id);
                CREATE INDEX IF NOT EXISTS processed_by_turn ON evidence_processed(turn_id);
                CREATE INDEX IF NOT EXISTS operations_by_turn ON evidence_operations(turn_id);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(interaction_turns)")}
            if "history_hidden" not in columns:
                db.execute("ALTER TABLE interaction_turns ADD COLUMN history_hidden INTEGER NOT NULL DEFAULT 0")
            if "input_kind" not in columns:
                db.execute("ALTER TABLE interaction_turns ADD COLUMN input_kind TEXT NOT NULL DEFAULT 'user'")
            if "parent_turn_id" not in columns:
                db.execute("ALTER TABLE interaction_turns ADD COLUMN parent_turn_id TEXT DEFAULT NULL")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, uri=self._uri)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self, write=True):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _turn(db, turn_id, role_name=None):
        row = db.execute("SELECT * FROM interaction_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise ValueError("unknown turn_id")
        if role_name is not None and row["role_name"] != role_name:
            raise ValueError("turn belongs to another role")
        return dict(row)

    @staticmethod
    def _segments(db, turn_id):
        rows = db.execute("SELECT * FROM interaction_segments WHERE turn_id=? ORDER BY segment_index",
                          (turn_id,)).fetchall()
        segments = {r["segment_id"]: dict(r, text_displayed=False, audio_state="unplayed", played_ms=0)
                    for r in rows}
        receipts = db.execute("SELECT payload FROM interaction_receipts WHERE turn_id=? ORDER BY rowid",
                              (turn_id,)).fetchall()
        for row in receipts:
            receipt = json.loads(row["payload"])
            for sid in receipt["segment_ids"]:
                segment = segments[sid]
                if receipt["channel"] == "text":
                    segment["text_displayed"] = True
                else:
                    old, new = segment["audio_state"], receipt["state"]
                    # Terminal failure/interruption cannot be upgraded by a late
                    # completion, nor can late started downgrade a terminal state.
                    if old == "unplayed" or old == "started" and new != "started":
                        segment["audio_state"] = new
                    segment["played_ms"] = max(segment["played_ms"], receipt.get("played_ms", 0))
        return list(segments.values())

    def get_turn(self, turn_id):
        with self.transaction(False) as db:
            turn = self._turn(db, turn_id)
            turn["segments"] = self._segments(db, turn_id)
            return turn

    def _continuation_chain(self, db, parent_turn_id, role_name, session_id):
        """Validate every ancestor and snapshot receipts under the creation lock.

        Reserve one node for the new child. A generating parent is safe to read:
        only already committed completion receipts count, without awaiting it.
        """
        chain, seen = [], set()
        while parent_turn_id is not None:
            if parent_turn_id in seen or len(chain) >= MAX_CONTINUATION_CHAIN - 1:
                raise ValueError("continuation parent chain is cyclic or too deep")
            seen.add(parent_turn_id)
            turn = self._turn(db, parent_turn_id, role_name)
            if turn["session_id"] != session_id:
                raise ValueError("continuation source belongs to another session")
            if turn["history_hidden"]:
                raise ValueError("continuation source history has been cleared")
            if chain and turn["seq"] >= chain[-1]["seq"]:
                raise ValueError("invalid continuation parent order")
            turn["segments"] = self._segments(db, parent_turn_id)
            chain.append(turn)
            if turn["input_kind"] == "user":
                if turn["parent_turn_id"] is not None:
                    raise ValueError("invalid user root parent")
                required(turn["user_text"], "original user goal")
                return list(reversed(chain))
            if turn["input_kind"] != "continuation" or turn["user_text"] or not turn["parent_turn_id"]:
                raise ValueError("invalid continuation source")
            parent_turn_id = turn["parent_turn_id"]
        raise ValueError("continuation source has no user root")

    def _start_turn(self, db, role_name, user_text, turn_id, session_id="", *, require_new=False,
                    input_kind="user", parent_turn_id=None):
        """Shared transactional insertion; return the turn and checked ancestors."""
        required(role_name, "role_name")
        required(turn_id, "turn_id")
        if not isinstance(session_id, str):
            raise ValueError("session_id must be a string")
        if input_kind not in {"user", "continuation"}:
            raise ValueError("invalid input_kind")
        if input_kind == "user":
            required(user_text, "user_text")
            if parent_turn_id is not None:
                raise ValueError("user input cannot have a parent_turn_id")
        else:
            if user_text != "":
                raise ValueError("continuation user_text must be empty")
            required(parent_turn_id, "parent_turn_id")
            if parent_turn_id == turn_id:
                raise ValueError("continuation must use a new child turn_id")
        old = db.execute("SELECT * FROM interaction_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if old:
            if (old["role_name"], old["user_text"], old["session_id"], old["input_kind"], old["parent_turn_id"]) != (
                    role_name, user_text, session_id, input_kind, parent_turn_id):
                raise ValueError("turn_id reuse with different input")
            if require_new:
                raise ValueError("这次对话已经开始，请使用新的轮次重试")
        chain = self._continuation_chain(db, parent_turn_id, role_name, session_id) if input_kind == "continuation" else []
        if not old:
            db.execute("INSERT OR IGNORE INTO interaction_roles(role_name) VALUES(?)", (role_name,))
            db.execute("INSERT INTO interaction_turns(turn_id,role_name,session_id,user_text,status,created_at,"
                       "input_kind,parent_turn_id) VALUES(?,?,?,?,?,?,?,?)",
                       (turn_id, role_name, session_id, user_text, "generating", now(), input_kind, parent_turn_id))
            db.execute("UPDATE interaction_roles SET revision=revision+1 WHERE role_name=?", (role_name,))
        turn = self._turn(db, turn_id)
        turn["segments"] = self._segments(db, turn_id)
        return turn, chain

    def start_turn(self, role_name, user_text, turn_id, session_id="", *, require_new=False,
                   input_kind="user", parent_turn_id=None):
        with self.transaction() as db:
            turn, _ = self._start_turn(db, role_name, user_text, turn_id, session_id, require_new=require_new,
                                       input_kind=input_kind, parent_turn_id=parent_turn_id)
            return turn

    def add_segment(self, turn_id, segment_index, text, segment_id):
        required(text, "text")
        required(segment_id, "segment_id")
        if type(segment_index) is not int or segment_index < 0:
            raise ValueError("segment_index must be a nonnegative integer")
        with self.transaction() as db:
            self._turn(db, turn_id)
            old = db.execute("SELECT * FROM interaction_segments WHERE segment_id=? OR "
                             "(turn_id=? AND segment_index=?)", (segment_id, turn_id, segment_index)).fetchall()
            if old:
                if len(old) != 1 or (old[0]["segment_id"], old[0]["turn_id"], old[0]["segment_index"], old[0]["text"]) != (
                        segment_id, turn_id, segment_index, text):
                    raise ValueError("segment identity/content conflict")
            else:
                db.execute("INSERT INTO interaction_segments VALUES(?,?,?,?)",
                           (segment_id, turn_id, segment_index, text))
                db.execute("UPDATE interaction_turns SET version=version+1 WHERE turn_id=?", (turn_id,))
        return self.get_turn(turn_id)

    def finish_turn(self, turn_id, status, assistant_text=""):
        if status not in {"completed", "interrupted", "failed", "cancelled"}:
            raise ValueError("invalid terminal generation status")
        if not isinstance(assistant_text, str):
            raise ValueError("assistant_text must be a string")
        with self.transaction() as db:
            turn = self._turn(db, turn_id)
            if turn["status"] != "generating" and (turn["status"], turn["assistant_text"]) != (status, assistant_text):
                raise ValueError("turn already finished with different result")
            if (turn["status"], turn["assistant_text"]) != (status, assistant_text):
                db.execute("UPDATE interaction_turns SET status=?,assistant_text=?,version=version+1 WHERE turn_id=?",
                           (status, assistant_text, turn_id))
        return self.get_turn(turn_id)

    def record_receipts(self, role_name, turn_id, receipts):
        if not isinstance(receipts, list) or not receipts or len(receipts) > 1000:
            raise ValueError("receipts must contain 1..1000 events")
        accepted = duplicates = 0
        with self.transaction() as db:
            turn = self._turn(db, turn_id, role_name)
            if turn["history_hidden"]:
                raise ValueError("turn history has been cleared")
            known = {s["segment_id"] for s in self._segments(db, turn_id)}
            for event in receipts:
                if not isinstance(event, dict):
                    raise ValueError("invalid receipt")
                required(event.get("event_id"), "event_id")
                ids = event.get("segment_ids")
                if not isinstance(ids, list) or not ids or any(not isinstance(s, str) for s in ids):
                    raise ValueError("segment_ids must be a nonempty string array")
                if len(ids) != len(set(ids)) or not set(ids) <= known:
                    raise ValueError("unknown or duplicate segment_id")
                channel, state = event.get("channel"), event.get("state")
                if not (channel == "text" and state == "displayed" or
                        channel == "audio" and state in {"started", "completed", "interrupted", "failed"}):
                    raise ValueError("invalid channel/state")
                played = event.get("played_ms", 0)
                if type(played) is not int or played < 0:
                    raise ValueError("played_ms must be a nonnegative integer")
                clean = dict(event_id=event["event_id"], segment_ids=sorted(ids),
                             channel=channel, state=state, played_ms=played)
                payload = json.dumps(clean, ensure_ascii=False, sort_keys=True)
                old = db.execute("SELECT * FROM interaction_receipts WHERE event_id=?", (clean["event_id"],)).fetchone()
                if old:
                    if old["turn_id"] != turn_id or old["payload"] != payload:
                        raise ValueError("event_id reuse with different receipt")
                    duplicates += 1
                else:
                    db.execute("INSERT INTO interaction_receipts VALUES(?,?,?,?)",
                               (clean["event_id"], turn_id, payload, now()))
                    accepted += 1
            if accepted:
                db.execute("UPDATE interaction_turns SET version=version+1 WHERE turn_id=?", (turn_id,))
            version = self._turn(db, turn_id)["version"]
        return dict(turn_id=turn_id, accepted=accepted, duplicates=duplicates, version=version)

    def history(self, role_name, limit=50):
        """Display projection: speech only in content; receipts stay structured.

        Partial audio without displayed text has empty content and evidence. No
        generated prefix is inferred from playback duration. Stored data is raw.
        """
        required(role_name, "role_name")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000 turns")
        result = []
        with self.transaction(False) as db:
            turns = db.execute("SELECT * FROM interaction_turns WHERE role_name=? AND history_hidden=0 ORDER BY seq DESC LIMIT ?",
                               (role_name, limit)).fetchall()
            for turn in reversed(turns):
                if turn["input_kind"] == "user" and turn["user_text"].strip():
                    result.append(dict(role="user", content=turn["user_text"], turn_id=turn["turn_id"]))
                segments = self._segments(db, turn["turn_id"])
                projections = assistant_segment_projections(segments,
                    preserve_annotation=LEGACY_PLAYBACK_ANNOTATION in turn["user_text"])
                projection_by_id = dict(zip((s["segment_id"] for s in segments), projections))
                visible = [s for s in segments if s["text_displayed"] or s["audio_state"] == "completed"]
                partial = [s for s in segments if s["audio_state"] in {"started", "interrupted"} or
                           s["audio_state"] == "failed" and s["played_ms"] > 0]
                if visible or partial:
                    # No interpolation of an audio percentage into a text prefix.
                    content = "".join(projection_by_id[s["segment_id"]]["text"] for s in visible)
                    if any(projection_by_id[s["segment_id"]]["repair"] for s in visible):
                        content = content.strip()
                    result.append(dict(role="assistant", content=content, turn_id=turn["turn_id"],
                                       segment_ids=[s["segment_id"] for s in visible],
                                       presentation=[dict(segment_id=s["segment_id"],
                                                          text_displayed=s["text_displayed"],
                                                          audio_state=s["audio_state"], played_ms=s["played_ms"])
                                                     for s in segments],
                                       partial=bool(partial) or len(visible) < len(segments)))
                    repaired = {index for s in visible
                                for index in projection_by_id[s["segment_id"]]["annotation_indexes"]}
                    if repaired:
                        result[-1]["content_projection"] = dict(kind="legacy_playback_annotation",
                            removed_occurrences=len(repaired), audio_text_coverage="unknown", original_retained=True)
        return project_history_messages(result)

    def conversation_context(self, role_name, limit=50, *, exclude_turn_id=None):
        """Independent LLM contract; never pass display metadata as speech."""
        return dialogue_context([m for m in self.history(role_name, limit)
                                 if m["turn_id"] != exclude_turn_id])

    def turns(self, role_name):
        required(role_name, "role_name")
        with self.transaction(False) as db:
            return [dict(r) for r in db.execute("SELECT * FROM interaction_turns WHERE role_name=? AND history_hidden=0 ORDER BY seq",
                                               (role_name,))]

    def roles(self):
        with self.transaction(False) as db:
            return [r[0] for r in db.execute("SELECT role_name FROM interaction_roles ORDER BY role_name")]

    def clear_history(self, role_name=None):
        """Hide durable event tombstones, retaining memory and immutable source audit.

        Late receipts/reconciliation cannot re-expose hidden turns. This is history
        clearing, not erasure of the independent evidence-memory provenance.
        """
        if role_name is not None:
            required(role_name, "role_name")
        with self.transaction() as db:
            roles = [role_name] if role_name is not None else [r[0] for r in db.execute("SELECT role_name FROM interaction_roles")]
            count = 0
            for role in roles:
                count += db.execute("SELECT COUNT(*) FROM interaction_turns WHERE role_name=? AND history_hidden=0", (role,)).fetchone()[0]
                db.execute("UPDATE interaction_turns SET history_hidden=1,version=version+1 WHERE role_name=? AND history_hidden=0", (role,))
                db.execute("UPDATE interaction_roles SET revision=revision+1 WHERE role_name=?", (role,))
                db.execute("DELETE FROM evidence_analysis WHERE turn_id IN (SELECT turn_id FROM interaction_turns WHERE role_name=?)", (role,))
        return count


_store = None
_store_lock = threading.Lock()


def get_interaction_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = InteractionStore()
        return _store
