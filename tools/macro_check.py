"""Попадание меню в БЖУ: среднее за день против нормы (анкета со скриншотов: м, 49, 80 кг, 170 см)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai  # noqa: E402
from nutrition import calc  # noqa: E402

P = {"sex": "m", "age": 49, "weight": 80, "height": 170, "activity": "mid", "goal": "keep"}
P |= calc(**P)


async def one():
    menu, st = await ai.week_menu(dict(P), ["Курица Ханди"])
    avg = {f: round(sum(d[f] for d in menu["days"]) / 7) for f in ("kcal", "protein", "fat", "carbs")}
    drinks = [f'{i["name"]} {i["grams"]} г' for d in menu["days"] for m in d["meals"] for i in m["items"]
              if i["kcal"] * 100 < 15 * max(i["grams"], 1)]
    eng = [i["name"] for d in menu["days"] for m in d["meals"] for i in m["items"] if i["name"].isascii()]
    print(f"{st['seconds']} с: {avg['kcal']} ккал Б{avg['protein']}/Ж{avg['fat']}/У{avg['carbs']} "
          f"(норма {P['target']} Б{P['protein']}/Ж{P['fat']}/У{P['carbs']}); напитки {drinks[:3]}; англ {eng}")


async def main():
    await asyncio.gather(one(), one())


asyncio.run(main())
