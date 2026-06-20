from datetime import date, datetime
from typing import Dict, List

from app.config import BODY_METRICS_FILE
from app.schemas import BodyMetricRecord, HealthProfile
from app.services.health import calculate_profile
from app.services.storage import read_json, write_json


def _today() -> str:
    return date.today().isoformat()


def add_body_metric(record: BodyMetricRecord) -> Dict:
    data = read_json(BODY_METRICS_FILE, [])
    item = record.model_dump()  # 把Pydantic模型转成字典
    item["date"] = item.get("date") or _today()
    item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if item.get("weight_kg"):
        profile = HealthProfile(weight_kg=item["weight_kg"])
        item["bmi"] = calculate_profile(profile)["bmi"]
    data.append(item)
    write_json(BODY_METRICS_FILE, data)  # 全量覆写
    return item


def list_body_metrics(user_id: str = "default_user") -> List[Dict]:
    """
    列出指定用户的体质数据记录，按照日期排序
    """
    data = read_json(BODY_METRICS_FILE, [])
    rows = [x for x in data if x.get("user_id", "default_user") == user_id]
    return sorted(rows, key=lambda x: x.get("date", ""))


def clear_body_metrics(user_id: str = "default_user") -> int:
    data = read_json(BODY_METRICS_FILE, [])
    kept = [x for x in data if x.get("user_id", "default_user") != user_id]
    removed = len(data) - len(kept)
    write_json(BODY_METRICS_FILE, kept)
    return removed


def trend_summary(user_id: str = "default_user") -> Dict:
    rows = list_body_metrics(user_id)
    if not rows:
        return {
            "records": [],
            "summary": "暂无长期身体数据。建议先记录体重、腰围或体脂率，系统会用于趋势分析和个性化方案推荐。",
            "latest": None,
            "delta": {},
        }
    latest = rows[-1]
    first = rows[0]
    delta = {}
    for key in ["weight_kg", "waist_cm", "body_fat_percent"]:
        if latest.get(key) is not None and first.get(key) is not None:
            delta[key] = round(float(latest[key]) - float(first[key]), 2)

    notes = []
    if "weight_kg" in delta:
        if delta["weight_kg"] < 0:
            notes.append(f"体重和首次记录相比，下降 {abs(delta['weight_kg'])} kg。")
        elif delta["weight_kg"] > 0:
            notes.append(f"体重和首次记录相比，上升 {delta['weight_kg']} kg。")
        else:
            notes.append("体重和首次记录相比，保持不变。")
    if "waist_cm" in delta and delta["waist_cm"] < 0:
        notes.append(f"腰围下降 {abs(delta['waist_cm'])} cm，体型管理有积极变化。")
    if "body_fat_percent" in delta and delta["body_fat_percent"] < 0:
        notes.append(f"体脂率下降 {abs(delta['body_fat_percent'])} 个百分点。")
    return {
        "records": rows,
        "summary": " ".join(notes) or "已有长期记录，建议继续积累至少 2-4 周数据以观察稳定趋势。",
        "latest": latest,
        "delta": delta,
    }


def parse_body_metric_from_text(text: str, user_id: str = "default_user"):
    import re

    patterns = {
        "weight_kg": r"(?:体重|重量)[^\d]{0,4}(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)?",
        "waist_cm": r"(?:腰围)[^\d]{0,4}(\d+(?:\.\d+)?)\s*(?:cm|厘米)?",
        "body_fat_percent": r"(?:体脂|体脂率)[^\d]{0,4}(\d+(?:\.\d+)?)\s*%?",
        "sleep_hours": r"(?:睡了|睡眠)[^\d]{0,4}(\d+(?:\.\d+)?)\s*(?:小时|h)?",
        "steps": r"(?:步数|走了)[^\d]{0,4}(\d+)\s*(?:步)?",
    }
    values = {"user_id": user_id}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        # steps是整数，要加一个判断，不能把小数点也匹配进去
        if match:
            values[key] = float(match.group(
                1)) if key != "steps" else int(match.group(1))
    if len(values) == 1:
        return None
    return BodyMetricRecord(**values, note="由 Agent 从用户文本中提取")
