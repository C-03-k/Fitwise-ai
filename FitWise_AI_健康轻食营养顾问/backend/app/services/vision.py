import base64
import json
import re
import uuid
from pathlib import Path

from fastapi import UploadFile
from openai import OpenAI

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, UPLOAD_DIR, VISION_MODEL
from app.schemas import FoodLogItem
from app.services.food import add_user_records, estimate_item


def _extract_json(text: str):
    # 从LLM返回的文本中提取第一个json对象
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        # json.loads:把json字符串转换成Python对象（通常是字典或列表）
        # json.dumps:把Python对象转换成json字符串
        return json.loads(match.group(0))
    except Exception:
        return None


async def recognize_food_image(file: UploadFile, meal_type: str = "拍照记录", user_id: str = "default_user"):
    suffix = Path(file.filename or "food.jpg").suffix or ".jpg"
    filename = f"{uuid.uuid4()}{suffix}"
    path = UPLOAD_DIR / filename
    content = await file.read()
    path.write_bytes(content)
    image_url = f"storage/uploads/{filename}"

    if not OPENAI_API_KEY:
        item = FoodLogItem(food_name="未知食物", grams=300, meal_type=meal_type)
        record = estimate_item(item, source="image_fallback", confidence=0.2)
        record.note = "未配置视觉模型 API Key，系统使用默认热量估算。请在系统设置中配置 Key 后重新识别。"
        add_user_records([record], user_id)
        return {
            "image": image_url,
            "items": [record.model_dump()],
            "raw": {
                "note": record.note,
                "health_advice": "图片热量估算存在误差，建议结合实际重量或包装营养表校准。",
            },
        }

    b64 = base64.b64encode(content).decode("utf-8")
    mime = file.content_type or "image/jpeg"
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    prompt = """你是健康管理 App 的食物热量识别助手。
请识别图片中的食物，并估算每项食物的重量、热量、蛋白质、脂肪、碳水和置信度。
只返回 JSON，不要 markdown，不要额外解释。
格式：
{
  "items": [
    {"food_name": "鸡胸肉沙拉", "grams": 280, "calories": 420, "protein": 35, "fat": 16, "carbs": 32, "confidence": 0.78}
  ],
  "note": "估算依据和误差说明",
  "health_advice": "面向减脂/控糖/轻食场景的简短建议"
}
要求：
1. 不确定时给保守估算，并在 note 里说明误差来源。
2. 不做疾病诊断，不承诺减肥效果。
3. 如果图片不是食物，items 返回空数组，并说明原因。"""
    try:
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            temperature=0.1,
            timeout=45,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
        )
        raw = resp.choices[0].message.content
    except Exception as exc:
        item = FoodLogItem(food_name="待确认食物", grams=300, meal_type=meal_type)
        record = estimate_item(
            item, source="image_error_fallback", confidence=0.1)
        record.note = f"视觉模型调用失败，系统使用保守默认估算。错误类型：{exc.__class__.__name__}"
        add_user_records([record], user_id)
        return {
            "image": image_url,
            "items": [record.model_dump()],
            "raw": {
                "note": record.note,
                "health_advice": "请稍后重新识别，或根据实际食物名称和重量手动修正热量记录。",
            },
        }
    parsed = _extract_json(
        raw) or {"items": [], "note": raw, "health_advice": ""}
    records = []
    for item in parsed.get("items", []):
        record = estimate_item(
            FoodLogItem(
                food_name=item.get("food_name", "未知食物"),
                grams=float(item.get("grams", 100)),
                meal_type=meal_type,
            ),
            source="image_ai",
            confidence=item.get("confidence"),
        )
        if item.get("calories"):
            record.calories = round(float(item.get("calories")), 1)
            record.protein = round(
                float(item.get("protein", record.protein)), 1)
            record.fat = round(float(item.get("fat", record.fat)), 1)
            record.carbs = round(float(item.get("carbs", record.carbs)), 1)
        record.note = parsed.get("note", "图片识别估算，实际热量可能受份量和烹饪方式影响。")
        records.append(record)
    add_user_records(records, user_id)
    return {"image": image_url, "items": [x.model_dump() for x in records], "raw": parsed}
