"""Role-scoped evidence management. Old JSON remains read-only and unverified."""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.evidence_memory import get_evidence_memory
from backend.services.interaction_store import get_interaction_store

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryEdit(BaseModel):
    role_name: str = Field(min_length=1)
    content: str = Field(min_length=1)
    revision: int = Field(ge=1, strict=True)


class ReceiptBatch(BaseModel):
    role_name: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    receipts: list[dict]


def _error(error):
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=409 if "revision conflict" in str(error) else 400, detail=str(error))


@router.get("")
async def get_memories(role: str | None = Query(None, min_length=1)):
    mem = get_evidence_memory()
    try:
        if role is None:
            memories = await asyncio.to_thread(mem.legacy_memories)
            return {"memories": memories, "total": len(memories), "status": "unverified",
                    "processing": False, "errors": [],
                    "message": "旧摘要无角色及交互证据，未用于回答。请选择角色查看新记忆。"}
        memories = await asyncio.to_thread(mem.list_memories, role)
        state = await asyncio.to_thread(mem.status, role)
        errors = [{"turn_id": a["turn_id"], "status": a["status"], "error": a["error"]}
                  for a in state["analyses"] if a["status"] in {"failed", "stale"}]
        # Malformed legacy data must not hide valid new evidence or silently
        # disappear. It remains a separate, explicitly unverified UI section.
        try:
            legacy = await asyncio.to_thread(mem.legacy_memories)
        except (ValueError, OSError) as error:
            legacy = []
            errors.append({"status": "failed", "error": f"旧摘要读取失败: {error}"})
        return {"memories": memories, "total": len(memories), "role_name": role,
                "processing": any(a["status"] in {"pending", "running"} for a in state["analyses"]),
                "unresolved": any(a["status"] == "unresolved" for a in state["analyses"]),
                "errors": errors, "legacy_memories": legacy}
    except (ValueError, OSError) as error:
        raise _error(error) from error


@router.get("/status")
async def memory_status(role: str = Query(..., min_length=1)):
    try:
        return await asyncio.to_thread(get_evidence_memory().status, role)
    except ValueError as error:
        raise _error(error) from error


@router.post("/receipts")
async def record_receipts(body: ReceiptBatch):
    try:
        result = await asyncio.to_thread(get_interaction_store().record_receipts,
                                         body.role_name, body.turn_id, body.receipts)
        if result['accepted'] and any(r['state'] in {'displayed', 'completed'} for r in body.receipts):
            from backend.services.interaction_tasks import get_memory_reconciler
            get_memory_reconciler().schedule(body.role_name, body.turn_id)
        return result
    except ValueError as error:
        raise _error(error) from error


@router.post("/generate")
async def generate_memory(role: str = Query(..., min_length=1)):
    """Retry in turn order; never summarize the old JSON."""
    mem = get_evidence_memory()
    try:
        turns = await asyncio.to_thread(mem.store.turns, role)
        results = []
        for turn in turns:
            result = await mem.reconcile_turn(turn["turn_id"])
            results.append(result)
            if result["status"] in {"failed", "stale"}:
                break
        memories = await asyncio.to_thread(mem.list_memories, role)
        failed = any(r["status"] in {"failed", "stale"} for r in results)
        unresolved = any(r["status"] == "unresolved" for r in results)
        changed = sum(len(r["created"]) + len(r["updated"]) for r in results)
        if failed:
            message = "部分对话记忆整理失败，已保存的有效记忆仍保留，请稍后重试。"
        elif unresolved:
            message = f"已整理 {changed} 项记忆；仍有回应无法确定接受范围，已保留为未决，没有写成约定。"
        elif changed:
            message = f"记忆整理完成，已新增或更新 {changed} 项有交互证据支持的记录。"
        else:
            message = "记忆整理完成，暂时没有新的可核验记录，已有记忆保持不变。"
        return {"status": "failed" if failed else "ok", "results": results,
                "memories": memories, "memory": None, "role_name": role, "message": message}
    except ValueError as error:
        raise _error(error) from error


@router.delete("")
async def clear_memories(role: str = Query(..., min_length=1)):
    try:
        count = await asyncio.to_thread(get_evidence_memory().clear, role)
        return {"status": "ok", "cleared": count, "role_name": role}
    except ValueError as error:
        raise _error(error) from error


@router.patch("/{item_id}")
async def edit_memory(item_id: str, body: MemoryEdit):
    try:
        memory = await asyncio.to_thread(get_evidence_memory().edit, item_id, body.role_name,
                                         body.content, body.revision)
        return {"status": "ok", "memory": memory}
    except (KeyError, ValueError) as error:
        raise _error(error) from error


@router.delete("/{item_id}")
async def revoke_memory(item_id: str, role: str = Query(..., min_length=1)):
    try:
        memory = await asyncio.to_thread(get_evidence_memory().revoke, item_id, role)
        return {"status": "ok", "memory": memory}
    except (KeyError, ValueError) as error:
        raise _error(error) from error


@router.get("/{item_id}/audit")
async def memory_audit(item_id: str, role: str = Query(..., min_length=1)):
    try:
        return {"audit": await asyncio.to_thread(get_evidence_memory().audit, item_id, role)}
    except KeyError as error:
        raise _error(error) from error
