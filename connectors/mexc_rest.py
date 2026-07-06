"""
connectors/mexc_rest.py — асинхронный REST-клиент MEXC (spot v3).

В рамках вехи 1 реализованы ПУБЛИЧНЫЕ эндпоинты (без подписи):
  - exchange_info()  : /api/v3/exchangeInfo   — правила торговли и список пар
  - ticker_24hr()    : /api/v3/ticker/24hr    — объёмы за 24ч (для ранжирования)
  - depth()          : /api/v3/depth          — снапшот стакана (старт локальной книги)

Приватные торговые методы (постановка ордеров, listenKey) добавим на вехе 6 —
они используют connectors/auth.py для подписи и заголовок X-MEXC-APIKEY.

Клиент асинхронный (aiohttp), с единой обёрткой _get для логирования и обработки
ошибок. Сеть в среде разработки может быть недоступна — логику отбора пар можно
проверять на сохранённом снапшоте (см. scripts/demo_pair_selection.py).
"""

from __future__ import annotations
from typing import Optional

import aiohttp

from infra.config import Config
from connectors.auth import signed_query
from infra.logging_conf import get_logger

log = get_logger("REST")

# Таймаут на запрос (сек). Рыночные данные должны приходить быстро.
_REQUEST_TIMEOUT = 10


class MexcRestClient:
    """Тонкая обёртка над REST API MEXC. Держит один aiohttp-сеанс."""

    def __init__(self, config: Config):
        self._cfg = config
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "MexcRestClient":
        # Открываем сеанс при входе в async-контекст
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        # Аккуратно закрываем сеанс
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: Optional[dict] = None):
        """
        Единая точка GET-запросов: логирует, проверяет статус, возвращает JSON.
        Все публичные ошибки видны в логе с тегом [REST].
        """
        assert self._session is not None, "клиент не инициализирован (используйте async with)"
        url = f"{self._cfg.rest_base}{path}"
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.error("GET %s -> HTTP %s: %s", path, resp.status, text[:200])
                    resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            # Сетевые ошибки логируем и пробрасываем выше — вызывающий решает, что делать
            log.error("сетевая ошибка GET %s: %s", path, e)
            raise

    # ── Публичные эндпоинты ──────────────────────────────────────────────────

    async def exchange_info(self) -> dict:
        """Правила торговли и полный список спотовых пар."""
        log.info("запрос exchangeInfo")
        return await self._get("/api/v3/exchangeInfo")

    async def ticker_24hr(self) -> list:
        """Статистика за 24ч по всем парам (используем quoteVolume для ранжирования)."""
        log.info("запрос ticker/24hr")
        return await self._get("/api/v3/ticker/24hr")

    async def depth(self, symbol: str, limit: int = 5000) -> dict:
        """
        Снапшот стакана по паре. Нужен для инициализации локальной книги
        перед применением инкрементов из WebSocket (веха 2).
        """
        log.info("запрос depth %s (limit=%d)", symbol, limit)
        return await self._get("/api/v3/depth", {"symbol": symbol, "limit": limit})

    # ── Приватные (подписанные) эндпоинты — веха 6 ───────────────────────────

    async def _signed_request(self, method: str, path: str,
                              params: Optional[dict] = None):
        """
        Подписанный запрос (HMAC-SHA256). Параметры + timestamp/recvWindow
        подписываются, подпись уходит в query string, ключ — в заголовке.
        Работает и для GET (аккаунт/ордер), и для POST/DELETE (ордера).
        """
        assert self._session is not None, "клиент не инициализирован (используйте async with)"
        if not self._cfg.api_key or not self._cfg.api_secret:
            raise RuntimeError("нет API-ключей в окружении — приватный запрос невозможен")

        qs = signed_query(self._cfg.api_secret, params or {})
        url = f"{self._cfg.rest_base}{path}?{qs}"
        headers = {"X-MEXC-APIKEY": self._cfg.api_key}
        try:
            async with self._session.request(method, url, headers=headers) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    log.error("%s %s -> HTTP %s: %s", method, path, resp.status, data)
                    resp.raise_for_status()
                return data
        except aiohttp.ClientError as e:
            log.error("сетевая ошибка %s %s: %s", method, path, e)
            raise

    async def place_order(self, symbol: str, side: str, order_type: str,
                          quantity: Optional[float] = None,
                          price: Optional[float] = None,
                          quote_order_qty: Optional[float] = None) -> dict:
        """
        Ставит ордер: POST /api/v3/order.
        side: 'BUY'|'SELL'; order_type: 'FILL_OR_KILL'|'IMMEDIATE_OR_CANCEL'|'MARKET'|'LIMIT'.
        Для FOK-лимита нужны price и quantity. Для MARKET-покупки — quote_order_qty.
        """
        params: dict = {"symbol": symbol, "side": side, "type": order_type}
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty
        log.info("ордер %s %s %s qty=%s price=%s", symbol, side, order_type, quantity, price)
        return await self._signed_request("POST", "/api/v3/order", params)

    async def query_order(self, symbol: str, order_id: str) -> dict:
        """Статус ордера: GET /api/v3/order."""
        return await self._signed_request("GET", "/api/v3/order",
                                          {"symbol": symbol, "orderId": order_id})

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Отмена ордера: DELETE /api/v3/order."""
        return await self._signed_request("DELETE", "/api/v3/order",
                                          {"symbol": symbol, "orderId": order_id})

    async def account(self) -> dict:
        """Балансы и данные аккаунта: GET /api/v3/account (подписанный)."""
        return await self._signed_request("GET", "/api/v3/account", {})