"""
connectors/mexc_ws.py — асинхронный WebSocket-клиент MEXC с ДИНАМИЧЕСКОЙ переподпиской.

Отвечает за транспорт: подключение, подписку на depth-каналы, поддержание
соединения (ping), авто-reconnect и — новое (Часть A) — смену состава подписок
на лету БЕЗ разрыва соединений. Разбор фреймов делегируется depth_decoder.

Модель: пул «слотов». Каждый слот = одно WS-соединение, держит до 30 подписок.
При обновлении состава (set_symbols) считаем разницу и шлём SUBSCRIPTION на новые
каналы и UNSUBSCRIPTION на выбывшие — соединения продолжают работать.

Факты MEXC: канал spot@public.aggre.depth.v3.api.pb@100ms@SYMBOL (protobuf);
≤30 подписок на соединение; соединение ≤24ч; ping против простоя; фреймы — bytes.
"""

from __future__ import annotations
import asyncio
import json
from typing import Callable, Awaitable, Optional

import websockets

from connectors.depth_decoder import decode, DepthUpdate
from infra.logging_conf import get_logger

log = get_logger("WS")

_MAX_SUBS_PER_CONN = 30       # лимит подписок на одно соединение (MEXC)
_PING_INTERVAL = 20          # период ping, сек
_RECONNECT_DELAY = 3         # пауза перед переподключением, сек

UpdateHandler = Callable[[DepthUpdate], Awaitable[None]]


def _depth_channel(symbol: str) -> str:
    """Имя канала глубины для символа (protobuf, интервал 100мс)."""
    return f"spot@public.aggre.depth.v3.api.pb@100ms@{symbol}"


class _Slot:
    """Один WS-слот: соединение + набор его текущих символов (до 30)."""

    def __init__(self, url: str, handler: UpdateHandler, slot_id: int):
        self._url = url
        self._handler = handler
        self.id = slot_id
        self.symbols: set[str] = set()      # текущий целевой состав слота
        self._ws = None                     # активное соединение (или None)
        self._running = False

    def has_room(self) -> bool:
        """Есть ли место под ещё одну подписку в этом слоте."""
        return len(self.symbols) < _MAX_SUBS_PER_CONN

    async def run(self) -> None:
        """Цикл соединения слота с авто-reconnect. При реконнекте переподписывает всё."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(self._url, ping_interval=None) as ws:
                    self._ws = ws
                    # При (пере)подключении подписываемся на весь текущий состав
                    if self.symbols:
                        await self._send_sub(list(self.symbols))
                    await asyncio.gather(self._recv_loop(ws), self._ping_loop(ws))
            except Exception as e:                    # noqa: BLE001
                self._ws = None
                if not self._running:
                    break
                log.error("слот %d: обрыв (%s) — реконнект через %ds", self.id, e, _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    def stop(self) -> None:
        self._running = False

    async def add(self, syms: list[str]) -> None:
        """Добавляет символы в слот и, если соединение живо, шлёт SUBSCRIPTION."""
        fresh = [s for s in syms if s not in self.symbols]
        if not fresh:
            return
        self.symbols.update(fresh)
        if self._ws is not None:
            await self._send_sub(fresh)

    async def remove(self, syms: list[str]) -> None:
        """Убирает символы из слота и, если соединение живо, шлёт UNSUBSCRIPTION."""
        gone = [s for s in syms if s in self.symbols]
        if not gone:
            return
        for s in gone:
            self.symbols.discard(s)
        if self._ws is not None:
            await self._send_unsub(gone)

    async def _send_sub(self, syms: list[str]) -> None:
        params = [_depth_channel(s) for s in syms]
        try:
            await self._ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
            log.info("слот %d: +%d подписок (итого %d)", self.id, len(syms), len(self.symbols))
        except Exception as e:                        # noqa: BLE001
            log.error("слот %d: ошибка подписки: %s", self.id, e)

    async def _send_unsub(self, syms: list[str]) -> None:
        params = [_depth_channel(s) for s in syms]
        try:
            await self._ws.send(json.dumps({"method": "UNSUBSCRIPTION", "params": params}))
            log.info("слот %d: -%d подписок (итого %d)", self.id, len(syms), len(self.symbols))
        except Exception as e:                        # noqa: BLE001
            log.error("слот %d: ошибка отписки: %s", self.id, e)

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, str):
                log.debug("слот %d текст: %s", self.id, raw[:120])
                continue
            upd = decode(raw)
            if upd is not None:
                await self._handler(upd)

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await ws.send(json.dumps({"method": "PING"}))
            except Exception:                         # noqa: BLE001
                return


class MexcWsClient:
    """Пул WS-слотов с динамической сменой состава подписок на лету."""

    def __init__(self, ws_url: str, symbols: list[str], handler: UpdateHandler,
                 max_slots: int = 3):
        self._url = ws_url
        self._handler = handler
        self._max_slots = max_slots
        self._slots: list[_Slot] = []
        self._initial = list(symbols)
        self._lock = asyncio.Lock()               # защита от гонок при переподписке

    async def run(self) -> None:
        """Поднимает слоты под стартовый состав и запускает их циклы."""
        # Раскидываем стартовые символы по слотам (по 30)
        self._slots = [_Slot(self._url, self._handler, i) for i in range(self._max_slots)]
        for i, sym in enumerate(self._initial):
            self._slots[i // _MAX_SUBS_PER_CONN % self._max_slots].symbols.add(sym)
        active = [s for s in self._slots if s.symbols]
        log.info("запуск %d WS-слотов на %d символов", len(active), len(self._initial))
        await asyncio.gather(*(s.run() for s in self._slots))

    async def stop(self) -> None:
        for s in self._slots:
            s.stop()

    def current_symbols(self) -> set[str]:
        """Все символы, на которые сейчас подписаны слоты."""
        out: set[str] = set()
        for s in self._slots:
            out |= s.symbols
        return out

    async def set_symbols(self, target: list[str]) -> None:
        """
        Приводит состав подписок к target: снимает выбывшие, добавляет новые.
        Соединения НЕ рвутся — только SUBSCRIPTION/UNSUBSCRIPTION по разнице.
        """
        async with self._lock:
            target_set = set(target)
            current = self.current_symbols()
            to_remove = current - target_set
            to_add = target_set - current
            if not to_remove and not to_add:
                return

            # 1. Снимаем выбывшие с их слотов
            for slot in self._slots:
                gone = [s for s in to_remove if s in slot.symbols]
                if gone:
                    await slot.remove(gone)

            # 2. Добавляем новые в слоты со свободным местом
            for sym in to_add:
                placed = False
                for slot in self._slots:
                    if slot.has_room():
                        await slot.add([sym])
                        placed = True
                        break
                if not placed:
                    log.warning("нет места под %s — превышен лимит слотов (%d)",
                                sym, self._max_slots)

            log.info("состав WS обновлён: +%d, -%d (итого %d)",
                     len(to_add), len(to_remove), len(self.current_symbols()))