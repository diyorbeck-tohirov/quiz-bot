import asyncio
import html
import json
import logging
import os
import random
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

# --- SOZLAMALAR ---
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("BOT_TOKEN", "").strip()

DB_PATH = BASE_DIR / "quiz.db"
QUESTIONS_PATH = BASE_DIR / "savolar.json"
TZ = timezone(timedelta(hours=5))  # Toshkent (UTC+5)
RETRY_DELAY = 5  # polling yiqilsa, necha soniyadan keyin qayta uriniladi

BTN_START = "🚀 Testni boshlash"
BTN_RESULTS = "📊 Natijalarim"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")],
)

dp = Dispatcher(storage=MemoryStorage())

# Har bir foydalanuvchi uchun alohida qulf (tez-tez bosishdagi poygani oldini oladi)
user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# --- MA'LUMOTLAR BAZASI ---
def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                score TEXT,
                percentage REAL,
                grade TEXT,
                date TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_results_user ON results (user_id)")


def _save_result(user_id: int, score: str, percentage: float, grade: str) -> None:
    date_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            "INSERT INTO results (user_id, score, percentage, grade, date) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, score, percentage, grade, date_str),
        )


def _get_results(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return conn.execute(
            "SELECT score, percentage, grade, date FROM results "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (user_id,),
        ).fetchall()


async def save_result(user_id: int, score: str, percentage: float, grade: str) -> None:
    await asyncio.to_thread(_save_result, user_id, score, percentage, grade)


async def get_results(user_id: int):
    return await asyncio.to_thread(_get_results, user_id)


# --- SAVOLLARNI YUKLASH ---
def load_questions() -> list[dict]:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    questions = []
    for i, q in enumerate(raw):
        try:
            question = str(q["question"])
            options = [str(o) for o in q["options"]]
            answer = str(q["answer"])
        except (KeyError, TypeError):
            logging.warning("Noto'g'ri formatdagi savol o'tkazib yuborildi: #%s", i)
            continue
        if len(options) < 2 or answer not in options:
            logging.warning("Savol #%s: variantlar yetarli emas yoki javob ular orasida yo'q", i)
            continue
        questions.append({"id": q.get("id", i), "question": question, "options": options, "answer": answer})
    return questions


# --- FSM ---
class QuizStates(StatesGroup):
    quiz_running = State()


# --- YORDAMCHILAR ---
def start_keyboard() -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_START)
    builder.button(text=BTN_RESULTS)
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def grade_for(percentage: float) -> str:
    if percentage >= 90:
        return "A'lo (5)"
    if percentage >= 70:
        return "Yaxshi (4)"
    if percentage >= 50:
        return "Qoniqarli (3)"
    return "Yomon (2)"


# --- BUYRUQLAR ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    name = html.escape(message.from_user.full_name)
    await message.answer(
        f"Xush kelibsiz, {name}!\n"
        "Testni boshlash uchun quyidagi tugmani bosing.\n"
        "Oxirgi 5 ta natijangizni ko'rish uchun /natijalarim buyrug'idan foydalaning.\n"
        "Testni to'xtatish uchun /stop.",
        reply_markup=start_keyboard(),
    )


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Hozir faol test yo'q.", reply_markup=start_keyboard())
        return
    await state.clear()
    await message.answer("Test to'xtatildi.", reply_markup=start_keyboard())


@dp.message(Command("natijalarim"))
@dp.message(F.text == BTN_RESULTS)
async def show_results(message: types.Message):
    results = await get_results(message.from_user.id)
    if not results:
        await message.answer("Siz hali test topshirmagansiz.")
        return

    text = "📊 <b>Sizning oxirgi 5 ta urinishingiz:</b>\n\n"
    for idx, (score, pct, grade, date) in enumerate(results, 1):
        text += (
            f"{idx}. 📅 {html.escape(date)}\n"
            f"   Natija: <code>{html.escape(score)}</code> ({pct:.1f}%) "
            f"- Baho: <i>{html.escape(grade)}</i>\n\n"
        )
    await message.answer(text)


# --- QUIZ LOGIKASI ---
async def finish_quiz(message: types.Message, user_id: int, state: FSMContext):
    data = await state.get_data()
    correct = data["correct_answers"]
    total = len(data["questions"])
    percentage = correct / total * 100 if total else 0.0
    grade = grade_for(percentage)
    score_str = f"{correct}/{total}"

    try:
        await save_result(user_id, score_str, percentage, grade)
    except sqlite3.Error:
        logging.exception("Natijani saqlab bo'lmadi")

    await state.clear()
    await message.answer(
        "🏁 <b>Test yakunlandi!</b>\n\n"
        f"📈 Natija: <code>{score_str}</code>\n"
        f"💯 Foiz: <code>{percentage:.1f}%</code>\n"
        f"🎖 Baho: <i>{html.escape(grade)}</i>",
        reply_markup=start_keyboard(),
    )


