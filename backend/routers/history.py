# -*- coding: UTF-8 -*-
"""
对话历史 API 路由。
"""

import json
from fastapi import APIRouter, Query

from config import HISTORY_FILE
from json_store import json_file_lock, read_json, write_json

router = APIRouter(prefix="/api/history", tags=["history"])


def _load() -> list:
    """读取历史文件"""
    try:
        data = read_json(HISTORY_FILE)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def _save(data: list) -> None:
    """写入历史文件"""
    write_json(HISTORY_FILE, data)


def append_turn(role_name: str, user_msg: str, assistant_msg: str) -> None:
    """追加一轮对话到历史（由 chat 路由调用）"""
    from datetime import datetime
    with json_file_lock(HISTORY_FILE):
        data = _load()
        data.append({
            "role_name": role_name,
            "messages": [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ],
            "timestamp": datetime.now().isoformat(),
        })
        # 限制最多 200 轮
        if len(data) > 200:
            data[:] = data[-200:]
        _save(data)


@router.get("")
async def get_history(role: str = Query(None, description="按角色名过滤")):
    """获取对话历史"""
    data = _load()
    if role:
        data = [h for h in data if h.get("role_name") == role]
    return {"history": data, "total": len(data)}


@router.delete("")
async def clear_history(role: str = Query(None, description="只清空指定角色的历史")):
    """清空对话历史"""
    if role:
        with json_file_lock(HISTORY_FILE):
            data = _load()
            before = len(data)
            data = [h for h in data if h.get("role_name") != role]
            _save(data)
        return {"status": "ok", "cleared": before - len(data), "role": role}
    else:
        _save([])
        return {"status": "ok", "cleared": "all"}
