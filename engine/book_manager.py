"""
engine/book_manager.py — управление набором локальных книг ордеров.

Держит по одной OrderBook на каждый подписанный символ и:
  - применяет decoded-обновления (DepthUpdate) к нужной книге;
  - при разрыве версий помечает книгу на пересинхронизацию и вызывает колбэк
    resync (его задаёт вызывающий код — обычно поход в REST /api/v3/depth);
  - отдаёт актуальные книги в форме, готовой для расчёта VWAP.

book_manager НЕ знает про сеть и protobuf — только про DepthUpdate и OrderBook.
Пересинхронизацию он делегирует через колбэк, чтобы не тянуть REST-клиент внутрь.
"""

from __future__ import annotations
from typing import Callable, Optional, Awaitable

from .order_book import OrderBook
from connectors.depth_decoder import DepthUpdate
from infra.logging_conf import get_logger

log = get_logger("BOOK")

# Колбэк пересинхронизации: по символу возвращает (bids, asks, version) из снапшота.
# Асинхронный, потому что реальный resync — это сетевой REST-запрос.
ResyncCallback = Callable[[str], Awaitable[tuple]]


class BookManager:
    """Набор локальных книг + логика пересинхронизации."""

    def __init__(self, symbols: list[str], resync_cb: Optional[ResyncCallback] = None):
        # По книге на символ
        self.books: dict[str, OrderBook] = {s: OrderBook(s) for s in symbols}
        # Колбэк для получения снапшота (задаётся снаружи; в тестах может быть None)
        self._resync_cb = resync_cb
        log.info("создан менеджер книг на %d символов", len(self.books))

    def has(self, symbol: str) -> bool:
        """Есть ли книга по этому символу (подписан ли он)."""
        return symbol in self.books

    async def on_update(self, upd: DepthUpdate) -> None:
        """
        Применяет одно обновление к нужной книге.
        При разрыве версий инициирует пересинхронизацию через колбэк.
        """
        book = self.books.get(upd.symbol)
        if book is None:
            return                                   # символ не подписан — игнор

        ok = book.apply_update(upd.bids, upd.asks, upd.from_version, upd.to_version)
        if not ok and not book.ready:
            # apply_update зафиксировал разрыв — пробуем восстановить книгу
            await self._resync(upd.symbol)

    async def _resync(self, symbol: str) -> None:
        """Пересинхронизация книги: тянем свежий снапшот через колбэк."""
        if self._resync_cb is None:
            log.warning("%s: разрыв версий, но resync-колбэк не задан", symbol)
            return
        log.info("%s: пересинхронизация книги...", symbol)
        try:
            bids, asks, version = await self._resync_cb(symbol)
            self.books[symbol].init_from_snapshot(bids, asks, version)
        except Exception as e:                        # noqa: BLE001 — логируем любую
            log.error("%s: пересинхронизация не удалась: %s", symbol, e)

    def init_book(self, symbol: str, bids: list, asks: list, version: int) -> None:
        """Прямая инициализация книги снапшотом (при старте, до потока обновлений)."""
        if symbol in self.books:
            self.books[symbol].init_from_snapshot(bids, asks, version)

    def snapshot_books(self, depth: int = 50) -> dict:
        """
        Отдаёт все готовые книги в форме {symbol: {'asks': [...], 'bids': [...]}}.
        Только ready-книги — по неготовым считать спред нельзя.
        """
        out = {}
        for symbol, book in self.books.items():
            if book.ready:
                out[symbol] = book.top(depth)
        return out

    def book_for(self, symbol: str, depth: int = 50) -> Optional[dict]:
        """Топ-уровни одной книги (для реактивного пересчёта на вехе 3)."""
        book = self.books.get(symbol)
        if book is None or not book.ready:
            return None
        return book.top(depth)
