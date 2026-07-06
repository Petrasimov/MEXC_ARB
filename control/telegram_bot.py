"""
control/telegram_bot.py — Telegram-пульт на aiogram (веха 5).

«Тонкий» управляющий слой: не содержит торговой логики, только меняет
RuntimeState (сумма, порог, режим, вкл/выкл), который движок читает атомарно.
Работает в том же event loop, что WS-клиент и репортёр.

Пульт — одно сообщение, которое редактируется на месте. Сумма и порог задаются
ручным вводом через FSM. Переключение на LIVE требует подтверждения.

Импорт aiogram защищён: без библиотеки модуль импортируется, но запуск попросит
установить aiogram (полноценный прогон — на машине с токеном).
"""

from __future__ import annotations
from typing import Optional

from infra.config import RuntimeState
from infra.logging_conf import get_logger
from control import keyboard as kb
from control.panel import render_panel_text, parse_amount, parse_threshold

log = get_logger("TG")

# Защищённый импорт aiogram
try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.filters import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.types import Message, CallbackQuery
    from aiogram.client.default import DefaultBotProperties
    from control.states import InputStates
    _AIOGRAM_OK = True
except Exception as e:                   # noqa: BLE001
    _AIOGRAM_OK = False
    _IMPORT_ERR = e


class TelegramPanel:
    """Telegram-пульт управления ботом. Связан с общим RuntimeState."""

    def __init__(self, token: str, chat_id: int, state: RuntimeState):
        if not _AIOGRAM_OK:
            raise RuntimeError(
                f"aiogram не установлен ({_IMPORT_ERR}). "
                f"Установите зависимости: pip install -r requirements.txt"
            )
        self._token = token
        self._chat_id = chat_id
        self._state = state
        self._bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
        self._dp = Dispatcher()
        self._router = Router()
        self._dp.include_router(self._router)
        self._panel_msg: Optional[Message] = None
        self._register()

    # ── Регистрация обработчиков ─────────────────────────────────────────────

    def _register(self) -> None:
        r = self._router
        r.message.register(self._cmd_start, Command("start"))
        r.message.register(self._cmd_cancel, Command("cancel"))
        # Кнопки управления
        r.callback_query.register(self._on_start, F.data == kb.CB_START)
        r.callback_query.register(self._on_stop, F.data == kb.CB_STOP)
        r.callback_query.register(self._on_dry, F.data == kb.CB_DRY)
        r.callback_query.register(self._on_live, F.data == kb.CB_LIVE)
        r.callback_query.register(self._on_live_confirm, F.data == kb.CB_LIVE_CONFIRM)
        r.callback_query.register(self._on_live_cancel, F.data == kb.CB_LIVE_CANCEL)
        r.callback_query.register(self._on_refresh, F.data == kb.CB_REFRESH)
        r.callback_query.register(self._on_set_amount, F.data == kb.CB_SET_AMOUNT)
        r.callback_query.register(self._on_set_threshold, F.data == kb.CB_SET_THRESHOLD)
        # Ввод значений в состояниях FSM
        r.message.register(self._input_amount, InputStates.waiting_amount)
        r.message.register(self._input_threshold, InputStates.waiting_threshold)

    # ── Запуск ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Отправляет пульт и запускает polling (живёт в общем event loop)."""
        self._panel_msg = await self._bot.send_message(
            self._chat_id, render_panel_text(self._state), reply_markup=kb.main_keyboard()
        )
        log.info("Telegram-пульт запущен")
        await self._dp.start_polling(self._bot)

    async def _refresh(self) -> None:
        """Перерисовывает сообщение-пульт с актуальным состоянием."""
        if self._panel_msg is None:
            return
        try:
            await self._panel_msg.edit_text(
                render_panel_text(self._state), reply_markup=kb.main_keyboard()
            )
        except Exception as e:               # noqa: BLE001 — «message is not modified» и т.п.
            log.debug("не удалось обновить пульт: %s", e)

    # ── Обработчики команд ───────────────────────────────────────────────────

    async def _cmd_start(self, message: Message) -> None:
        """Команда /start — присылает свежий пульт."""
        self._panel_msg = await message.answer(
            render_panel_text(self._state), reply_markup=kb.main_keyboard()
        )

    async def _cmd_cancel(self, message: Message, state: FSMContext) -> None:
        """Команда /cancel — выход из режима ввода."""
        await state.clear()
        await message.answer("Ввод отменён.")

    # ── Кнопки старт/стоп/режим ──────────────────────────────────────────────

    async def _on_start(self, cq: CallbackQuery) -> None:
        self._state.running = True
        log.info("мониторинг ВКЛючён через пульт")
        await self._refresh()
        await cq.answer("Запущено")

    async def _on_stop(self, cq: CallbackQuery) -> None:
        self._state.running = False
        log.info("мониторинг ОСТАНОВлен через пульт")
        await self._refresh()
        await cq.answer("Остановлено")

    async def _on_dry(self, cq: CallbackQuery) -> None:
        self._state.mode = "dry"
        log.info("режим переключён на DRY")
        await self._refresh()
        await cq.answer("Режим DRY")

    async def _on_live(self, cq: CallbackQuery) -> None:
        """LIVE не включаем сразу — сначала спрашиваем подтверждение."""
        await cq.message.edit_text(
            "⚠️ <b>Включить LIVE-режим?</b>\n\n"
            "Будут выставляться <b>реальные ордера</b> на бирже. "
            "У MEXC нет тестнета — сделки настоящие.",
            reply_markup=kb.live_confirm_keyboard(),
        )
        await cq.answer()

    async def _on_live_confirm(self, cq: CallbackQuery) -> None:
        self._state.mode = "live"
        log.warning("режим переключён на LIVE через пульт")
        await self._refresh()
        await cq.answer("LIVE включён")

    async def _on_live_cancel(self, cq: CallbackQuery) -> None:
        await self._refresh()
        await cq.answer("Отменено")

    async def _on_refresh(self, cq: CallbackQuery) -> None:
        await self._refresh()
        await cq.answer("Обновлено")

    # ── Ручной ввод суммы и порога ───────────────────────────────────────────

    async def _on_set_amount(self, cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(InputStates.waiting_amount)
        await cq.message.answer("💰 Введите сумму связки в USDT (например, 1500).\n"
                                "Отмена — /cancel")
        await cq.answer()

    async def _on_set_threshold(self, cq: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(InputStates.waiting_threshold)
        await cq.message.answer("🎯 Введите порог в процентах (например, 0.35).\n"
                                "Отмена — /cancel")
        await cq.answer()

    async def _input_amount(self, message: Message, state: FSMContext) -> None:
        """Приём введённой суммы с валидацией."""
        value, err = parse_amount(message.text or "")
        if err:
            await message.answer(f"❌ {err}\nПопробуйте ещё раз или /cancel")
            return
        self._state.amount_usdt = value
        await state.clear()
        log.info("сумма изменена через пульт: %.2f USDT", value)
        await message.answer(f"✅ Сумма: {value:.0f} USDT")
        await self._refresh()

    async def _input_threshold(self, message: Message, state: FSMContext) -> None:
        """Приём введённого порога с валидацией."""
        value, err = parse_threshold(message.text or "")
        if err:
            await message.answer(f"❌ {err}\nПопробуйте ещё раз или /cancel")
            return
        self._state.threshold_pct = value
        await state.clear()
        log.info("порог изменён через пульт: %.4f%%", value)
        await message.answer(f"✅ Порог: {value:.2f}%")
        await self._refresh()