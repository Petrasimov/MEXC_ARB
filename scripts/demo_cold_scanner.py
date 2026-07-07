"""
scripts/demo_cold_scanner.py — офлайн-проверка холодного скана (Часть A).

Запуск:
    python -m scripts.demo_cold_scanner

Проверяет БЕЗ сети:
  1. Построение псевдо-книг из синтетического bookTicker.
  2. Фильтр фантомов (объём топа < суммы пользователя отсекается).
  3. Оценку всех треугольников и сортировку по спреду, выбор топ-N.
"""

from __future__ import annotations

from infra.logging_conf import setup_logging, get_logger
from core.models import Pair
from core.triangles import MarketGraph
from engine.cold_scanner import cold_scan, build_pseudo_books

log = get_logger("DEMO")


def main() -> None:
    setup_logging()

    pairs = [
        Pair("BTCUSDT", "BTC", "USDT"),
        Pair("ETHBTC",  "ETH", "BTC"),
        Pair("ETHUSDT", "ETH", "USDT"),
        Pair("SOLUSDT", "SOL", "USDT"),
        Pair("SOLBTC",  "SOL", "BTC"),
    ]
    triangles = MarketGraph(pairs).build_triangles(["USDT"])

    # Синтетический bookTicker: одна связка выгодна (ETHUSDT bid выше рынка).
    book_ticker = [
        {"symbol": "BTCUSDT", "bidPrice": "60000", "bidQty": "5", "askPrice": "60000", "askQty": "5"},
        {"symbol": "ETHBTC",  "bidPrice": "0.05",  "bidQty": "100", "askPrice": "0.05", "askQty": "100"},
        {"symbol": "ETHUSDT", "bidPrice": "3025",  "bidQty": "50", "askPrice": "3000", "askQty": "50"},
        {"symbol": "SOLUSDT", "bidPrice": "150",   "bidQty": "200", "askPrice": "150", "askQty": "200"},
        {"symbol": "SOLBTC",  "bidPrice": "0.0025", "bidQty": "0", "askPrice": "0.0025", "askQty": "0"},
    ]

    amount = 100.0

    # 1-2. Псевдо-книги (SOLBTC с нулевым объёмом отсекается как битый топ)
    books = build_pseudo_books(book_ticker, min_notional=amount)
    print("\n=== Псевдо-книги (SOLBTC с нулевым топом отсеян) ===")
    for sym, b in books.items():
        print(f"  {sym}: bid_price={b['bids'][0][0]} ask_price={b['asks'][0][0]}")
    print(f"  SOLBTC отфильтрован (нулевой объём): {'SOLBTC' not in books}")

    # 3. Холодный скан
    result = cold_scan(triangles, book_ticker, amount=amount,
                       fee_provider=lambda s: 0.0, top_n=5)
    print("\n=== Ранжирование треугольников по спреду ===")
    for tri, net in result.ranked:
        print(f"  {net:+.4f}%  {tri.path}")
    print(f"\n=== Выбрано для WS: {len(result.triangles)} треугольников, "
          f"{len(result.symbols)} уникальных пар ===")
    print("  пары:", result.symbols)


if __name__ == "__main__":
    main()