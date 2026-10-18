"""Проверка без Telegram: перевод рецепта, поиск по-русски, вид меню в сообщениях бота."""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai  # noqa: E402
import bot  # noqa: E402
import mealdb  # noqa: E402
import youtube  # noqa: E402
from nutrition import calc  # noqa: E402

PROFILE = {"sex": "m", "age": 40, "weight": 85, "height": 180, "activity": "mid", "goal": "lose"}
PROFILE |= calc(**PROFILE)
LIKED = ["Курица терияки", "Лосось на гриле", "Греческий салат"]


def plain(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t)


async def main():
    q = await ai.query_to_en("что-нибудь с тыквой")
    found = await mealdb.search(q)
    print("Запрос «что-нибудь с тыквой» →", q, "→", len(found), "рецептов")
    print("Названия:", await ai.translate_titles([f["title"] for f in found[:5]]))
    r = await mealdb.lookup("52795")
    t, st = await ai.translate_recipe(r)
    print("\n== Перевод:", r["title"], "→", t["title"], st)
    print("КБЖУ порции:", t["kcal"], t["protein"], t["fat"], t["carbs"], "порций", t["servings"])
    print("\n".join(t["ingredients"][:6]), "\n", t["steps"][0])
    print("YouTube API включён:", youtube.enabled())
    menu, st = await ai.week_menu(dict(PROFILE), LIKED)
    print("\n== Меню:", st)
    print(plain(bot.render_day(menu["days"][0], PROFILE["target"])))
    print(plain(bot.render_summary(menu | {"model": st["model"]}, PROFILE)))


if __name__ == "__main__":
    asyncio.run(main())
