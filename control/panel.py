"""
control/panel.py — чистая логика Telegram-пульта (без aiogram).

Здесь только то, что не зависит от фреймворка: рендер текста сообщения-пульта
из RuntimeState и валидация ручного ввода суммы/порога. Так логику легко
тестировать офлайн, а telegram_bot.py остаётся тонким слоем поверх aiogram.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

from infra.config import RuntimeState

# Разумные границы ручного ввода — защита от опечаток
_AMOUNT_MAX = 1_000_000.0     # больше — почти наверняка ошибка
_THRESHOLD_MAX = 50.0         # порог в %, реальные спреды — доли процента


def render_panel_text(state: RuntimeState, updated_at: Optional[str] = None) -> str:
    """Собирает текст сообщения-пульта из текущего состояния."""
    status = "▶️ работает" if state.running else "⏸ остановлен"
    if state.mode == "live":
        mode = "🔴 LIVE (реальные ордера!)"
    else:
        mode = "🟢 DRY (без реальных ордеров)"
    ts = updated_at or datetime.now().strftime("%H:%M:%S")

    return (
        "🤖 <b>MEXC Арбитраж — ПУЛЬТ</b>\n\n"
        f"📊 Статус: {status}\n"
        f"🔧 Режим: {mode}\n"
        f"💰 Сумма: {state.amount_usdt:.0f} USDT\n"
        f"🎯 Порог: {state.threshold_pct:.2f}%\n\n"
        f"<i>Последнее обновление: {ts}</i>"
    )


def _to_number(text: str) -> Optional[float]:
    """Парсит число, принимая запятую как десятичный разделитель."""
    try:
        return float(text.strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_amount(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Проверяет ввод суммы. Возвращает (значение, None) при успехе
    или (None, текст_ошибки) при неверном вводе.
    """
    value = _to_number(text)
    if value is None:
        return None, "Нужно число. Пример: 1500"
    if value <= 0:
        return None, "Сумма должна быть больше нуля."
    if value > _AMOUNT_MAX:
        return None, f"Слишком большая сумма (максимум {_AMOUNT_MAX:.0f})."
    return value, None


def parse_threshold(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Проверяет ввод порога (в процентах). Возвращает (значение, None)
    при успехе или (None, текст_ошибки) при неверном вводе.
    """
    value = _to_number(text)
    if value is None:
        return None, "Нужно число. Пример: 0.35"
    if value <= 0:
        return None, "Порог должен быть больше нуля."
    if value > _THRESHOLD_MAX:
        return None, f"Слишком большой порог (максимум {_THRESHOLD_MAX:.0f}%)."
    return value, None