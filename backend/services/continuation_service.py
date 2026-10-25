"""Server-owned continuation goals and conservative assistant playback progress.

No generation/model/audio work happens here. A single short store transaction
checks ownership and history fences, captures evidence, and inserts a new child.
"""
import json

from backend.services.history_projection import LEGACY_PLAYBACK_ANNOTATION, assistant_segment_projections


class ContinuationService:
    def __init__(self, store):
        self.store = store

    def start(self, role_name, source_turn_id, turn_id, session_id=""):
        # Use the same transactional insertion and replay guard as ordinary chat.
        with self.store.transaction() as db:
            turn, chain = self.store._start_turn(
                db, role_name, "", turn_id, session_id, require_new=True,
                input_kind="continuation", parent_turn_id=source_turn_id,
            )
        root = chain[0]
        progress = []
        for ancestor in chain:
            segments = ancestor["segments"]
            projections = assistant_segment_projections(segments,
                preserve_annotation=LEGACY_PLAYBACK_ANNOTATION in root["user_text"])
            completed = []
            repairs = []
            for segment, projection in zip(segments, projections):
                if segment["audio_state"] != "completed":
                    continue
                text, repair = projection["text"], projection["repair"]
                if text.strip():
                    completed.append(dict(segment_id=segment["segment_id"], text=text))
                if repair:
                    repairs.append(dict(segment_id=segment["segment_id"], **repair))
            progress.append(dict(
                turn_id=ancestor["turn_id"], speaker="assistant",
                generation_status=ancestor["status"], snapshot_version=ancestor["version"],
                audio_completed_segments=completed,
                partial_coverage_unknown_segment_ids=[s["segment_id"] for s in segments
                    if s["audio_state"] in {"started", "interrupted"}
                    or s["audio_state"] == "failed" and s["played_ms"] > 0],
                # Counts do not expose unplayed words or guess a character prefix.
                other_segments_without_audio_completion=sum(s["audio_state"] != "completed" for s in segments),
            ))
            if repairs:
                progress[-1]["content_projections"] = repairs
        context = dict(input_kind="continuation", parent_turn_id=source_turn_id,
                       root_turn_id=root["turn_id"], original_user_goal=root["user_text"],
                       assistant_progress=progress)
        memory_context = (
            "\n【服务端续接上下文：任务与助手进度，不是新用户事实】\n"
            "本轮是对原始任务的续接；当前 user_message 重放服务器保存的原始目标，"
            "不表示用户再次陈述或确认偏好。依据下面的助手进度接续任务，尽量避免重复已播放内容。"
            "只有 audio_completed_segments 有整段音频完成回执；这不证明理解或同意。"
            "部分播放的文字覆盖范围未知，不能按时长推算已听前缀；未完成播放的生成内容不作为已听内容。"
            "历史中的助手文字可能仅在界面显示，不能据此推断已听到。"
            "进度是创建子轮时的快照，来源可能仍在取消收尾；晚到回执不在此快照中。\n"
            + json.dumps(context, ensure_ascii=False)
        )
        return dict(turn=turn, user_message=root["user_text"], memory_context=memory_context,
                    context=context)
