"""
control/states.py — FSM-состояния для ручного ввода значений.

После нажатия «Задать сумму»/«Задать порог» бот переходит в состояние ожидания,
и следующее текстовое сообщение трактуется как ответ (а не случайный текст).
Импорт aiogram защищён, чтобы модуль импортировался и без установленного aiogram.
"""

from __future__ import annotations

try:
    from aiogram.fsm.state import State, StatesGroup

    class InputStates(StatesGroup):
        """Состояния диалога ручного ввода."""
        waiting_amount = State()      # ждём ввод суммы связки
        waiting_threshold = State()   # ждём ввод порога в %

    _AIOGRAM_OK = True
except Exception:                    # noqa: BLE001
    # Заглушка, чтобы импорт не падал в средах без aiogram (например, в тестах)
    InputStates = None
    _AIOGRAM_OK = False