"""
control/keyboard.py — inline-клавиатуры пульта и коды кнопок.

Собирает клавиатуры для сообщения-пульта и диалога подтверждения LIVE.
Импорт aiogram защищён — коды callback-ов доступны и без aiogram (нужны в тестах).
"""

from __future__ import annotations

# ── Коды callback-ов кнопок (строки, приходят в CallbackQuery.data) ──────────
CB_START = "ctl:start"
CB_STOP = "ctl:stop"
CB_DRY = "ctl:dry"
CB_LIVE = "ctl:live"
CB_SET_AMOUNT = "ctl:set_amount"
CB_SET_THRESHOLD = "ctl:set_threshold"
CB_REFRESH = "ctl:refresh"
CB_LIVE_CONFIRM = "ctl:live_confirm"
CB_LIVE_CANCEL = "ctl:live_cancel"

# Защищённый импорт aiogram
try:
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    _AIOGRAM_OK = True
except Exception:                        # noqa: BLE001
    _AIOGRAM_OK = False


def main_keyboard():
    """Основная клавиатура пульта. Возвращает InlineKeyboardMarkup или None."""
    if not _AIOGRAM_OK:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶️ Старт", callback_data=CB_START),
            InlineKeyboardButton(text="⏸ Стоп", callback_data=CB_STOP),
        ],
        [
            InlineKeyboardButton(text="🟢 DRY", callback_data=CB_DRY),
            InlineKeyboardButton(text="🔴 LIVE", callback_data=CB_LIVE),
        ],
        [
            InlineKeyboardButton(text="💰 Задать сумму", callback_data=CB_SET_AMOUNT),
            InlineKeyboardButton(text="🎯 Задать порог", callback_data=CB_SET_THRESHOLD),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить статус", callback_data=CB_REFRESH),
        ],
    ])


def live_confirm_keyboard():
    """Клавиатура подтверждения включения LIVE. Защита от случайного запуска."""
    if not _AIOGRAM_OK:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, включить LIVE", callback_data=CB_LIVE_CONFIRM),
            InlineKeyboardButton(text="↩️ Отмена", callback_data=CB_LIVE_CANCEL),
        ],
    ])