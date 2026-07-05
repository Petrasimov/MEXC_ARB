"""
connectors/mexc_ws.py — асинхронный WebSocket-клиент MEXC (рыночные данные).

Отвечает за транспорт: подключение, подписку на depth-каналы, поддержание
соединения (ping) и авто-переподключение. Разбор фреймов делегируется
depth_decoder, применение к книгам — book_manager. Сам клиент «тонкий».

Факты MEXC, заложенные сюда:
  - канал глубины: spot@public.aggre.depth.v3.api.pb@100ms@SYMBOL (protobuf);
  - не более 30 подписок на одно соединение → символы бьём на группы;
  - соединение живёт ≤24ч, отваливается при простое → нужен ping и reconnect;
  - фреймы бинарные (protobuf) — приходят как bytes.
"""

from __future__ import annotations
import asyncio
import json
from typing import Callable, Awaitable, Optional

import websockets

from connectors.depth_decoder import decode, DepthUpdate
from infra.logging_conf import get_logger

log = get_logger("WS")

# Лимит подписок на одно соединение (ограничение MEXC)
_MAX_SUBS_PER_CONN = 30
# Период ping в секундах (у MEXC авто-дисконнект при простое ~30-60с)
_PING_INTERVAL = 20
# Пауза перед переподключением при обрыве
_RECONNECT_DELAY = 3

# Тип обработчика обновления (обычно book_manager.on_update)
UpdateHandler = Callable[[DepthUpdate], Awaitable[None]]


def _depth_channel(symbol: str) -> str:
    """Имя канала глубины для символа (protobuf, интервал 100мс)."""
    return f"spot@public.aggre.depth.v3.api.pb@100ms@{symbol}"


class MexcWsClient:
    """Тонкий WS-клиент: держит соединения и льёт обновления в обработчик."""

    def __init__(self, ws_url: str, symbols: list[str], handler: UpdateHandler):
        self._url = ws_url
        self._symbols = list(symbols)
        self._handler = handler
        self._running = False

    async def run(self) -> None:
        """
        Запускает по одному воркеру на каждую группу символов (≤30).
        Каждый воркер сам переподключается при обрыве.
        """
        self._running = True
        groups = [
            self._symbols[i:i + _MAX_SUBS_PER_CONN]
            for i in range(0, len(self._symbols), _MAX_SUBS_PER_CONN)
        ]
        log.info("запуск %d WS-соединений на %d символов", len(groups), len(self._symbols))
        await asyncio.gather(*(self._conn_worker(g) for g in groups))

    async def stop(self) -> None:
        """Останавливает воркеры (они выйдут из цикла переподключения)."""
        self._running = False

    async def _conn_worker(self, symbols: list[str]) -> None:
        """Один воркер: соединение + подписка + приём, с авто-reconnect."""
        while self._running:
            try:
                async with websockets.connect(self._url, ping_interval=None) as ws:
                    await self._subscribe(ws, symbols)
                    # Параллельно: приём сообщений и периодический ping
                    await asyncio.gather(
                        self._recv_loop(ws),
                        self._ping_loop(ws),
                    )
            except Exception as e:                    # noqa: BLE001 — любой обрыв логируем
                if not self._running:
                    break
                log.error("обрыв WS (%d симв.): %s — переподключение через %ds",
                          len(symbols), e, _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _subscribe(self, ws, symbols: list[str]) -> None:
        """Отправляет запрос подписки на depth-каналы группы символов."""
        params = [_depth_channel(s) for s in symbols]
        await ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
        log.info("подписка на %d каналов отправлена", len(params))

    async def _recv_loop(self, ws) -> None:
        """Принимает фреймы, декодирует и передаёт в обработчик."""
        async for raw in ws:
            # Текстовые сообщения (ответы на подписку/ping) — просто логируем на DEBUG
            if isinstance(raw, str):
                log.debug("текстовое сообщение WS: %s", raw[:120])
                continue
            upd = decode(raw)                         # bytes -> DepthUpdate (или None)
            if upd is not None:
                await self._handler(upd)

    async def _ping_loop(self, ws) -> None:
        """Периодический ping, чтобы MEXC не разорвал соединение по простою."""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await ws.send(json.dumps({"method": "PING"}))
            except Exception:                         # noqa: BLE001
                return                                # соединение умерло — выйдем, воркер переподключится
