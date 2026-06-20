import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app.config import SHORT_MEMORY_DIR, SHORT_TERM_MAX_TURNS
from app.services.storage import read_json, write_json

"""
memory.py 负责管理短期记忆，核心设计思路：
滑动窗口 + 摘要压缩：
turns 保存最近N轮完整对话。
summary滚动累积更早历史的压缩版
"""


def new_session_id() -> str:
    # 新建一个UUID作为会话id，转成16进制，取前12位
    return f"session_{uuid.uuid4().hex[:12]}"


def session_path(session_id: str) -> Path:
    """
    将session_id映射为对应的json文件路径
    1. 只保留字母、数字、下划线和短横线
    2. 拼接文件路径(短期记忆目录 + session_id + .json)
    """
    safe_id = "".join(ch for ch in session_id if ch.isalnum()
                      or ch in {"_", "-"})
    return SHORT_MEMORY_DIR / f"{safe_id}.json"


def load_session(session_id: str, user_id: Optional[str] = None) -> Dict:
    """
    加载并读取指定session_id的会话数据，如果文件不存在则返回一个新的会话结构
    """
    session = read_json(
        session_path(session_id),
        {
            "session_id": session_id,
            "user_id": user_id or "default_user",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "turns": [],
            "summary": "",
        },
    )
    if "user_id" not in session:
        session["user_id"] = user_id or "default_user"
    return session


def save_turn(
    session_id: str,
    user_message: str,
    assistant_answer: str,
    intent: str,
    tools_used: List[str],
    user_id: str = "default_user",
):
    session = load_session(session_id, user_id)
    session["user_id"] = session.get("user_id") or user_id
    session["turns"].append(
        {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_message,
            "assistant": assistant_answer,
            "intent": intent,
            "tools_used": tools_used,
        }
    )
    session["updated_at"] = session["turns"][-1]["time"]
    if len(session["turns"]) > SHORT_TERM_MAX_TURNS:
        removed = session["turns"][:-SHORT_TERM_MAX_TURNS]
        session["summary"] = build_summary(session.get("summary", ""), removed)
        session["turns"] = session["turns"][-SHORT_TERM_MAX_TURNS:]
    write_json(session_path(session_id), session)
    return session


def build_summary(previous_summary: str, turns: List[Dict]) -> str:
    facts = []
    for turn in turns[-6:]:
        facts.append(
            f"用户问题：{turn.get('user', '')}；助手回复主题：{turn.get('intent', '')}")
    joined = "；".join(facts)
    if previous_summary:
        return f"{previous_summary}；{joined}"[-1200:]
    return joined[-1200:]


def get_recent_context(session_id: str, user_id: Optional[str] = None) -> Dict:
    """
    session 结构:
    {
        session_id: str,
        user_id: str,
        created_at: str,
        turns: List[{}, {}, ...],
        summary: str
    }
    """
    session = load_session(session_id, user_id)
    if user_id and session.get("user_id", "default_user") != user_id:
        return {"summary": "", "turns": []}
    return {
        "summary": session.get("summary", ""),
        "turns": session.get("turns", [])[-SHORT_TERM_MAX_TURNS:],
    }


def list_sessions(user_id: Optional[str] = None):
    """
    遍历短期记忆目录下的全部json文件
    读取每个文件的session_id、created_at、turns数量、最后一条用户消息等信息
    返回一个列表，按照created_at倒序排序（最新的在最前面）
    """
    sessions = []
    for path in SHORT_MEMORY_DIR.glob("*.json"):
        item = read_json(path, {})
        item_user_id = item.get("user_id", "default_user")
        if user_id and item_user_id != user_id:
            continue
        sessions.append(
            {
                "session_id": item.get("session_id", path.stem),
                "user_id": item_user_id,
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at") or (
                    item.get("turns", [])[-1].get("time", "") if item.get("turns") else item.get("created_at", "")
                ),
                "turn_count": len(item.get("turns", [])),
                "last_message": (item.get("turns", [])[-1].get("user", "") if item.get("turns") else ""),
            }
        )
    return sorted(sessions, key=lambda x: x.get("updated_at", "") or x.get("created_at", ""), reverse=True)


def get_user_recent_context(user_id: str = "default_user") -> Dict:
    """
    按用户聚合最近短期上下文。
    多个会话时取最近创建的会话并汇总其最近轮次，供只知道 user_id 的场景使用。
    """
    user_sessions = list_sessions(user_id)
    turns = []
    summaries = []
    for item in user_sessions:
        session = load_session(item["session_id"], user_id)
        if session.get("summary"):
            summaries.append(session["summary"])
        turns.extend(session.get("turns", []))
        if len(turns) >= SHORT_TERM_MAX_TURNS:
            break
    turns = sorted(turns, key=lambda x: x.get("time", ""))[-SHORT_TERM_MAX_TURNS:]
    return {
        "user_id": user_id,
        "summary": "；".join(summaries)[-1200:],
        "turns": turns,
        "sessions": user_sessions,
    }


def clear_user_sessions(user_id: str = "default_user") -> int:
    removed = 0
    for item in list_sessions(user_id):
        path = session_path(item["session_id"])
        if path.exists():
            path.unlink(missing_ok=True)
            removed += 1
    return removed
