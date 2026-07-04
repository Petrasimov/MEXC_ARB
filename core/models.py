"""
core/models.py — базовые модели данных треугольного арбитража.

Здесь только структуры (датаклассы), без логики.
Логика вынесена в triangles.py, vwap.py, spread.py — чтобы файлы были небольшими
и каждый отвечал за одну задачу.

Замечание по точности: для MVP используем float. В продакшене денежные величины
стоит перевести на Decimal, чтобы избежать накопления ошибок округления.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Pair:
    """Спотовая пара BASE/QUOTE. Пример: 'BTCUSDT' -> base='BTC', quote='USDT'."""
    symbol: str          # биржевой символ, напр. 'BTCUSDT'
    base: str            # базовый актив (что торгуем)
    quote: str           # котируемый актив (за что торгуем)
    taker_fee: float = 0.0  # комиссия тейкера для этой пары (доля, 0.001 = 0.1%)


@dataclass(frozen=True)
class Leg:
    """Одна нога треугольника: конверсия from_asset -> to_asset через одну пару."""
    symbol: str          # символ пары для этой ноги
    side: str            # 'BUY' | 'SELL'
    book_side: str       # 'asks' | 'bids' — какую сторону книги проходим
    from_asset: str      # актив на входе
    to_asset: str        # актив на выходе


@dataclass(frozen=True)
class Triangle:
    """Замкнутый цикл из 3 ног: start -> A -> B -> start."""
    start: str
    legs: tuple          # (Leg, Leg, Leg)

    @property
    def symbols(self) -> tuple:
        """Символы всех трёх пар цикла — удобно для подписки на книги."""
        return tuple(l.symbol for l in self.legs)

    @property
    def path(self) -> str:
        """Человекочитаемый путь: 'USDT → BTC → ETH → USDT'."""
        assets = [self.legs[0].from_asset] + [l.to_asset for l in self.legs]
        return " → ".join(assets)

    @property
    def pattern(self) -> str:
        """Тип связки в терминах BBS/BSS и т.п. (buy/sell по каждой ноге)."""
        letters = "".join("B" if l.side == "BUY" else "S" for l in self.legs)
        return letters  # напр. 'BBS' или 'BSS'

    def __str__(self) -> str:
        return f"[{self.path}] ({self.pattern}) ({', '.join(self.symbols)})"


@dataclass
class Evaluation:
    """Результат оценки одного треугольника на конкретную сумму."""
    triangle: Triangle
    start_amount: float
    final_amount: Optional[float]      # None, если не хватило ликвидности
    gross_pct: Optional[float]         # спред без комиссий, %
    net_pct: Optional[float]           # спред после 3 комиссий, %
    executable: bool                   # хватило ли глубины на всю сумму
    leg_amounts: list = field(default_factory=list)  # объём на выходе каждой ноги
