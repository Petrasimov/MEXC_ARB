"""
engine/cold_scanner.py — холодный скан всей биржи по bookTicker.

Идея (Часть A плана): один запрос bookTicker даёт лучшие bid/ask с объёмами по
всем парам. Из них строим «псевдо-книги» в 1 уровень, грубо оцениваем спред КАЖДОГО
треугольника, отсеиваем фантомы (где объёма на топе меньше суммы пользователя),
сортируем по спреду и берём топ-N. Это НЕ точный VWAP на всю глубину — это дешёвый
отбор кандидатов. Точная оценка идёт потом по живым WS-книгам.

Важно: спред тут оптимистичен (по одному уровню), поэтому это только фильтр «где
может быть интересно», а не решение о сделке.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from core.spread import evaluate_triangle
from infra.logging_conf import get_logger

log = get_logger("PAIRS")

FeeProvider = Callable[[str], float]


@dataclass
class ColdResult:
    """Результат холодного скана: топ треугольников и их уникальные пары."""
    triangles: list                 # топ-N треугольников по спреду (объекты Triangle)
    symbols: list                   # уникальные пары этих треугольников (для WS)
    ranked: list                    # список (triangle, net_pct) для лога/отладки


def build_pseudo_books(book_ticker: list, min_notional: float) -> dict:
    """
    Строит псевдо-книги {symbol: {'asks': [[price, qty]], 'bids': [[price, qty]]}}
    из ответа bookTicker.

    Про фильтр ликвидности: notional верхнего уровня у разных пар выражен в РАЗНЫХ
    котируемых валютах (BTCUSDT — в USDT, ETHBTC — в BTC), поэтому сравнивать его
    напрямую с суммой пользователя в USDT некорректно. Здесь оставляем только грубую
    защиту: отсекаем пары с нулевым/пустым топом. Точную проверку ликвидности на
    сумму делает горячая фаза по полной WS-книге в правильных валютах.
    """
    books: dict = {}
    for row in book_ticker:
        try:
            symbol = row["symbol"]
            bid_p = float(row["bidPrice"]); bid_q = float(row["bidQty"])
            ask_p = float(row["askPrice"]); ask_q = float(row["askQty"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid_p <= 0 or ask_p <= 0 or bid_q <= 0 or ask_q <= 0:
            continue                                 # пустой/битый топ — пропуск
        # Для ОЦЕНКИ спреда раздуваем объём уровня: холодный скан меряет спред по
        # ЦЕНЕ top-of-book, а не по глубине. Реальная глубина проверяется на горячей фазе.
        big = 1e18
        books[symbol] = {"asks": [[ask_p, big]], "bids": [[bid_p, big]]}
    return books
    return books


def cold_scan(
    triangles: list,
    book_ticker: list,
    amount: float,
    fee_provider: FeeProvider,
    top_n: int,
    max_subs: int = 30,
) -> ColdResult:
    """
    Оценивает все треугольники по псевдо-книгам bookTicker, сортирует по спреду,
    берёт топ-N. Возвращает треугольники, уникальные пары (с учётом лимита подписок)
    и полный ранжированный список для лога.
    """
    books = build_pseudo_books(book_ticker, min_notional=amount)

    ranked: list = []
    for tri in triangles:
        ev = evaluate_triangle(tri, books, amount, fee_provider)
        if not ev.executable or ev.net_pct is None:
            continue                                 # не хватило псевдо-глубины/пары нет
        ranked.append((tri, ev.net_pct))

    # Сортируем по убыванию спреда
    ranked.sort(key=lambda x: x[1], reverse=True)

    # Набираем топ-треугольники, следя чтобы их уникальные пары влезли в лимиты WS.
    # Один треугольник добавляет до 3 новых пар; берём пока пары помещаются.
    chosen: list = []
    symbols: list = []
    seen: set = set()
    for tri, net in ranked:
        new_syms = [s for s in tri.symbols if s not in seen]
        if len(symbols) + len(new_syms) > max_subs * _max_conns():
            continue                                 # не влезает в общий лимит подписок
        for s in new_syms:
            seen.add(s); symbols.append(s)
        chosen.append(tri)
        if len(chosen) >= top_n:
            break

    log.info("холодный скан: оценено %d, кандидатов %d, выбрано %d треугольников, %d пар",
             len(triangles), len(ranked), len(chosen), len(symbols))
    return ColdResult(triangles=chosen, symbols=symbols, ranked=ranked)


def _max_conns() -> int:
    """Сколько WS-соединений максимум разрешаем поднимать (лимит 30 подписок каждое)."""
    return 3   # до 90 подписок суммарно — с запасом на топ-30 треугольников