"""
reporting/snapshot.py — построение снапшота таблицы связок.

Единый источник данных для обоих выводов (терминал и Google Sheets): берёт
треугольники и текущие книги, считает спред каждого, формирует строки Row,
сортирует по убыванию нетто-спреда и проставляет ранг.

Чистая логика без внешних зависимостей — легко тестируется офлайн.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from core.spread import evaluate_triangle

# Провайдер комиссий: symbol -> ставка тейкера (доля)
FeeProvider = Callable[[str], float]


@dataclass
class Row:
    """Одна строка таблицы мониторинга (для терминала и Google Sheets)."""
    rank: int
    path: str                    # 'USDT → BTC → ETH → USDT'
    pattern: str                 # 'BBS' / 'BSS'
    leg_labels: list             # ['BUY BTCUSDT', 'BUY ETHBTC', 'SELL ETHUSDT']
    prices: list                 # лучшая цена по каждой ноге (или None)
    result: Optional[float]      # итоговая сумма после 3 ног
    net_pct: Optional[float]     # нетто-спред, %
    gross_pct: Optional[float]   # брутто-спред, %
    fees_pct: Optional[float]    # суммарные комиссии, %
    executable: bool             # хватило ли глубины
    status: str                  # 'норм' / 'мало ликвидности' / 'аномалия'
    updated_at: str              # метка времени пересчёта


def _best_price(book: Optional[dict], side: str) -> Optional[float]:
    """Лучшая цена нужной стороны книги (верхний уровень), либо None."""
    if not book:
        return None
    levels = book.get(side)
    if levels:
        return levels[0][0]
    return None


def build_rows(
    triangles: list,
    books: dict,
    amount: float,
    fee_provider: FeeProvider,
    anomaly_pct: float,
) -> list[Row]:
    """
    Строит и сортирует строки таблицы по всем треугольникам.
    Сортировка: по убыванию нетто-спреда; неисполнимые (None) — в конце.
    """
    rows: list[Row] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for tri in triangles:
        ev = evaluate_triangle(tri, books, amount, fee_provider)

        leg_labels = [f"{leg.side} {leg.symbol}" for leg in tri.legs]
        prices = [_best_price(books.get(leg.symbol), leg.book_side) for leg in tri.legs]

        # Статус строки
        if not ev.executable or ev.net_pct is None:
            status = "мало ликвидности"
        elif ev.net_pct >= anomaly_pct:
            status = "аномалия"          # спред «слишком хорош» — вероятно битые данные
        else:
            status = "норм"

        fees = None
        if ev.net_pct is not None and ev.gross_pct is not None:
            fees = ev.gross_pct - ev.net_pct

        rows.append(Row(
            rank=0,
            path=tri.path,
            pattern=tri.pattern,
            leg_labels=leg_labels,
            prices=prices,
            result=ev.final_amount,
            net_pct=ev.net_pct,
            gross_pct=ev.gross_pct,
            fees_pct=fees,
            executable=ev.executable,
            status=status,
            updated_at=now,
        ))

    # Сортировка по нетто-спреду (None — в конец), затем проставляем ранг
    rows.sort(
        key=lambda r: r.net_pct if r.net_pct is not None else float("-inf"),
        reverse=True,
    )
    for i, r in enumerate(rows, start=1):
        r.rank = i
    return rows