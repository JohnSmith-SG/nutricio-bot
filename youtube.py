"""Поиск русскоязычного видеорецепта через официальный YouTube Data API v3.

Ключ — YOUTUBE_API_KEY или файл YOUTUBE_API_KEY_FILE (на сервере).
Квота бесплатная: 10 000 единиц в сутки, один поиск = 100 единиц → ~100 поисков в день.
Поэтому результат по каждому рецепту кэшируется в базе (storage), и повторно не ищем.
"""
import html
import logging
import os
from pathlib import Path

import aiohttp

API = "https://www.googleapis.com/youtube/v3/search"
log = logging.getLogger(__name__)


def _key() -> str | None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key and os.environ.get("YOUTUBE_API_KEY_FILE"):
        f = Path(os.environ["YOUTUBE_API_KEY_FILE"])
        key = f.read_text(encoding="utf-8").strip() if f.exists() else None
    return key or None


def enabled() -> bool:
    return _key() is not None


async def find_ru_video(title_ru: str) -> dict | None:
    """Первое подходящее русскоязычное видео по запросу «<название> рецепт».
    Возвращает {"url", "title", "channel"} или None."""
    key = _key()
    if not key:
        return None
    params = {
        "part": "snippet", "type": "video", "maxResults": 5,
        "q": f"{title_ru} рецепт", "relevanceLanguage": "ru", "regionCode": "RU",
        "videoEmbeddable": "true", "safeSearch": "strict", "key": key,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(API, params=params) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    log.warning("YouTube API %s: %s", r.status,
                                (data.get("error") or {}).get("message", "")[:150])
                    return None
    except Exception:
        log.warning("YouTube API недоступен")
        return None
    for it in data.get("items", []):
        vid = it.get("id", {}).get("videoId")
        if vid:
            sn = it["snippet"]
            return {"url": f"https://www.youtube.com/watch?v={vid}",
                    "title": html.unescape(sn.get("title", "")),  # YouTube отдаёт &quot; и т. п.
                    "channel": html.unescape(sn.get("channelTitle", ""))}
    return None
