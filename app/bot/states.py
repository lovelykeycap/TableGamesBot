from aiogram.fsm.state import State, StatesGroup


class JoinByCode(StatesGroup):
    waiting_for_code = State()


class AdminInput(StatesGroup):
    waiting_for_text = State()


class HatInput(StatesGroup):
    waiting_for_word = State()
    waiting_for_emergency_phrase = State()
