"""Калорийный Бот (@Nutricio_OlegTsu_Bot).

Переписан с конструктора Salebot (схема — materials/salebot-flow-scheme.png).
Анкета → расчёт калорий (Миффлин — Сан-Жеор) → меню на неделю от ИИ (ProxyAPI)
→ поиск рецептов TheMealDB с переводом на русский и русским видео (YouTube Data API)
→ избранное с рейтингом 1–5 звёзд → дневник веса → отчёт в Excel (export.py).

Сервер: systemd-служба nutricio-bot (см. deploy/), секреты — из *_FILE.
Локально: bash ../.claude/scripts/nutricio-bot-run.sh (ключи берёт скрипт).
"""
import asyncio
import html
import logging
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import ai
import export
import mealdb
import storage
import youtube
from nutrition import ACTIVITY, GOALS, SEX, calc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(Path(__file__).with_name("bot.log"), encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("nutricio")
router = Router()

BTN_FORM = "📋 Анкета и расчёт калорий"
BTN_MENU = "🍽 Меню на неделю"
BTN_MENU_LOCKED = "🔒 Меню на неделю"
BTN_SEARCH = "🔍 Поиск рецептов"
BTN_FAVS = "⭐ Мои рецепты"
BTN_WEIGHT = "⚖️ Дневник веса"
BTN_EXCEL = "📊 Отчёт в Excel"


class Form(StatesGroup):
    sex = State()
    age = State()
    weight = State()
    height = State()
    activity = State()
    goal = State()


class Search(StatesGroup):
    query = State()


class WeightLog(StatesGroup):
    value = State()


# ---------- клавиатуры ----------

def main_kb(user_id: int) -> ReplyKeyboardMarkup:
    menu = BTN_MENU if storage.get_profile(user_id) else BTN_MENU_LOCKED
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text=BTN_FORM), KeyboardButton(text=menu)],
        [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_FAVS)],
        [KeyboardButton(text=BTN_WEIGHT), KeyboardButton(text=BTN_EXCEL)],
    ])


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def rating_kb(meal_id: str, current: int | None) -> InlineKeyboardMarkup:
    rows = [[(f"{i}⭐" if i != current else f"✅{i}", f"s:{meal_id}:{i}") for i in range(1, 6)]]
    if current:
        rows.append([("🗑 Убрать из избранного", f"d:{meal_id}")])
    return inline(rows)


def rating_text(current: int | None) -> str:
    if current:
        return f"Твоя оценка: {stars(current)} — рецепт в «{BTN_FAVS[2:]}». Можно изменить:"
    return "Оцени блюдо от 1 до 5 звёзд — оценка сохранит его в «Мои рецепты»:"


# ---------- старт ----------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    name = html.escape(m.from_user.first_name or "друг")
    await m.answer(
        f"Привет, {name}! 👋\n\n"
        "Я удобный бот, который поможет быстро определить свои индивидуальные нормы питания, "
        "упростить процесс планирования рациона и помочь с достижением целей в улучшении "
        "физической формы.\n\n"
        "Заполни анкету, посмотри на индивидуальный расчёт калорий и подбери рецепты — "
        "понравившиеся оценивай звёздами, лучшие окажутся сверху в «Мои рецепты».",
        reply_markup=main_kb(m.from_user.id),
    )


# ---------- анкета ----------

@router.message(F.text == BTN_FORM)
async def form_start(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Form.sex)
    await m.answer("Для заполнения анкеты укажите:\n\n<b>Ваш пол</b>",
                   reply_markup=inline([[("М", "sex:m"), ("Ж", "sex:f")]]))


@router.callback_query(Form.sex, F.data.startswith("sex:"))
async def form_sex(c: CallbackQuery, state: FSMContext):
    sex = c.data.split(":")[1]
    await state.update_data(sex=sex)
    await c.message.edit_text(f"Ваш пол: <b>{SEX[sex]}</b>")
    await state.set_state(Form.age)
    await c.message.answer("<b>Ваш возраст</b> (полных лет):")
    await c.answer()


