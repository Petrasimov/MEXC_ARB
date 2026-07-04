"""
core/spread.py — оценка прибыльности треугольника.

Прогоняет стартовую сумму через 3 ноги по реальной глубине стакана,
считает gross (без комиссий) и net (после 3 комиссий) спред, проверяет порог.

fee_provider — функция symbol -> ставка тейкера (доля). Позволяет задавать
разные комиссии по парам (и учитывать нулевые комиссии MEXC / скидку токеном).
"""

from __future__ import annotations
from typing import Callable, Optional

from .models import Triangle, Evaluation
from .vwap import walk_buy, walk_sell

# Тип провайдера комиссий: по символу возвращает ставку тейкера (напр. 0.0 или 0.001)
FeeProvider = Callable[[str], float]


def evaluate_triangle(
    triangle: Triangle,
    books: dict,
    start_amount: float,
    fee_provider: FeeProvider,
) -> Evaluation:
    """
    Прогоняет start_amount через 3 ноги. Считает gross и net спред.
    books — словарь symbol -> {'asks': [...], 'bids': [...]}.
    """
    def run(amount: float, with_fees: bool):
        """Один проход по трём ногам. Возвращает (итог, [объёмы по ногам])."""
        outs = []
        for leg in triangle.legs:
            book = books.get(leg.symbol)
            if book is None:                     # нет книги по этой паре
                return None, outs
            fee = fee_provider(leg.symbol) if with_fees else 0.0
            if leg.side == "BUY":
                amount = walk_buy(book["asks"], amount, fee)
            else:
                amount = walk_sell(book["bids"], amount, fee)
            if amount is None:                   # не хватило глубины
                return None, outs
            outs.append(amount)
        return amount, outs

    net_final, leg_amounts = run(start_amount, with_fees=True)
    gross_final, _ = run(start_amount, with_fees=False)

    if net_final is None:
        return Evaluation(triangle, start_amount, None, None, None, False, [])

    gross_pct = (gross_final / start_amount - 1.0) * 100.0
    net_pct = (net_final / start_amount - 1.0) * 100.0
    return Evaluation(
        triangle, start_amount, net_final,
        gross_pct, net_pct, True, leg_amounts,
    )


def scan(
    triangles: list[Triangle],
    books: dict,
    start_amount: float,
    fee_provider: FeeProvider,
    threshold_pct: float,
) -> list[Evaluation]:
    """
    Оценивает все треугольники, оставляет исполнимые с net > порог,
    сортирует по убыванию net-спреда. Это выход шага «решение» пайплайна.
    """
    results = []
    for tri in triangles:
        ev = evaluate_triangle(tri, books, start_amount, fee_provider)
        if ev.executable and ev.net_pct is not None and ev.net_pct > threshold_pct:
            results.append(ev)
    results.sort(key=lambda e: e.net_pct, reverse=True)
    return results
