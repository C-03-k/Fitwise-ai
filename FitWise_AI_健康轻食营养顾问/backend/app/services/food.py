import csv
import uuid
from pathlib import Path
from typing import List

from app.config import DATA_DIR, RECORDS_FILE
from app.schemas import FoodLogItem, MealRecord
from app.services.storage import now_text, read_json, write_json


FOOD_DB = DATA_DIR / "food_database" / "common_foods.csv"


def load_foods():
    foods = []
    with FOOD_DB.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            foods.append(row)
    return foods


def find_food(food_name: str):
    foods = load_foods()
    for row in foods:
        if food_name in row["food_name"] or row["food_name"] in food_name:
            return row
    return None


def estimate_item(item: FoodLogItem, source: str = "manual", confidence=None) -> MealRecord:
    row = find_food(item.food_name)
    grams = float(item.grams)
    if row:
        factor = grams / 100
        calories = float(row["calories_per_100g"]) * factor
        protein = float(row["protein_per_100g"]) * factor
        fat = float(row["fat_per_100g"]) * factor
        carbs = float(row["carbs_per_100g"]) * factor
    else:
        calories = grams * 1.2
        protein = grams * 0.05
        fat = grams * 0.03
        carbs = grams * 0.15

    return MealRecord(
        id=str(uuid.uuid4()),
        created_at=now_text(),
        meal_type=item.meal_type,
        source=source,
        food_name=item.food_name,
        grams=grams,
        calories=round(calories, 1),
        protein=round(protein, 1),
        fat=round(fat, 1),
        carbs=round(carbs, 1),
        confidence=confidence,
    )


def add_records(records: List[MealRecord]):
    return add_user_records(records)


def add_user_records(records: List[MealRecord], user_id: str = "default_user"):
    data = read_json(RECORDS_FILE, [])
    items = []
    for record in records:
        record.user_id = user_id
        item = record.model_dump()
        items.append(item)
    data.extend(items)
    write_json(RECORDS_FILE, data)
    return data


def list_records(user_id: str = "default_user"):
    data = read_json(RECORDS_FILE, [])
    return [x for x in data if x.get("user_id", "default_user") == user_id]


def daily_summary(user_id: str = "default_user"):
    records = list_records(user_id)
    today = now_text()[:10]  # "年-月-日"
    today_records = [r for r in records if r.get("created_at", "")[
        :10] == today]
    total = {
        "calories": round(sum(float(x.get("calories", 0)) for x in today_records), 1),
        "protein": round(sum(float(x.get("protein", 0)) for x in today_records), 1),
        "fat": round(sum(float(x.get("fat", 0)) for x in today_records), 1),
        "carbs": round(sum(float(x.get("carbs", 0)) for x in today_records), 1),
        "record_count": len(today_records),
    }
    return total


def clear_records(user_id: str = "default_user"):
    data = read_json(RECORDS_FILE, [])
    kept = [x for x in data if x.get("user_id", "default_user") != user_id]
    removed = len(data) - len(kept)
    write_json(RECORDS_FILE, kept)
    return removed