def _number(text: str, lo: float, hi: float) -> float | None:
    try:
        v = float(text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    return v if lo <= v <= hi else None


@router.message(Form.age)
async def form_age(m: Message, state: FSMContext):
    v = _number(m.text, 14, 100)
    if v is None:
        return await m.answer("Введите возраст числом от 14 до 100, например: 35")
    await state.update_data(age=int(v))
    await state.set_state(Form.weight)
    await m.answer("<b>Ваш вес</b> (кг):")


@router.message(Form.weight)
async def form_weight(m: Message, state: FSMContext):
    v = _number(m.text, 30, 250)
    if v is None:
        return await m.answer("Введите вес в килограммах от 30 до 250, например: 72,5")
    await state.update_data(weight=v)
    await state.set_state(Form.height)
    await m.answer("<b>Ваш рост</b> (см):")


@router.message(Form.height)
async def form_height(m: Message, state: FSMContext):
    v = _number(m.text, 120, 230)
    if v is None:
        return await m.answer("Введите рост в сантиметрах от 120 до 230, например: 178")
    await state.update_data(height=v)
    await state.set_state(Form.activity)
    await m.answer("<b>Ваш уровень физической активности</b>",
                   reply_markup=inline([[(title, f"act:{k}")] for k, (title, _) in ACTIVITY.items()]))


@router.callback_query(Form.activity, F.data.startswith("act:"))
async def form_activity(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[1]
    await state.update_data(activity=key)
    await c.message.edit_text(f"Уровень активности: <b>{ACTIVITY[key][0]}</b>")
    await state.set_state(Form.goal)
    await c.message.answer("<b>Определите цель</b>",
                           reply_markup=inline([[(title, f"goal:{k}")] for k, (title, _) in GOALS.items()]))
    await c.answer()


@router.callback_query(Form.goal, F.data.startswith("goal:"))
async def form_goal(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[1]
    d = await state.get_data()
    await state.clear()
    await c.message.edit_text(f"Цель: <b>{GOALS[key][0]}</b>")
    r = calc(d["sex"], d["age"], d["weight"], d["height"], d["activity"], key)
    storage.save_profile(
        c.from_user.id, name=c.from_user.first_name, sex=d["sex"], age=d["age"],
        weight=d["weight"], height=d["height"], activity=d["activity"], goal=key, **r)
    storage.add_weight(c.from_user.id, d["weight"])  # вес из анкеты — первая запись дневника
    log.info("profile saved user=%s target=%s", c.from_user.id, r["target"])
    await c.message.answer("Анкета заполнена, спасибо! ✅")
    await c.message.answer(
        "Сейчас мы определим оптимальное количество энергии на день с учётом "
        "индивидуального уровня физической нагрузки…")
    await c.message.answer(
        f"Базовый обмен (формула Миффлина — Сан-Жеора): <b>{r['bmr']} ккал</b>\n"
        f"С учётом активности (×{ACTIVITY[d['activity']][1]}): <b>{r['tdee']} ккал</b>\n\n"
        f"🔥 <b>Ваша суточная норма калорий — {r['target']} ккал</b>\n"
        f"(цель «{GOALS[key][0]}»)\n\n"
        f"БЖУ на день: белки {r['protein']} г · жиры {r['fat']} г · углеводы {r['carbs']} г",
        reply_markup=main_kb(c.from_user.id))
    await c.answer()


# ---------- меню на неделю (ИИ через ProxyAPI) ----------

MEAL_ICON = {"Завтрак": "🍳", "Обед": "🍲", "Перекус": "🍎", "Ужин": "🍽"}
_busy: set[int] = set()  # кто сейчас ждёт ответа ИИ — защита от двойного нажатия


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def render_day(d: dict, target: int) -> str:
    lines = [f"📅 <b>{d['day']}</b> — <b>{fmt(d['kcal'])} ккал</b> из {fmt(target)}\n"
             f"Б {d['protein']} · Ж {d['fat']} · У {d['carbs']} г"]
    for m in d["meals"]:
        lines.append(f"\n{MEAL_ICON.get(m['meal'], '•')} <b>{html.escape(m['meal'])}</b> · {m['kcal']} ккал")
        lines += [f"• {html.escape(i['name'])} — {i['grams']} г, {i['kcal']} ккал" for i in m["items"]]
    return "\n".join(lines)


def render_summary(menu: dict, p) -> str:
    days = menu["days"]
    avg = {f: round(sum(d[f] for d in days) / len(days)) for f in ("kcal", "protein", "fat", "carbs")}
    text = (f"📊 <b>В среднем за день:</b> {fmt(avg['kcal'])} ккал (норма {fmt(p['target'])})\n"
            f"Б {avg['protein']} / Ж {avg['fat']} / У {avg['carbs']} г "
            f"(норма {p['protein']} / {p['fat']} / {p['carbs']})\n\n"
            "ℹ️ Блюда и их калорийность подобрал ИИ по справочным данным; "
            "итоги посчитал бот и подогнал размер порций под вашу норму.")
    return text + f"\n<i>Модель: {html.escape(menu.get('model', ''))}</i>"


async def send_menu(m: Message, menu: dict, p):
    for d in menu["days"]:
        await m.answer(render_day(d, p["target"]))
    await m.answer(render_summary(menu, p),
                   reply_markup=inline([[("🔄 Составить другое меню", "menu:new")]]))


class Progress:
    """Живой индикатор, пока работает ИИ: раз в ~2 с обновляет сообщение
    (render(секунды) → текст) и держит в чате статус «печатает…»."""

    def __init__(self, msg: Message, render):
        self.msg, self.render = msg, render
        self.t0 = time.monotonic()
        self._task: asyncio.Task | None = None

    async def _loop(self):
        last = ""
        while True:
            try:
                await self.msg.bot.send_chat_action(self.msg.chat.id, "typing")
                text = self.render(int(time.monotonic() - self.t0))
                if text != last:
                    await self.msg.edit_text(text)
                    last = text
            except Exception:  # «message is not modified», сетевые мелочи — не мешаем основной работе
                pass
            await asyncio.sleep(2)

    async def __aenter__(self):
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()


MENU_HINTS = ["🧮 Изучаю анкету и норму калорий…", "🥗 Подбираю блюда под ваши вкусы…",
              "🛒 Проверяю, чтобы продукты были в обычном магазине…"]


def menu_progress(state: dict):
    def render(sec: int) -> str:
        n = min(state["day"], 7)
        if n == 0:
            line = MENU_HINTS[(sec // 4) % len(MENU_HINTS)]
        else:
            line = f"{'▰' * n}{'▱' * (7 - n)}  {ai.DAYS[n - 1]} — {n} из 7 дней"
        return f"⏳ <b>Готовим Ваше меню на неделю…</b>\n\n{line}\n\n⏱ {sec} с · обычно до минуты"
    return render


async def make_menu(m: Message, user_id: int):
    p = storage.get_profile(user_id)
    if user_id in _busy:
        return await m.answer("⏳ Меню уже готовится, ещё немного…")
    _busy.add(user_id)
    state = {"day": 0}
    render = menu_progress(state)
    wait = await m.answer(render(0))
    try:
        liked = [(storage.get_recipe_ru(f["meal_id"]) or {}).get("title") or f["title"]  # по-русски, если есть
                 for f in storage.favorites(user_id) if f["rating"] >= 4][:10]
        async with Progress(wait, render):
            menu, stats = await ai.week_menu(dict(p), liked,
                                             on_day=lambda n: state.update(day=n))
        menu |= {"model": stats["model"], "target": p["target"]}
        storage.save_menu(user_id, menu)
        log.info("menu user=%s %s", user_id, stats)
    except Exception:
        log.exception("menu failed user=%s", user_id)
        return await wait.edit_text("😔 Не получилось составить меню — сервис ИИ сейчас не отвечает. "
                                    "Попробуйте через пару минут.")
    finally:
        _busy.discard(user_id)
    await wait.edit_text(f"✅ Готово Ваше меню на неделю! ({int(stats['seconds'])} с)")
    await send_menu(m, menu, p)


@router.message(F.text.in_({BTN_MENU, BTN_MENU_LOCKED}))
async def week_menu(m: Message):
    p = storage.get_profile(m.from_user.id)
    if not p:
        return await m.answer(f"🔒 Кнопка станет активной после анкеты — нажмите «{BTN_FORM}».",
                              reply_markup=main_kb(m.from_user.id))
    saved = storage.get_menu(m.from_user.id)
    if saved and saved.get("target") == p["target"]:  # анкета не менялась — показываем сохранённое
        await m.answer("Ваше последнее меню на неделю:")
        return await send_menu(m, saved, p)
    await make_menu(m, m.from_user.id)


@router.callback_query(F.data == "menu:new")
async def menu_new(c: CallbackQuery):
    await c.answer()
    await make_menu(c.message, c.from_user.id)


# ---------- поиск рецептов ----------

@router.message(F.text == BTN_SEARCH)
async def search_start(m: Message, state: FSMContext):
    await state.set_state(Search.query)
    await m.answer("Что ищем? Напишите блюдо или продукт: <i>курица, рыба, паста, суп, десерт</i> "
                   "— можно любыми словами: <i>что-нибудь с тыквой</i>")


@router.message(Search.query, F.text)
async def search_run(m: Message, state: FSMContext):
    await state.clear()
    query = m.text
    if mealdb.translate(query) == query.strip().lower() and not query.isascii():
        try:  # слова нет в словаре — переводим запрос нейросетью
            query = await ai.query_to_en(query)
        except Exception:
            log.warning("query_to_en failed: %s", query)
    try:
        found = await mealdb.search(query)
    except Exception:
        return await m.answer("Сервис рецептов сейчас не отвечает, попробуйте чуть позже.")
    if found:  # русские названия: из кэша переводов, остальные — одним запросом к ИИ
        cached = [storage.get_recipe_ru(f["id"]) for f in found]
        missing = [f for f, c in zip(found, cached) if not c]
        names = {}
        if missing:
            try:
                names = dict(zip((f["id"] for f in missing),
                                 await ai.translate_titles([f["title"] for f in missing])))
            except Exception:
                log.warning("translate_titles failed")
        for f, c in zip(found, cached):
            f["title"] = c["title"] if c else names.get(f["id"], f["title"])
    if not found:
        return await m.answer("Ничего не нашлось 🤷 Попробуйте другое слово, например «курица» или «паста».")
    await m.answer(f"Нашлось рецептов: {len(found)}. Выберите:",
                   reply_markup=inline([[(f["title"][:60], f"r:{f['id']}")] for f in found]))


def _chunks(text: str, size: int = 4000) -> list[str]:
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > size:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


async def recipe_ru(meal_id: str) -> dict | None:
    """Рецепт по-русски: перевод ИИ + КБЖУ порции + русское видео. Всё кэшируется в базе."""
    cached = storage.get_recipe_ru(meal_id)
    if cached:
        return cached
    r = await mealdb.lookup(meal_id)
    if not r:
        return None
    try:
        t, stats = await ai.translate_recipe(r)
        log.info("recipe translated meal=%s %s", meal_id, stats)
    except Exception:
        log.exception("translate failed meal=%s", meal_id)
        return r | {"steps": [r["instructions"]], "untranslated": True}  # покажем оригинал, не кэшируем
    ru = r | t | {"title_en": r["title"], "video_ru": await youtube.find_ru_video(t["title"])}
    storage.save_recipe_ru(meal_id, ru)
    return ru


async def show_recipe(c: CallbackQuery, meal_id: str):
    wait = None
    try:
        if storage.get_recipe_ru(meal_id):  # уже переведён — показываем сразу
            r = await recipe_ru(meal_id)
        else:
            def render(sec: int) -> str:
                return f"📖 Перевожу рецепт на русский и ищу видео…  ⏱ {sec} с"
            wait = await c.message.answer(render(0))
            async with Progress(wait, render):
                r = await recipe_ru(meal_id)
            await wait.delete()
    except Exception:
        if wait:
            await wait.delete()
        return await c.message.answer("Сервис рецептов сейчас не отвечает, попробуйте чуть позже.")
    if not r:
        return await c.message.answer("Рецепт не найден.")
    caption = f"<b>{html.escape(r['title'])}</b>"
    if r.get("kcal"):
        caption += (f"\n🔥 {r['kcal']} ккал на порцию · Б {r['protein']} / Ж {r['fat']} / У {r['carbs']} г"
                    f"\n🍽 Порций: {r.get('servings', '—')}")
    if r["thumb"]:
        await c.message.answer_photo(r["thumb"], caption=caption)
    else:
        await c.message.answer(caption)

    body = "🧾 <b>Ингредиенты</b>\n" + "\n".join(f"• {html.escape(i)}" for i in r["ingredients"])
    steps = r.get("steps") or []
    body += "\n\n👩‍🍳 <b>Приготовление</b>\n" + "\n".join(
        html.escape(x) if len(steps) == 1 else f"{n}. {html.escape(x)}" for n, x in enumerate(steps, 1))
    if r.get("untranslated"):
        body += "\n\n<i>Перевод сейчас недоступен — показан оригинал.</i>"
    v = r.get("video_ru")
    if v:
        body += (f'\n\n🎬 <b>Видеорецепт:</b> <a href="{html.escape(v["url"])}">'
                 f'{html.escape(v["title"])}</a> ({html.escape(v["channel"])})')
    elif r.get("youtube"):  # русского не нашли — оригинальное видео
        body += (f'\n\n🎬 <b>Видеорецепт</b> (на английском): '
                 f'<a href="{html.escape(r["youtube"])}">смотреть на YouTube</a>')
    for part in _chunks(body):
        await c.message.answer(part, disable_web_page_preview=True)

    current = storage.get_rating(c.from_user.id, meal_id)
    await c.message.answer(rating_text(current), reply_markup=rating_kb(meal_id, current))
    log.info("recipe shown user=%s meal=%s video_ru=%s", c.from_user.id, meal_id, bool(v))


@router.callback_query(F.data.startswith("r:"))
async def open_recipe(c: CallbackQuery):
    await c.answer()
    await show_recipe(c, c.data[2:])


@router.callback_query(F.data.startswith("s:"))
async def set_rating(c: CallbackQuery):
    _, meal_id, n = c.data.split(":")
    n = int(n)
    cached = storage.get_recipe_ru(meal_id)
    title = cached["title"] if cached else meal_id
    storage.rate(c.from_user.id, meal_id, title, n)
    log.info("rated user=%s meal=%s rating=%s", c.from_user.id, meal_id, n)
    await c.message.edit_text(rating_text(n), reply_markup=rating_kb(meal_id, n))
    await c.answer(f"Оценка {stars(n)} сохранена")


@router.callback_query(F.data.startswith("d:"))
async def delete_fav(c: CallbackQuery):
    meal_id = c.data[2:]
    storage.remove(c.from_user.id, meal_id)
    await c.message.edit_text(rating_text(None), reply_markup=rating_kb(meal_id, None))
    await c.answer("Убрано из избранного")


# ---------- мои рецепты ----------

@router.message(F.text == BTN_FAVS)
async def my_recipes(m: Message):
    favs = storage.favorites(m.from_user.id)
    if not favs:
        return await m.answer(f"Пока пусто. Найдите блюдо в «{BTN_SEARCH[2:]}» и поставьте ему оценку.")
    await m.answer(
        f"⭐ <b>Мои рецепты</b> ({len(favs)}) — по рейтингу, лучшие сверху:",
        reply_markup=inline([[(f"{stars(f['rating'])}  {f['title'][:40]}", f"r:{f['meal_id']}")]
                             for f in favs]))


# ---------- дневник веса и отчёт в Excel ----------

def kg(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def weight_summary(rows) -> str:
    if not rows:
        return "Записей пока нет."
    start, last = rows[0]["weight"], rows[-1]["weight"]
    lines = [f"• {r['created_at'][8:10]}.{r['created_at'][5:7]} {r['created_at'][11:]} — "
             f"<b>{kg(r['weight'])} кг</b>" for r in rows[-7:]]
    diff = round(last - start, 1)
    trend = "без изменений" if diff == 0 else f"{diff:+g} кг".replace(".", ",")
    return (f"Последние записи ({min(len(rows), 7)} из {len(rows)}):\n" + "\n".join(lines)
            + f"\n\nСтарт {kg(start)} кг → сейчас {kg(last)} кг: <b>{trend}</b>")


@router.message(F.text == BTN_WEIGHT)
async def weight_start(m: Message, state: FSMContext):
    if not storage.get_profile(m.from_user.id):
        return await m.answer(f"Сначала заполните анкету — «{BTN_FORM}».", reply_markup=main_kb(m.from_user.id))
    await state.set_state(WeightLog.value)
    await m.answer(f"⚖️ <b>Дневник веса</b>\n\n{weight_summary(storage.weights(m.from_user.id))}\n\n"
                   "Напишите сегодняшний вес в килограммах, например: <i>84,6</i>")


@router.message(F.text == BTN_EXCEL)
async def excel_report(m: Message, state: FSMContext):
    await state.clear()
    data = export.build(m.from_user.id)
    if not data:
        return await m.answer(f"Отчёт появится после анкеты — «{BTN_FORM}».", reply_markup=main_kb(m.from_user.id))
    name = f"nutricio-report-{datetime.now(storage.MSK):%Y-%m-%d}.xlsx"
    await m.answer_document(BufferedInputFile(data, filename=name), caption=(
        "📊 <b>Ваш отчёт</b>: листы «Профиль», «Меню на неделю», «Дневник веса» (с графиком) "
        "и «Мои рецепты». Открывается в Excel или Google Таблицах."))
    log.info("excel sent user=%s bytes=%s", m.from_user.id, len(data))


@router.message(WeightLog.value)
async def weight_save(m: Message, state: FSMContext):
    v = _number(m.text, 30, 250)
    if v is None:
        return await m.answer("Введите вес числом от 30 до 250, например: 84,6")
    await state.clear()
    storage.add_weight(m.from_user.id, round(v, 1))
    log.info("weight saved user=%s", m.from_user.id)
    await m.answer(f"✅ Записано: <b>{kg(round(v, 1))} кг</b>\n\n{weight_summary(storage.weights(m.from_user.id))}\n\n"
                   f"Вся история и график — в «{BTN_EXCEL}». Чтобы пересчитать норму калорий "
                   f"под новый вес, пройдите анкету заново.", reply_markup=main_kb(m.from_user.id))


@router.message()
async def fallback(m: Message):
    await m.answer("Выберите действие на кнопках ниже 👇", reply_markup=main_kb(m.from_user.id))


async def main():
    # Локально токен приходит из скрипта-обёртки (BOT_TOKEN), на сервере — из файла (BOT_TOKEN_FILE)
    token = os.environ.get("BOT_TOKEN")
    if not token and os.environ.get("BOT_TOKEN_FILE"):
        token = Path(os.environ["BOT_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("Нет токена — задайте BOT_TOKEN или BOT_TOKEN_FILE")
    storage.init()
    # Telegram доступен через системный прокси Windows (VPN-клиент) — aiohttp сам его не видит
    proxy = urllib.request.getproxies().get("https")
    session = AiohttpSession(proxy=proxy) if proxy else None
    log.info("proxy: %s", proxy or "нет")
    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
