"""
scripts/demo_pair_selection.py — проверка вехи 1 без сети.

Запуск:
    python -m scripts.demo_pair_selection

Что делает:
  1. Загружает снапшоты exchangeInfo и ticker/24hr из tests/fixtures.
  2. Отбирает торгуемые пары, строит треугольники (с логами каждого шага).
  3. Печатает построенные треугольники и индекс symbol->треугольники.
  4. Прогоняет один треугольник через синтетический стакан (проверка VWAP+спреда).
"""

from __future__ import annotations
import json
import os

from infra.logging_conf import setup_logging, get_logger
from infra.config import Config, START_ASSETS, BRIDGES
from engine.pair_selector import parse_symbols, select_universe, build_index
from core.spread import evaluate_triangle

log = get_logger("DEMO")

# Путь к фикстурам относительно этого файла
_FIX = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")


def _load(name: str):
    """Загружает JSON-фикстуру по имени файла."""
    with open(os.path.join(_FIX, name), encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    setup_logging()
    cfg = Config.from_env()

    # 1. Снапшоты данных
    exchange_info = _load("exchangeinfo_sample.json")
    tickers = _load("tickers_sample.json")

    # 2. Отбор пар и построение треугольников
    pairs = parse_symbols(exchange_info, cfg.default_taker_fee)
    universe, triangles = select_universe(
        pairs, tickers, list(START_ASSETS), list(BRIDGES), cfg.top_n
    )
    index = build_index(triangles)

    # 3. Показываем результат
    print("\n=== Пары для подписки ===")
    print(", ".join(sorted(p.symbol for p in universe)))

    print("\n=== Построенные треугольники ===")
    for t in triangles:
        print("  ", t)

    print("\n=== Индекс symbol -> сколько треугольников ===")
    for sym, tris in sorted(index.items()):
        print(f"  {sym}: {len(tris)}")

    # 4. Проверка расчёта спреда на синтетическом стакане
    #    Берём треугольник USDT→USDC→BTC→USDT (buy-buy-sell), если он найден.
    target = next((t for t in triangles if t.symbols == ("USDCUSDT", "BTCUSDC", "BTCUSDT")), None)
    if target is None:
        log.warning("целевой треугольник для проверки VWAP не найден")
        return

    books = {
        "USDCUSDT": {"asks": [[1.0000, 100000.0]], "bids": [[0.9999, 100000.0]]},
        "BTCUSDC":  {"asks": [[60000.0, 5.0]],     "bids": [[59990.0, 5.0]]},
        "BTCUSDT":  {"asks": [[60050.0, 5.0]],     "bids": [[60200.0, 5.0]]},
    }
    fee_provider = lambda symbol: 0.0     # у MEXC часто 0% — для демо тоже 0

    ev = evaluate_triangle(target, books, start_amount=100.0, fee_provider=fee_provider)
    print("\n=== Проверка спреда на синтетическом стакане ===")
    print(f"  связка:   {ev.triangle}")
    print(f"  объёмы:   {[round(x, 6) for x in ev.leg_amounts]}")
    print(f"  gross:    {ev.gross_pct:+.4f}%   net: {ev.net_pct:+.4f}%")
    print(f"  результат:{ev.final_amount:.4f} USDT из 100.00")


if __name__ == "__main__":
    main()
