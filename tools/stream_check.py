"""Проверка streaming: в какие секунды модель начинает писать каждый день меню."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai  # noqa: E402
from tools.smoke import LIKED, PROFILE  # noqa: E402

t0 = time.monotonic()
seen = [0]


def on_day(n: int):
    if n > seen[0]:
        seen[0] = n
        print(f"{time.monotonic() - t0:5.1f} с — день {n}: {ai.DAYS[min(n, 7) - 1]}")


menu, st = asyncio.run(ai.week_menu(dict(PROFILE), LIKED, on_day=on_day))
print("готово:", st)
