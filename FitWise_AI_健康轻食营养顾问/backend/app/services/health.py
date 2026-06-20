from app.schemas import HealthProfile


ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}


def calculate_profile(profile: HealthProfile):
    height_m = profile.height_cm / 100
    bmi = profile.weight_kg / (height_m * height_m)

    if profile.gender == "male":
        bmr = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age + 5
    else:
        bmr = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age - 161

    factor = ACTIVITY_FACTORS.get(profile.activity_level, 1.375)
    tdee = bmr * factor
    deficit = 400 if profile.goal == "fat_loss" else 0
    target_calories = max(1200 if profile.gender ==
                          "female" else 1500, tdee - deficit)
    protein_min = profile.weight_kg * 1.2
    protein_max = profile.weight_kg * 1.8

    if bmi < 18.5:
        bmi_status = "偏瘦"
    elif bmi < 24:
        bmi_status = "正常"
    elif bmi < 28:
        bmi_status = "超重"
    else:
        bmi_status = "肥胖"

    return {
        "bmi": round(bmi, 1),
        "bmi_status": bmi_status,
        "bmr": round(bmr),
        "tdee": round(tdee),
        "target_calories": round(target_calories),
        "protein_range": f"{round(protein_min)}-{round(protein_max)}g",
        "calorie_advice": "建议采用温和热量缺口，避免极端节食。特殊疾病、孕期或进食障碍风险请咨询医生或注册营养师。",
    }
