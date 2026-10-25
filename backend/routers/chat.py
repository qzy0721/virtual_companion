"""Streaming dialogue with independent input and presentation evidence."""
import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ExpressionRequest, ExpressionResponse, PresentationRequest
from backend.services.chat_service import get_chat_service
from backend.services.expression_service import get_expression_service
from backend.services.interaction_store import get_interaction_store
from backend.services.continuation_service import ContinuationService
from backend.services.evidence_memory import get_evidence_memory
from backend.services.interaction_tasks import get_memory_reconciler
from backend.routers.history import _load as load_history, clear_history_data
from PromptOptimizer import PromptOptimizer
from config import MAX_HISTORY

router = APIRouter(prefix="/api/chat", tags=["chat"])


def encode_event(kind, data, **identity):
    payload = dict(type=kind, data=data, **identity)
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(request: ChatRequest):
    role_prompt = PromptOptimizer.get_prompt_by_role(request.role_name)
    if not role_prompt:
        raise HTTPException(404, "角色不存在，请重新选择角色")
    store = get_interaction_store()
    turn_id = request.turn_id or uuid.uuid4().hex
    continuation = None
    try:
        if request.resume_from is not None:
            continuation = ContinuationService(store).start(
                request.role_name, request.resume_from, turn_id, request.session_id)
            turn = continuation["turn"]
        else:
            # Input survives provider errors and disconnects before a single token.
            turn = store.start_turn(request.role_name, request.message, turn_id, request.session_id, require_new=True)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if turn["status"] != "generating":
        raise HTTPException(409, "这次对话已结束，请使用新的轮次重试")

    from backend.services.history_projection import combine_dialogue_contexts, dialogue_context, presentation_context
    legacy = []
    for item in load_history():
        if item.get("role_name") == request.role_name:
            legacy.extend(item.get("messages", []))
    old_context = dialogue_context(legacy[-MAX_HISTORY * 2:])
    projected = store.conversation_context(request.role_name, limit=MAX_HISTORY, exclude_turn_id=turn_id)
    context = combine_dialogue_contexts([old_context, projected], MAX_HISTORY * 2)
    history = context["messages"]
    memory_context = get_evidence_memory().context(request.role_name)
    memory_context += presentation_context(context["evidence"])
    if continuation:
        memory_context += continuation["memory_context"]
    if history:
        memory_context += ("\n历史对话用于延续语境，其中助手的话不代表用户确认；"
                           "旧记录缺少呈现证据，不据此补造约定。有效记忆与当前明确修正优先。")
    if turn["input_kind"] == "user":
        get_memory_reconciler().schedule(request.role_name, turn_id, user_only=True)

    async def event_generator():
        outcome, full_text, index = "interrupted", "", 0
        stream = None
        try:
            yield encode_event("meta", {"turn_id": turn_id, "session_id": request.session_id,
                                        "role_context": role_prompt[:2000],
                                        "input_kind": turn["input_kind"], "parent_turn_id": turn["parent_turn_id"]})
            stream = get_chat_service().chat_stream(
                role_name=request.role_name,
                user_message=continuation["user_message"] if continuation else request.message,
                history=history, memory_context=memory_context,
            )
            async for kind, data in stream:
                if kind == "delta":
                    full_text += data
                elif kind in {"text", "done"}:
                    full_text = data
                if kind == "sentence":
                    segment_id = f"{turn_id}:{index}"
                    store.add_segment(turn_id, index, data, segment_id)
                    yield encode_event(kind, data, turn_id=turn_id, segment_index=index, segment_id=segment_id)
                    index += 1
                elif kind == "done":
                    store.finish_turn(turn_id, "completed", full_text)
                    outcome = "completed"
                    yield encode_event(kind, data, turn_id=turn_id)
                else:
                    if kind == "error":
                        outcome = "failed"
                    yield encode_event(kind, data, turn_id=turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            outcome = "failed"
            yield encode_event("error", str(error), turn_id=turn_id)
        finally:
            try:
                if stream is not None:
                    await stream.aclose()
            finally:
                if outcome != "completed":
                    store.finish_turn(turn_id, outcome, full_text)
                get_memory_reconciler().schedule(request.role_name, turn_id)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })


@router.post("/receipts")
async def record_presentation(request: PresentationRequest):
    try:
        receipts = [receipt.model_dump(exclude_none=True) for receipt in request.receipts]
        for receipt in receipts:
            receipt["played_ms"] = round(receipt.get("played_ms", 0))
        result = get_interaction_store().record_receipts(request.role_name, request.turn_id, receipts)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if result["accepted"] and any(r["state"] in {"displayed", "completed"} for r in receipts):
        get_memory_reconciler().schedule(request.role_name, request.turn_id)
    return result


@router.delete("/history")
async def clear_history():
    clear_history_data()
    return {"status": "ok", "message": "对话历史已清空"}


@router.post("/expressions", response_model=ExpressionResponse)
async def analyze_expressions(request: ExpressionRequest):
    expressions = await get_expression_service().analyze(
        sentences=request.sentences,
        available_expressions=request.available_expressions,
        available_actions=request.available_actions,
    )
    return ExpressionResponse(expressions=expressions)
