import os
import asyncio
from datetime import date
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# --- Загружаем токены из секретного файла .env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

# --- Настройка доступа к Google Sheets ---
SERVICE_ACCOUNT_FILE = "gsmbotproject-97531b75e9c3.json"  # ИМЯ вашего JSON-файла
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
client = gspread.authorize(creds)
# Предполагаем, что данные будем записывать в первый лист ('Sheet1')
worksheet = client.open_by_key(SHEET_ID).sheet1

# --- Определяем состояния диалога (FSM) ---
class FuelForm(StatesGroup):
    operation = State()      # Приход или расход
    fuel_type = State()      # Тип ГСМ (наименование)
    density = State()        # Плотность (факт)
    liters = State()         # Литры (факт)
    counterparty = State()   # Откуда/Куда
    level = State()          # Уровень до/после
    comment = State()        # Комментарий

# --- Настройка бота и диспетчера ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Клавиатуры с кнопками ---
def operation_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Приход"), KeyboardButton(text="Расход")]],
        resize_keyboard=True,
    )

def fuel_kb() -> ReplyKeyboardMarkup:
    # Сюда можно добавить другие виды ГСМ — просто допиши кнопки в список
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="ДТ")]],
        resize_keyboard=True,
    )

def skip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
    )

# --- Старт: показываем кнопки выбора операции ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот для учёта ГСМ на нефтебазе.\n"
        "Выберите операцию:",
        reply_markup=operation_kb(),
    )
    await state.set_state(FuelForm.operation)

# --- Шаг 0. Выбор операции (Приход / Расход) ---
@dp.message(FuelForm.operation)
async def process_operation(message: types.Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if "приход" in text:
        await state.update_data(operation="приход")
    elif "расход" in text:
        await state.update_data(operation="расход")
    else:
        await message.answer("Нажмите кнопку: Приход или Расход", reply_markup=operation_kb())
        return
    await message.answer("Шаг 1. Выберите тип ГСМ:", reply_markup=fuel_kb())
    await state.set_state(FuelForm.fuel_type)

# --- Шаг 1. Тип ГСМ ---
@dp.message(FuelForm.fuel_type)
async def process_fuel_type(message: types.Message, state: FSMContext):
    await state.update_data(fuel_type=(message.text or "").strip())
    await message.answer(
        "Шаг 2. Введите фактическую плотность (т/м³, напр. 0.840):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(FuelForm.density)

# --- Шаг 2. Плотность ---
@dp.message(FuelForm.density)
async def process_density(message: types.Message, state: FSMContext):
    try:
        density = float((message.text or "").replace(",", "."))
        await state.update_data(density=density)
        await message.answer("Шаг 3. Введите объём в литрах (факт):")
        await state.set_state(FuelForm.liters)
    except ValueError:
        await message.answer("Ошибка! Введите корректное число. Попробуйте снова:")

# --- Шаг 3. Литры ---
@dp.message(FuelForm.liters)
async def process_liters(message: types.Message, state: FSMContext):
    try:
        liters = float((message.text or "").replace(",", "."))
        await state.update_data(liters=liters)
        await message.answer("Шаг 4. Введите откуда/куда (контрагент или резервуар):")
        await state.set_state(FuelForm.counterparty)
    except ValueError:
        await message.answer("Ошибка! Введите корректное число. Попробуйте снова:")

# --- Шаг 4. Контрагент / резервуар ---
@dp.message(FuelForm.counterparty)
async def process_counterparty(message: types.Message, state: FSMContext):
    await state.update_data(counterparty=(message.text or "").strip())
    await message.answer("Шаг 5. Введите уровень до/после (например, 1200 мм):")
    await state.set_state(FuelForm.level)

# --- Шаг 5. Уровень ---
@dp.message(FuelForm.level)
async def process_level(message: types.Message, state: FSMContext):
    await state.update_data(level=(message.text or "").strip())
    await message.answer(
        "Шаг 6. Введите комментарий или нажмите «Пропустить»:",
        reply_markup=skip_kb(),
    )
    await state.set_state(FuelForm.comment)

# --- Шаг 6. Комментарий + запись в таблицу ---
@dp.message(FuelForm.comment)
async def process_comment(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    comment = "" if text.lower() in ("пропустить", "-", "") else text

    user_data = await state.get_data()
    operation = user_data["operation"]
    fuel_type = user_data["fuel_type"]
    density = user_data["density"]
    liters = user_data["liters"]
    counterparty = user_data["counterparty"]
    level = user_data["level"]
    today = date.today().strftime("%d.%m.%Y")

    # Работаем с ИСПРАВЛЕННОЙ таблицей: тонны (C и E) — это формулы (=литры*плотность/1000).
    # Бот пишет литры (I/K), плотность (G) и в свою строку прописывает ту же формулу,
    # чтобы тоннаж считался в таблице автоматически.
    #
    # Столбцы:
    #   A  Дата
    #   C  Приход тн (Факт)  = I*G/1000   (формула)
    #   E  Уход тн   (Факт)  = K*G/1000   (формула)
    #   G  Плотность (Факт)
    #   I  Литры + (Факт)    -> приход
    #   K  Литры - (Факт)    -> расход
    #   L  наименование, M Откуда/Куда, N Уровень до/после, O Комментарии
    tons = round(liters * density / 1000, 3)  # для подтверждающего сообщения

    try:
        # Находим первую пустую строку по столбцу A (Дата). Строка 1 — заголовок.
        dates = worksheet.col_values(1)
        target_row = len(dates) + 1
        if target_row < 2:
            target_row = 2

        # Список ячеек на запись: (адрес, значение)
        cells = [
            (f"A{target_row}", today),
            (f"G{target_row}", density),
            (f"L{target_row}", fuel_type),
            (f"M{target_row}", counterparty),
            (f"N{target_row}", level),
            (f"O{target_row}", comment),
        ]

        if operation == "приход":
            cells.append((f"I{target_row}", liters))                                  # Литры + (Факт)
            cells.append((f"C{target_row}", f"=I{target_row}*G{target_row}/1000"))     # Приход тн (Факт)
        else:  # расход
            cells.append((f"K{target_row}", liters))                                  # Литры - (Факт)
            cells.append((f"E{target_row}", f"=K{target_row}*G{target_row}/1000"))     # Уход тн (Факт)

        worksheet.batch_update(
            [{"range": addr, "values": [[val]]} for addr, val in cells],
            value_input_option="USER_ENTERED",  # чтобы дата и формулы распознавались
        )

        await message.answer(
            f"✅ Запись добавлена в таблицу (строка {target_row})!\n"
            f"Операция: {operation}\n"
            f"Тип ГСМ: {fuel_type}\n"
            f"Плотность (факт): {density} т/м³\n"
            f"Литры (факт): {liters} л → {tons} т\n"
            f"Контрагент: {counterparty}\n"
            f"Уровень: {level}\n"
            f"Комментарий: {comment or 'Отсутствует'}",
            reply_markup=operation_kb(),
        )
    except Exception as e:
        await message.answer(
            f"❌ Произошла ошибка при записи в таблицу: {e}",
            reply_markup=operation_kb(),
        )

    # Возвращаемся к выбору операции для следующей записи
    await state.clear()
    await state.set_state(FuelForm.operation)

# --- Любое другое сообщение: показываем меню ---
@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите операцию:", reply_markup=operation_kb())
    await state.set_state(FuelForm.operation)

# --- Запуск бота ---
async def main():
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
