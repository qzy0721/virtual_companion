"""Read projections for dialogue text and presentation evidence.

The old history renderer appended a reserved playback annotation to assistant
speech. It can now exist in generated segments/legacy JSON. Repair only that exact
artifact on reads; never rewrite stored text, receipts, user input or audit data.
"""
import json


LEGACY_PLAYBACK_ANNOTATION = "[有语音片段仅部分播放；实际听到的文字范围未知]"


class AssistantSpeechProjection:
    """Literal streaming filter before delta, sentence and durable generation.

    Hold only a possible token prefix. A mismatch is released unchanged, even for
    normal brackets. Explicit user discussion of this exact token is opt-in text,
    so callers can preserve it rather than censor the user's requested quotation.
    """
    def __init__(self, preserve_annotation=False):
        self.pending = ""
        self.preserve_annotation = preserve_annotation

    def feed(self, chunk):
        if self.preserve_annotation:
            return chunk
        text = (self.pending + chunk).replace(LEGACY_PLAYBACK_ANNOTATION, "\n")
        self.pending = ""
        for length in range(min(len(text), len(LEGACY_PLAYBACK_ANNOTATION) - 1), 0, -1):
            if text.endswith(LEGACY_PLAYBACK_ANNOTATION[:length]):
                self.pending = text[-length:]
                return text[:-length]
        return text

    def finish(self):
        tail, self.pending = self.pending, ""
        return tail


def assistant_text_projection(text, *, preserve_annotation=False):
    if preserve_annotation:
        return text, None
    count = text.count(LEGACY_PLAYBACK_ANNOTATION)
    if not count:
        return text, None
    # Preserve a boundary between surrounding clauses; removing a marker must not
    # manufacture a new contiguous quote. Ordinary bracketed prose is untouched.
    lines = []
    for line in text.splitlines(keepends=True):
        if LEGACY_PLAYBACK_ANNOTATION in line:
            if not line.replace(LEGACY_PLAYBACK_ANNOTATION, "").strip():
                continue
            line = line.replace(LEGACY_PLAYBACK_ANNOTATION, "\n")
        lines.append(line)
    content = "".join(lines).strip()
    return content, dict(kind="legacy_playback_annotation", removed_occurrences=count,
                         audio_text_coverage="unknown", original_retained=True)


def assistant_segment_projections(segments, *, preserve_annotation=False):
    """Map complete owned annotations back onto the original sentence spans.

    A legacy annotation itself contains a semicolon, so the speech splitter can
    have persisted it across two sentences. Recognize it on the ordered source,
    then remove only its exact overlapping ranges from each eligible source.
    Looking at the full source does not authorize presenting unplayed words.
    """
    joined = "".join(segment["text"] for segment in segments)
    annotations = []
    if not preserve_annotation:
        start = 0
        while (start := joined.find(LEGACY_PLAYBACK_ANNOTATION, start)) >= 0:
            annotations.append((start, start + len(LEGACY_PLAYBACK_ANNOTATION)))
            start += len(LEGACY_PLAYBACK_ANNOTATION)
    result, offset = [], 0
    for segment in segments:
        text, pieces, rendered, touched = segment["text"], [], [], []
        end, cursor = offset + len(text), 0
        for index, (left, right) in enumerate(annotations):
            if left < end and right > offset:
                piece = text[cursor:max(0, left - offset)]
                pieces.append(piece)
                rendered.append(piece)
                if offset <= left < end:
                    rendered.append("\n")
                cursor = min(len(text), right - offset)
                touched.append(index)
        pieces.append(text[cursor:])
        rendered.append(text[cursor:])
        pieces = [piece for piece in pieces if piece]
        repair = (dict(kind="legacy_playback_annotation", removed_occurrences=len(touched),
                       audio_text_coverage="unknown", original_retained=True) if touched else None)
        result.append(dict(text="".join(rendered) if touched else text,
                           repair=repair, quote_spans=pieces, annotation_indexes=touched))
        offset = end
    return result


def project_history_messages(messages):
    """Copy assistant text for display; user text remains byte-for-byte intact."""
    projected = []
    # Preserve exact user quotations in their responding assistant turn, too.
    preserve = False
    for message in messages:
        item = dict(message)
        if item.get("role") == "user":
            preserve = LEGACY_PLAYBACK_ANNOTATION in item.get("content", "")
        if item.get("role") == "assistant" and isinstance(item.get("content"), str):
            item["content"], repair = assistant_text_projection(item["content"], preserve_annotation=preserve)
            if repair:
                item["content_projection"] = repair
        projected.append(item)
    return projected


def dialogue_context(messages):
    """Return provider-safe speech and separate evidence, including empty partials.

    Evidence carries no unpresented words. Empty assistant messages remain useful
    to a history viewer but are never sent as fabricated assistant speech.
    """
    speech, evidence = [], []
    for item in project_history_messages(messages):
        role, content = item.get("role"), item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if role == "user" or content.strip():
            speech.append(dict(role=role, content=content))
        if role == "assistant":
            state = {key: item[key] for key in ("turn_id", "segment_ids", "presentation", "partial",
                                               "content_projection") if key in item}
            state["message_index"] = len(speech) - 1 if content.strip() else None
            if "presentation" not in state:
                state["presentation_evidence"] = "unavailable"
            evidence.append(state)
    return dict(messages=speech, evidence=evidence)


def combine_dialogue_contexts(contexts, message_limit):
    """Keep evidence bound to its zero-based history message after windowing."""
    messages, evidence = [], []
    for context in contexts:
        for state in context["evidence"]:
            state = dict(state)
            if state["message_index"] is not None:
                state["message_index"] += len(messages)
            evidence.append(state)
        messages.extend(context["messages"])
    cut = max(0, len(messages) - message_limit)
    evidence = [state for state in evidence if state["message_index"] is None or state["message_index"] >= cut]
    for state in evidence:
        if state["message_index"] is not None:
            state["message_index"] -= cut
    return dict(messages=messages[cut:], evidence=evidence)


def presentation_context(evidence):
    if not evidence:
        return ""
    return ("\n【历史呈现证据：内部资料，不是对话正文】\n"
            "以下结构数据只约束如何使用历史，不要复述字段、内部注释或把它们当成说过的话。"
            "message_index 是历史消息列表内从0开始的索引，null 表示只有证据、没有可引用的助手正文。"
            "text_displayed 仅证明文字呈现，不证明已读、已听或同意。"
            "只有 audio_state=completed 是整段音频完成回执，也不证明理解或接受。"
            "started/interrupted 或 failed 且 played_ms>0 的文字覆盖范围未知，"
            "不能按毫秒推算已听前缀；没有完成回执的内容不能当作已听。"
            "content_projection 是旧内部注释的读时恢复记录，不是本轮的播放回执；"
            "旧记录缺少证据时保持未知，不补造约定。\n"
            + json.dumps(evidence, ensure_ascii=False))


def memory_item_has_legacy_annotation(item):
    """Quarantine contaminated assistant-derived memories from model context only.

    A user's actual quotation or explicit memory edit is not an internal artifact.
    Raw memory records/audits remain available for inspection and explicit edits.
    """
    if LEGACY_PLAYBACK_ANNOTATION not in item["content"]:
        return False
    sources = item.get("sources", [])
    return (any(s.get("speaker") == "assistant" for s in sources)
            and not any(s.get("annotation_is_user_quote") for s in sources)
            and not any(s.get("speaker") == "user" and LEGACY_PLAYBACK_ANNOTATION in s.get("quote", "")
                        for s in sources))
