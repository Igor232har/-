import os
import asyncio
from datetime import date
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# --- Загружаем токены из секретного файла .env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

# --- Настройка доступа к Google Sheets ---
SERVICE_ACCOUNT_FILE = "gsmbotproject-6e6ba3697a39.json" # Укажите ИМЯ вашего JSON-файла
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

# --- Обработчики команд бота ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear() # На всякий случай очищаем старые диалоги
    await message.answer(
        "Привет! Я бот для учёта ГСМ на нефтебазе.\n"
        "Для начала работы отправьте команду:\n"
        "/prihod - для учёта прихода топлива\n"
        "/rashod - для учёта расхода топлива"
    )

@dp.message(Command("prihod"))
async def cmd_prihod(message: types.Message, state: FSMContext):
    await state.update_data(operation="приход")
    await message.answer("Шаг 1. Введите тип ГСМ (например, АИ-92, ДТ):")
    await state.set_state(FuelForm.fuel_type)

@dp.message(Command("rashod"))
async def cmd_rashod(message: types.Message, state: FSMContext):
    await state.update_data(operation="расход")
    await message.answer("Шаг 1. Введите тип ГСМ (например, АИ-92, ДТ):")
    await state.set_state(FuelForm.fuel_type)

@dp.message(FuelForm.fuel_type)
async def process_fuel_type(message: types.Message, state: FSMContext):
    await state.update_data(fuel_type=message.text)
    await message.answer("Шаг 2. Введите фактическую плотность (кг/м³):")
    await state.set_state(FuelForm.density)

@dp.message(FuelForm.density)
async def process_density(message: types.Message, state: FSMContext):
    try:
        density = float(message.text)
        await state.update_data(density=density)
        await message.answer("Шаг 3. Введите объём в литрах (факт):")
        await state.set_state(FuelForm.liters)
    except ValueError:
        await message.answer("Ошибка! Введите корректное число. Попробуйте снова:")

@dp.message(FuelForm.liters)
async def process_liters(message: types.Message, state: FSMContext):
    try:
        liters = float(message.text)
        await state.update_data(liters=liters)
        await message.answer("Шаг 4. Введите откуда/куда (контрагент или резервуар):")
        await state.set_state(FuelForm.counterparty)
    except ValueError:
        await message.answer("Ошибка! Введите корректное число. Попробуйте снова:")

@dp.message(FuelForm.counterparty)
async def process_counterparty(message: types.Message, state: FSMContext):
    await state.update_data(counterparty=message.text)
    await message.answer("Шаг 5. Введите уровень до/после (например, 1200 мм):")
    await state.set_state(FuelForm.level)

@dp.message(FuelForm.level)
async def process_level(message: types.Message, state: FSMContext):
    await state.update_data(level=message.text)
    await message.answer("Шаг 6. Введите комментарий (или отправьте '-' для пропуска):")
    await state.set_state(FuelForm.comment)

@dp.message(FuelForm.comment)
async def process_comment(message: types.Message, state: FSMContext):
    # Собираем все данные в словарь
    comment = message.text if message.text != "-" else ""
    user_data = await state.get_data()
    user_data["comment"] = comment

    operation = user_data["operation"]
    fuel_type = user_data["fuel_type"]
    density = user_data["density"]
    liters = user_data["liters"]
    counterparty = user_data["counterparty"]
    level = user_data["level"]
    today = date.today().isoformat()

    # Рассчитываем массу в тоннах
    tons = round(liters * density / 1000, 3)

    # Определяем, в какие столбцы писать
    if operation == "приход":
        tons_col = 3   # Столбец C (Приход тн Факт)
        liters_col = 9 # Столбец I (Литры + факт)
    else:  # расход
        tons_col = 5   # Столбец E (Уход тн Факт)
        liters_col = 11 # Столбец K (Литры - факт)

    # Готовим строку для записи
    # Создаём пустой список на 20 элементов, чтобы заполнить нужные колонки
    row_to_add = [None] * 20
    row_to_add[0] = today                 # A: Дата
    row_to_add[tons_col-1] = tons         # C/E: Тонны (индекс = номер столбца - 1)
    row_to_add[liters_col-1] = liters     # I/K: Литры (индекс = номер столбца - 1)
    row_to_add[6] = density               # G: Плотность (Факт) (7-й столбец -> индекс 6)
    row_to_add[11] = fuel_type            # L: Наименование (12-й столбец -> индекс 11)
    row_to_add[12] = counterparty         # M: Откуда/Куда (13-й столбец -> индекс 12)
    row_to_add[13] = level                # N: Уровень до/после (14-й столбец -> индекс 13)
    row_to_add[14] = comment              # O: Коментарии (15-й столбец -> индекс 14)

    # Записываем в таблицу
    try:
        worksheet.append_row(row_to_add)
        await message.answer(
            f"✅ Данные успешно добавлены в таблицу!\n"
            f"Операция: {operation}\n"
            f"Тип ГСМ: {fuel_type}\n"
            f"Плотность: {density} кг/м³\n"
            f"Литры: {liters} л → {tons} т\n"
            f"Контрагент: {counterparty}\n"
            f"Уровень: {level}\n"
            f"Комментарий: {comment or 'Отсутствует'}"
        )
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка при записи в таблицу: {e}")

    # Очищаем состояние, чтобы освободить память
    await state.clear()

# --- Запуск бота ---
async def main():
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())