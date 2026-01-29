from aiogram.fsm.state import State, StatesGroup

class AdminState(StatesGroup):
    waiting_for_new_days = State()
    waiting_for_new_refs = State()
    editing_user_id = State()
    waiting_for_search_query = State()

class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()