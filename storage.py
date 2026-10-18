"""SQLite: анкеты пользователей (замена CRM из схемы), избранные рецепты с рейтингом,
дневник веса, кэш ИИ — русские переводы рецептов и последнее меню на неделю."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).with_name("nutricio.db")
MSK = timezone(timedelta(hours=3))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY, name TEXT, sex TEXT, age INTEGER,
            weight REAL, height REAL, activity TEXT, goal TEXT,
            bmr INTEGER, tdee INTEGER, target INTEGER,
            protein INTEGER, fat INTEGER, carbs INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER, meal_id TEXT, title TEXT, rating INTEGER,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, meal_id));
        CREATE TABLE IF NOT EXISTS recipes_ru (
            meal_id TEXT PRIMARY KEY, data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS menus (
            user_id INTEGER PRIMARY KEY, data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, weight REAL,
            created_at TEXT);
        CREATE INDEX IF NOT EXISTS weights_user ON weights(user_id, created_at);
        """)


# ---------- дневник веса (время — московское, как его видит пользователь) ----------

def add_weight(user_id: int, weight: float) -> None:
    with _conn() as c:
        c.execute("INSERT INTO weights (user_id, weight, created_at) VALUES (?, ?, ?)",
                  (user_id, weight, datetime.now(MSK).strftime("%Y-%m-%d %H:%M")))


def weights(user_id: int) -> list[sqlite3.Row]:
    """Все записи по порядку: первая — самая старая."""
    with _conn() as c:
        return c.execute("SELECT * FROM weights WHERE user_id = ? ORDER BY created_at, id",
                         (user_id,)).fetchall()


# ---------- кэш ИИ: переводы рецептов и меню (чтобы не платить дважды) ----------

def get_recipe_ru(meal_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT data FROM recipes_ru WHERE meal_id = ?", (meal_id,)).fetchone()
        return json.loads(r["data"]) if r else None


def save_recipe_ru(meal_id: str, data: dict) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO recipes_ru (meal_id, data) VALUES (?, ?)",
                  (meal_id, json.dumps(data, ensure_ascii=False)))


def get_menu(user_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT data FROM menus WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(r["data"]) if r else None


def save_menu(user_id: int, data: dict) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO menus (user_id, data, created_at) "
                  "VALUES (?, ?, CURRENT_TIMESTAMP)", (user_id, json.dumps(data, ensure_ascii=False)))


def save_profile(user_id: int, **p) -> None:
    cols = ", ".join(p)
    with _conn() as c:
        c.execute(
            f"INSERT OR REPLACE INTO profiles (user_id, {cols}) VALUES (?, {', '.join('?' * len(p))})",
            (user_id, *p.values()),
        )


def get_profile(user_id: int) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()


def rate(user_id: int, meal_id: str, title: str, rating: int) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO favorites (user_id, meal_id, title, rating) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, meal_id) DO UPDATE SET rating = excluded.rating""",
            (user_id, meal_id, title, rating),
        )


def get_rating(user_id: int, meal_id: str) -> int | None:
    with _conn() as c:
        r = c.execute(
            "SELECT rating FROM favorites WHERE user_id = ? AND meal_id = ?", (user_id, meal_id)
        ).fetchone()
        return r["rating"] if r else None


def favorites(user_id: int) -> list[sqlite3.Row]:
    """Избранное по убыванию рейтинга: 5 звёзд — сверху."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM favorites WHERE user_id = ? ORDER BY rating DESC, added_at DESC",
            (user_id,),
        ).fetchall()


def remove(user_id: int, meal_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM favorites WHERE user_id = ? AND meal_id = ?", (user_id, meal_id))
