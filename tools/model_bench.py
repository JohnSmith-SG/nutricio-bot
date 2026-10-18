"""Сравнение моделей ProxyAPI на задаче «меню на неделю» для одной и той же анкеты.

Меряет: время, токены, стоимость (по тарифам ProxyAPI), валидность JSON,
насколько сумма калорий от модели отличается от нормы (до коррекции кодом).
Результат — таблица в stdout и JSON в tools/bench-results.json.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai  # noqa: E402
from nutrition import calc  # noqa: E402

# Тарифы ProxyAPI, ₽ за 1 млн токенов (ввод, вывод) — proxyapi.ru/models, 14.10.2026
PRICES = {
    "openai/gpt-4o-mini": (39, 155),
    "openai/gpt-4.1-mini": (104, 413),
    "openai/gpt-5-mini": (65, 516),
    "deepseek/deepseek-v4-flash": (20, 40),
    "qwen/qwen3.8-flash": (20, 65),
    "deepseek/deepseek-v3.2": (36.84, 53.68),
    "deepseek/deepseek-chat-v3.1": (74.74, 231.58),
}

PROFILE = {"sex": "m", "age": 40, "weight": 85, "height": 180, "activity": "mid", "goal": "lose"}
PROFILE |= calc(**{k: PROFILE[k] for k in ("sex", "age", "weight", "height", "activity")}, goal=PROFILE["goal"])
LIKED = ["Курица терияки", "Лосось на гриле", "Греческий салат"]


async def run(model: str) -> dict:
    res = {"model": model}
    try:
        menu, st = await ai.week_menu(dict(PROFILE), LIKED, model)
    except Exception as e:
        return res | {"error": f"{type(e).__name__}: {str(e)[:150]}"}
    pin, pout = PRICES[model]
    devs = [abs(d["model_kcal"] - PROFILE["target"]) / PROFILE["target"] * 100 for d in menu["days"]]
    meals = [i for d in menu["days"] for m in d["meals"] for i in m["items"]]
    dishes = [" + ".join(i["name"] for i in m["items"]) for d in menu["days"] for m in d["meals"]]
    # правдоподобие: ккал против 4Б+9Ж+4У и плотность (ккал на 100 г) — выше 250 у обычных блюд редкость
    mism = [abs(m["kcal"] - (4 * m["protein"] + 9 * m["fat"] + 4 * m["carbs"])) / max(m["kcal"], 1) * 100 for m in meals]
    dense = [f'{m["name"]} {m["grams"]} г = {m["kcal"]} ккал' for m in meals
             if m["kcal"] * 100 / max(m["grams"], 1) > 250 and "орех" not in m["name"].lower()]
    return res | st | {
        "cost_rub": round((st["input_tokens"] * pin + st["output_tokens"] * pout) / 1e6, 2),
        "avg_deviation_pct": round(sum(devs) / 7, 1),
        "max_deviation_pct": round(max(devs), 1),
        "unique_dishes": len(set(dishes)),
        "attempts": st.get("attempts"),
        "scaled_days": [d.get("scaled") for d in menu["days"]],
        "macro_mismatch_pct": round(sum(mism) / len(mism), 1),
        "implausible": dense,
        "sample_day": menu["days"][0],
    }


async def main():
    models = sys.argv[1:] or list(PRICES)
    print(f"Анкета: норма {PROFILE['target']} ккал, Б{PROFILE['protein']}/Ж{PROFILE['fat']}/У{PROFILE['carbs']}")
    results = await asyncio.gather(*(run(m) for m in models))
    for r in results:
        if "error" in r:
            print(f"{r['model']:28} ОШИБКА {r['error']}")
        else:
            print(f"{r['model']:28} {r['seconds']:>5} с  {r['cost_rub']:>5} ₽  "
                  f"откл. ср {r['avg_deviation_pct']:>5}% макс {r['max_deviation_pct']:>5}%  "
                  f"блюд {r['unique_dishes']}/28  ккал≠БЖУ {r['macro_mismatch_pct']}%  "
                  f"неправдоп. {len(r['implausible'])}  масштаб {r['scaled_days']}  попыток {r['attempts']}  ток {r['input_tokens']}/{r['output_tokens']}")
            for x in r["implausible"]:
                print("      ⚠", x)
    out = Path(__file__).with_name(f"bench-results-{len(list(Path(__file__).parent.glob('bench-results*.json'))) + 1}.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


asyncio.run(main())
