"""Нейросеть через ProxyAPI (библиотека openai): меню на неделю и перевод рецептов.

Модель — переменная NUTRICIO_MODEL (по умолчанию MODEL ниже), ключ — PROXYAPI_KEY
или файл PROXYAPI_KEY_FILE (на сервере). Калории считает не модель, а код:
модель даёт граммы и КБЖУ блюд, бот сам складывает итоги и подгоняет порции под норму.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from openai import AsyncOpenAI

BASE_URL = "https://openai.api.proxyapi.ru/v1"  # единый адрес: модели любых поставщиков
MODEL = "openai/gpt-4o-mini"  # выбрана по сравнению моделей — tools/model_bench.py
TOLERANCE = 0.05  # день отклоняется от нормы больше чем на 5% → масштабируем порции

log = logging.getLogger(__name__)
_client: AsyncOpenAI | None = None


def model() -> str:
    return os.environ.get("NUTRICIO_MODEL") or MODEL


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.environ.get("PROXYAPI_KEY")
        if not key and os.environ.get("PROXYAPI_KEY_FILE"):
            key = Path(os.environ["PROXYAPI_KEY_FILE"]).read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("Нет ключа ProxyAPI — задайте PROXYAPI_KEY или PROXYAPI_KEY_FILE")
        _client = AsyncOpenAI(api_key=key, base_url=BASE_URL, timeout=150, max_retries=1)
    return _client


async def ask_json(system: str, user: str, model_name: str | None = None,
                   temperature: float = 0.7,
                   on_text: Callable[[str], None] | None = None) -> tuple[dict, dict]:
    """Запрос к модели с ответом строго в JSON. Возвращает (данные, статистика).
    on_text — ответ приходит по кусочкам (streaming), функция получает накопленный текст:
    так бот показывает пользователю настоящий ход работы."""
    t0 = time.monotonic()
    m = model_name or model()
    extra = {} if "gpt-5" in m else {"temperature": temperature}  # рассуждающие модели её не принимают
    stream = await client().chat.completions.create(
        model=m,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        stream=True, stream_options={"include_usage": True},
        **extra,
    )
    text, usage, used_model = "", None, m
    async for chunk in stream:
        used_model = chunk.model or used_model
        usage = chunk.usage or usage
        if chunk.choices and chunk.choices[0].delta.content:
            text += chunk.choices[0].delta.content
            if on_text:
                on_text(text)
    stats = {"model": used_model, "seconds": round(time.monotonic() - t0, 1),
             "input_tokens": usage.prompt_tokens if usage else 0,
             "output_tokens": usage.completion_tokens if usage else 0}
    log.info("AI %s", stats)
    return json.loads(text), stats


# ---------- меню на неделю ----------

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

MEALS = [("Завтрак", 0.25), ("Обед", 0.35), ("Перекус", 0.15), ("Ужин", 0.25)]

MENU_SYSTEM = """Ты — нутрициолог. Составляешь меню на неделю на русском языке из простых
блюд, продукты для которых есть в обычном российском супермаркете. Блюда не повторяются
чаще двух раз за неделю.
Каждый приём пищи — 1–4 позиции: основное блюдо, гарнир, хлеб, фрукт, напиток и т. п.
Набирай калорийность приёма пищи до цели количеством позиций и размером порций,
а НЕ завышением цифр. Граммы — вес готовой порции. Калорийность и БЖУ каждой позиции —
строго по справочным данным для указанного веса (ккал ≈ 4×белки + 9×жиры + 4×углеводы).
Отвечай только JSON по схеме:
{"days":[{"day":"Понедельник","meals":[{"meal":"Завтрак","items":[{"name":"Овсянка на молоке",
"grams":250,"kcal":280,"protein":10,"fat":8,"carbs":42}, …]}, …]}, …]}
Все названия — по-русски (иностранные блюда тоже: «Курица Ханди», а не «Chicken Handi»).
Соблюдай БЖУ дня, а не только калории: основа рациона — крупы, картофель, хлеб, макароны,
фрукты (углеводы); белок — из мяса, рыбы, творога, яиц без перебора; жирное (орехи, масло,
жирные соусы) — понемногу. Все числа — целые."""

SEX_RU = {"m": "мужчина", "f": "женщина"}
GOAL_RU = {"keep": "поддерживать форму", "lose": "похудеть", "gain": "набрать вес"}
NUM = ("grams", "kcal", "protein", "fat", "carbs")
SUM = ("kcal", "protein", "fat", "carbs")


def menu_prompt(profile: dict, liked: list[str]) -> str:
    p = profile
    targets = ", ".join(f"{name} ~{round(p['target'] * k)} ккал" for name, k in MEALS)
    text = (f"Человек: {SEX_RU[p['sex']]}, {p['age']} лет, вес {p['weight']} кг, рост {p['height']} см.\n"
            f"Цель: {GOAL_RU[p['goal']]}.\n"
            f"Норма в день: {p['target']} ккал, белки {p['protein']} г, жиры {p['fat']} г, "
            f"углеводы {p['carbs']} г.\nЦели по приёмам пищи: {targets}.")
    if liked:
        text += ("\nЕму нравятся блюда (оценил на 4–5 звёзд), включи 2–3 из них или похожие: "
                 + "; ".join(liked))
    return text


def balance(menu: dict, target: int) -> dict:
    """Код сам считает итоги приёмов пищи и дня. Если день далеко от нормы —
    пропорционально меняем порции (граммы и КБЖУ), чтобы итог совпал с нормой."""
    for d in menu["days"]:
        items = [i for m in d["meals"] for i in m["items"]]
        total = sum(i["kcal"] for i in items) or 1
        d["model_kcal"] = total
        if abs(total - target) / target > TOLERANCE:
            k = target / total
            for i in items:
                if i["kcal"] * 100 < 15 * max(i["grams"], 1):  # чай, кофе, вода — порцию не трогаем
                    continue
                step = 10 if i["grams"] * k >= 50 else 5  # «приличные» граммы: 260 г, а не 263
                grams = max(step, round(i["grams"] * k / step) * step)
                kk = grams / i["grams"] if i["grams"] else k  # КБЖУ — пропорционально новым граммам
                i["grams"] = grams
                for f in SUM:
                    i[f] = round(i[f] * kk)
            d["scaled"] = round(k, 2)
        for m in d["meals"]:
            for f in SUM:
                m[f] = sum(i[f] for i in m["items"])
        for f in SUM:
            d[f] = sum(m[f] for m in d["meals"])
    return menu


def validate_menu(data: dict) -> dict:
    days = data.get("days")
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError(f"ожидалось 7 дней, пришло {len(days) if isinstance(days, list) else days!r}")
    for d, name in zip(days, DAYS):
        d["day"] = name
        for m in d["meals"]:
            if not m.get("items"):
                raise ValueError(f"пустой приём пищи: {name} {m.get('meal')}")
            for i in m["items"]:
                i["name"] = str(i.get("name") or "—")
                for f in ("grams", "protein", "fat", "carbs"):
                    i[f] = int(round(float(i.get(f) or 0)))
                # модель иногда пропускает калории у позиции — считаем по БЖУ сами
                kcal = i.get("kcal")
                i["kcal"] = int(round(float(kcal))) if kcal not in (None, "") else \
                    4 * i["protein"] + 9 * i["fat"] + 4 * i["carbs"]
    return data


async def week_menu(profile: dict, liked: list[str], model_name: str | None = None,
                    on_day: Callable[[int], None] | None = None) -> tuple[dict, dict]:
    """on_day(n) — модель начала писать n-й день (по мере поступления ответа)."""
    def watch(text: str):
        if on_day:
            on_day(text.count('"day"'))

    for attempt in (1, 2):  # модель изредка нарушает схему JSON — одна повторная попытка
        data, stats = await ask_json(MENU_SYSTEM, menu_prompt(profile, liked), model_name, on_text=watch)
        try:
            return balance(validate_menu(data), profile["target"]), stats | {"attempts": attempt}
        except (ValueError, KeyError, TypeError) as e:
            log.warning("меню не по схеме (попытка %s): %r", attempt, e)
            if attempt == 2:
                raise


# ---------- перевод рецепта ----------

RECIPE_SYSTEM = """Ты — кулинарный редактор. Переведи рецепт на русский язык.
Меры переведи в привычные: cup → стакан (~240 мл), tbsp → ст. л., tsp → ч. л.,
oz → граммы, lb → граммы, °F → °C. Шаги приготовления — нумерованный список,
коротко и понятно. Оцени число порций и калорийность одной порции по справочным данным.
Отвечай только JSON:
{"title":"…","ingredients":["Курица — 500 г", …],"steps":["…", …],
"servings":4,"kcal":450,"protein":30,"fat":18,"carbs":40}
Все числа — целые, КБЖУ — на одну порцию."""


async def translate_recipe(r: dict) -> tuple[dict, dict]:
    src = (f"Название: {r['title']}\nКатегория: {r['category']}, кухня: {r['area']}\n"
           "Ингредиенты:\n" + "\n".join(r["ingredients"]) + f"\n\nПриготовление:\n{r['instructions']}")
    return await ask_json(RECIPE_SYSTEM, src, temperature=0.2)


# ---------- мелкие переводы для поиска ----------

async def translate_titles(titles: list[str]) -> list[str]:
    """Названия блюд из списка результатов → по-русски (один короткий запрос)."""
    data, _ = await ask_json(
        'Переведи названия блюд на русский, кратко, как в меню ресторана. '
        'Ответ — JSON {"titles": [...]} в том же порядке и того же размера.',
        "\n".join(titles), temperature=0)
    out = data.get("titles") or []
    return out if len(out) == len(titles) else titles


async def query_to_en(query: str) -> str:
    """Запрос пользователя → одно-два английских слова для поиска в TheMealDB."""
    data, _ = await ask_json(
        'Пользователь ищет рецепт. Дай 1–2 английских слова для поиска в базе рецептов '
        '(название блюда, продукт или категория). Ответ — JSON {"q": "..."}',
        query, temperature=0)
    return (data.get("q") or query).strip()
