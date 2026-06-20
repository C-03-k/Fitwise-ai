from typing import Optional

from app.config import PROFILE_FILE
from app.schemas import HealthProfile
from app.services.storage import read_json, write_json


def _load_profile_store() -> dict:
    data = read_json(PROFILE_FILE, {})
    if isinstance(data, dict) and "users" in data:
        return data
    if isinstance(data, dict) and data:
        return {"users": {"default_user": data}}
    return {"users": {}}


def get_profile(user_id: str = "default_user") -> Optional[dict]:
    store = _load_profile_store()
    return store.get("users", {}).get(user_id)


def save_profile(user_id: str, profile: HealthProfile) -> dict:
    store = _load_profile_store()
    users = store.setdefault("users", {})
    users[user_id] = profile.model_dump()
    write_json(PROFILE_FILE, store)
    return users[user_id]


def clear_profile(user_id: str = "default_user") -> bool:
    store = _load_profile_store()
    users = store.setdefault("users", {})
    existed = user_id in users
    if existed:
        users.pop(user_id)
        write_json(PROFILE_FILE, store)
    return existed