async def send_question(message: types.Message, user_id: int, state: FSMContext):
    data = await state.get_data()
    questions = data["questions"]
    idx = data["current_index"]

    if idx >= len(questions):
        await finish_quiz(message, user_id, state)
        return

    q = questions[idx]
    options = list(q["options"])
    random.shuffle(options)
    # callback_data'da faqat indekslar yuboriladi (64 bayt limitdan qochish uchun)
    await state.update_data(current_options=options)

    builder = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        builder.button(text=opt, callback_data=f"ans:{idx}:{i}")
    builder.adjust(1)

    await message.answer(
        f"❓ <b>{idx + 1}-savol:</b>\n{html.escape(q['question'])}",
        reply_markup=builder.as_markup(),
    )


@dp.message(F.text == BTN_START, StateFilter(None))
async def start_quiz(message: types.Message, state: FSMContext):
    try:
        questions = await asyncio.to_thread(load_questions)
    except (OSError, json.JSONDecodeError):
        logging.exception("Savollar fayli o'qilmadi: %s", QUESTIONS_PATH)
        await message.answer("Savollar fayli topilmadi yoki buzilgan. Admin bilan bog'laning.")
        return

    if not questions:
        await message.answer("Hozircha savollar mavjud emas.")
        return

    random.shuffle(questions)
    await state.set_state(QuizStates.quiz_running)
    await state.update_data(questions=questions, current_index=0, correct_answers=0, current_options=[])
    await message.answer("Test boshlandi! Omad yor bo'lsin.", reply_markup=types.ReplyKeyboardRemove())
    await send_question(message, message.from_user.id, state)


@dp.message(F.text == BTN_START, StateFilter(QuizStates.quiz_running))
async def quiz_already_running(message: types.Message):
    await message.answer("Test allaqachon davom etyapti. To'xtatish uchun /stop.")


# --- JAVOBLAR ---
@dp.callback_query(F.data.startswith("ans:"))
async def handle_answer(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    msg = callback.message
    if not isinstance(msg, types.Message):
        await callback.answer("Xabar endi mavjud emas.", show_alert=True)
        return

    async with user_locks[user_id]:
        if await state.get_state() != QuizStates.quiz_running.state:
            await callback.answer("Bu test faol emas. Iltimos, yangisini boshlang.", show_alert=True)
            return

        try:
            _, q_idx_str, opt_idx_str = callback.data.split(":")
            q_idx, opt_idx = int(q_idx_str), int(opt_idx_str)
        except ValueError:
            await callback.answer("Noto'g'ri so'rov.", show_alert=True)
            return

        data = await state.get_data()
        # Eski yoki allaqachon javob berilgan savol tugmasi
        if q_idx != data["current_index"]:
            await callback.answer("Bu savolga allaqachon javob berilgan.", show_alert=True)
            return

        options = data["current_options"]
        if not 0 <= opt_idx < len(options):
            await callback.answer("Noto'g'ri variant.", show_alert=True)
            return

        q = data["questions"][q_idx]
        is_correct = options[opt_idx] == q["answer"]

        # Avval holatni yangilaymiz, keyin UI
        await state.update_data(
            current_index=q_idx + 1,
            correct_answers=data["correct_answers"] + int(is_correct),
        )

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            logging.debug("Tugmalarni o'chirib bo'lmadi", exc_info=True)

        if is_correct:
            feedback = "✅ To'g'ri!"
        else:
            feedback = f"❌ Noto'g'ri!\nTo'g'ri javob: {html.escape(q['answer'])}"

        await callback.answer()
        await msg.reply(feedback)
        await send_question(msg, user_id, state)


# --- UMUMIY XATO USHLOVCHI ---
@dp.error()
async def on_error(event: ErrorEvent) -> bool:
    logging.error("Handlerda xatolik: %s", event.exception, exc_info=event.exception)
    update = event.update
    try:
        if update.callback_query:
            await update.callback_query.answer("Xatolik yuz berdi. Qayta urinib ko'ring.", show_alert=True)
        elif update.message:
            await update.message.answer("Xatolik yuz berdi. Qayta urinib ko'ring.")
    except Exception:
        logging.debug("Xato haqida foydalanuvchiga xabar berib bo'lmadi", exc_info=True)
    return True  # xato qayta ko'tarilmaydi, bot ishlashda davom etadi


# --- ASOSIY FUNKSIYA ---
async def main():
    if not TOKEN or TOKEN == "bu_yerga_bot_tokeningizni_yozing":
        raise SystemExit("BOT_TOKEN topilmadi. .env faylni tekshiring (namuna: .env.example).")

    init_db()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        while True:
            try:
                logging.info("Bot ishga tushdi...")
                await dp.start_polling(bot)
                break  # to'xtatish signali (Ctrl+C) bilan toza chiqildi
            except TelegramUnauthorizedError:
                raise SystemExit("Token noto'g'ri. BotFather'dan olingan tokenni .env ga qayta yozing.")
            except Exception:
                logging.exception("Polling to'xtadi, %s soniyadan keyin qayta uriniladi", RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot to'xtatildi.")