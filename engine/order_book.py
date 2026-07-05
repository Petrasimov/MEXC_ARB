"""
engine/order_book.py — одна локальная книга ордеров по паре.

Хранит стороны bids/asks как словари {цена: объём} и умеет:
  - инициализироваться из REST-снапшота;
  - применять инкрементальные обновления с проверкой непрерывности версий;
  - отдавать отсортированные топ-уровни для расчёта VWAP.

Важные факты MEXC, заложенные сюда:
  - объём в обновлении АБСОЛЮТНЫЙ (не дельта): просто заменяем объём на уровне,
    а нулевой объём означает удаление уровня;
  - непрерывность по версиям: fromVersion нового апдейта должен быть равен
    предыдущему toVersion + 1, иначе фиксируем разрыв (нужна пересинхронизация).
"""

from __future__ import annotations
from typing import Optional

from infra.logging_conf import get_logger

log = get_logger("BOOK")


class OrderBook:
    """Локальная книга одной пары. Не знает про сеть и protobuf."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        # Стороны книги как {цена: объём}. Словарь удобнее для точечных апдейтов.
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        # Версия последнего применённого обновления (MEXC: toVersion / lastUpdateId)
        self.last_version: Optional[int] = None
        # Флаг готовности: книга инициализирована снапшотом и синхронна
        self.ready: bool = False

    # ── Инициализация из REST-снапшота ───────────────────────────────────────

    def init_from_snapshot(self, bids: list, asks: list, version: int) -> None:
        """
        Заполняет книгу снапшотом REST /api/v3/depth.
        bids/asks — списки [цена, объём] (строки или числа), version — lastUpdateId.
        """
        self._bids = {float(p): float(q) for p, q in bids if float(q) > 0}
        self._asks = {float(p): float(q) for p, q in asks if float(q) > 0}
        self.last_version = int(version)
        self.ready = True
        log.info("%s: снапшот загружен, version=%s, bids=%d asks=%d",
                 self.symbol, version, len(self._bids), len(self._asks))

    # ── Применение инкрементального обновления ───────────────────────────────

    def apply_update(self, bids: list, asks: list,
                     from_version: int, to_version: int) -> bool:
        """
        Применяет инкремент. Возвращает True при успехе, False при разрыве версий
        (значит нужна пересинхронизация — этим займётся book_manager).
        """
        if not self.ready or self.last_version is None:
            log.warning("%s: апдейт до инициализации — игнор", self.symbol)
            return False

        # Апдейты, которые старее снапшота, просто пропускаем (не ошибка)
        if to_version <= self.last_version:
            return True

        # Проверка непрерывности: не должно быть «дырки» в версиях
        if from_version != self.last_version + 1:
            log.error("%s: РАЗРЫВ версий: ожидали %d, пришло from=%d (to=%d)",
                      self.symbol, self.last_version + 1, from_version, to_version)
            self.ready = False
            return False

        self._apply_side(self._bids, bids)
        self._apply_side(self._asks, asks)
        self.last_version = to_version
        return True

    @staticmethod
    def _apply_side(side: dict, levels: list) -> None:
        """
        Применяет уровни к одной стороне. Объём абсолютный:
        >0 — установить/заменить объём на уровне, ==0 — удалить уровень.
        """
        for price, qty in levels:
            p, q = float(price), float(qty)
            if q <= 0:
                side.pop(p, None)          # уровень исчерпан — убираем
            else:
                side[p] = q                # заменяем объём на уровне

    # ── Выдача данных для расчёта ─────────────────────────────────────────────

    def top(self, depth: int = 50) -> dict:
        """
        Возвращает топ-N уровней в формате для VWAP:
        {'asks': [[price, qty], ...] по возрастанию,
         'bids': [[price, qty], ...] по убыванию}.
        """
        asks = sorted(self._asks.items())[:depth]              # дешёвые сверху
        bids = sorted(self._bids.items(), reverse=True)[:depth]  # дорогие сверху
        return {
            "asks": [[p, q] for p, q in asks],
            "bids": [[p, q] for p, q in bids],
        }

    def best_bid_ask(self) -> tuple:
        """Лучшие bid/ask (для быстрых проверок и логов). None, если пусто."""
        best_bid = max(self._bids) if self._bids else None
        best_ask = min(self._asks) if self._asks else None
        return best_bid, best_ask
