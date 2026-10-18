"""Расчёт суточной нормы калорий по формуле Миффлина — Сан-Жеора."""

# Уровни активности из анкеты (как на схеме Salebot) → коэффициент
ACTIVITY = {
    "none": ("Практически отсутствует", 1.2),
    "low": ("Низкий", 1.375),
    "mid": ("Средний", 1.55),
    "high": ("Выше среднего", 1.725),
    "max": ("Высокий", 1.9),
}

# Цель → поправка к норме
GOALS = {
    "keep": ("Поддерживать форму", 1.0),
    "lose": ("Похудеть", 0.85),
    "gain": ("Набрать вес", 1.15),
}

SEX = {"m": "Мужской", "f": "Женский"}


def bmr(sex: str, age: int, weight: float, height: float) -> float:
    """Базовый обмен (ПБМ) — калории в полном покое."""
    base = 10 * weight + 6.25 * height - 5 * age
    return base + 5 if sex == "m" else base - 161


def calc(sex: str, age: int, weight: float, height: float, activity: str, goal: str) -> dict:
    b = bmr(sex, age, weight, height)
    tdee = b * ACTIVITY[activity][1]
    target = tdee * GOALS[goal][1]
    # БЖУ на цель: белок 1,8 г/кг (2 г/кг при наборе), жиры 0,9 г/кг, остальное — углеводы
    protein = weight * (2.0 if goal == "gain" else 1.8)
    fat = weight * 0.9
    carbs = max(0.0, (target - protein * 4 - fat * 9) / 4)
    return {
        "bmr": round(b),
        "tdee": round(tdee),
        "target": round(target),
        "protein": round(protein),
        "fat": round(fat),
        "carbs": round(carbs),
    }
