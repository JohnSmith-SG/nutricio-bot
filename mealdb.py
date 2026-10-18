"""Клиент TheMealDB (бесплатный тестовый ключ "1")."""
import logging
import re

import aiohttp

API = "https://www.themealdb.com/api/json/v1/1"
log = logging.getLogger(__name__)

# TheMealDB ищет только по-английски — переводим частые русские запросы
RU_EN = {
    "курица": "chicken", "курицу": "chicken", "куриная": "chicken", "курятина": "chicken",
    "индейка": "turkey", "утка": "duck", "говядина": "beef", "свинина": "pork",
    "баранина": "lamb", "рыба": "seafood", "морепродукты": "seafood", "лосось": "salmon",
    "тунец": "tuna", "креветки": "prawns", "паста": "pasta", "макароны": "pasta",
    "лапша": "noodles", "рис": "rice", "суп": "soup", "салат": "salad", "пирог": "pie",
    "торт": "cake", "десерт": "dessert", "завтрак": "breakfast", "яйца": "egg",
    "яйцо": "egg", "сыр": "cheese", "грибы": "mushroom", "картофель": "potato",
    "картошка": "potato", "тыква": "pumpkin", "шоколад": "chocolate", "блины": "pancakes",
    "карри": "curry", "бургер": "burger", "пицца": "pizza", "овощи": "vegetarian",
    "вегетарианское": "vegetarian", "веган": "vegan", "гарнир": "side",
}


_video_ok: dict[str, bool] = {}
_PLAYABLE = re.compile(r'"playabilityStatus":\{"status":"(\w+)"')


async def video_available(url: str) -> bool:
    """Ссылки в TheMealDB старые: часть видео стали приватными или удалены.
    Смотрим статус на странице ролика (oEmbed врёт для приватных). Результат кэшируем."""
    if not url:
        return False
    if url in _video_ok:
        return _video_ok[url]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, params={"hl": "en"},
                             headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}) as r:
                m = _PLAYABLE.search(await r.text())
                ok = bool(m) and m.group(1) == "OK"
    except Exception:
        log.warning("YouTube check failed: %s", url)
        return False  # не кэшируем — проверим в следующий раз
    _video_ok[url] = ok
    log.info("video %s available=%s", url, ok)
    return ok


def translate(query: str) -> str:
    q = query.strip().lower()
    return RU_EN.get(q, q)


async def _get(path: str, **params) -> list:
    # TheMealDB иногда подвисает — одна повторная попытка
    for attempt in (1, 2):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(f"{API}/{path}", params=params) as r:
                    r.raise_for_status()
                    data = await r.json(content_type=None)
                    return (data or {}).get("meals") or []
        except Exception:
            log.warning("TheMealDB %s %s: попытка %s не удалась", path, params, attempt)
            if attempt == 2:
                raise


async def search(query: str, limit: int = 10) -> list[dict]:
    """Ищет по названию, добирает по категории и ингредиенту до limit.
    Возвращает [{id, title}]."""
    q = translate(query)
    found: dict[str, str] = {}
    for path, params in (("search.php", {"s": q}),
                         ("filter.php", {"c": q.capitalize()}),
                         ("filter.php", {"i": q.replace(" ", "_")})):
        for m in await _get(path, **params):
            found.setdefault(m["idMeal"], m["strMeal"])
        if len(found) >= limit:
            break
    return [{"id": i, "title": t} for i, t in list(found.items())[:limit]]


async def lookup(meal_id: str) -> dict | None:
    meals = await _get("lookup.php", i=meal_id)
    if not meals:
        return None
    m = meals[0]
    ingredients = []
    for i in range(1, 21):
        name = (m.get(f"strIngredient{i}") or "").strip()
        measure = (m.get(f"strMeasure{i}") or "").strip()
        if name:
            ingredients.append(f"{name} — {measure}" if measure else name)
    return {
        "id": m["idMeal"],
        "title": m["strMeal"],
        "category": m.get("strCategory") or "",
        "area": m.get("strArea") or "",
        "thumb": m.get("strMealThumb") or "",
        "instructions": (m.get("strInstructions") or "").strip(),
        "ingredients": ingredients,
        "youtube": yt if await video_available(yt := (m.get("strYoutube") or "").strip()) else "",
    }
