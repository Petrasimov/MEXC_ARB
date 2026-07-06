"""
scripts/demo_scanner.py — офлайн-проверка вехи 3 (без сети).

Запуск:
    python -m scripts.demo_scanner

Проверяет:
  1. Реактивный пересчёт: обновление символа триггерит только его связки.
  2. Формирование сигнала при net > порог.
  3. Санити-фильтр: аномальный спред отбрасывается.
  4. Замер скорости пересчёта (p50/p95/p99) на серии итераций.
"""

from __future__ import annotations
import random

from infra.logging_conf import setup_logging, get_logger
from infra.config import RuntimeState
from infra.metrics import Metrics
from engine.book_manager import BookManager
from engine.scanner import Scanner, Signal
from core.models import Pair
from core.triangles import MarketGraph, build_symbol_index

log = get_logger("DEMO")


def main() -> None:
    setup_logging()

    # 1. Собираем набор пар и треугольники (как после отбора вехи 1)
    pairs = [
        Pair("BTCUSDT", "BTC", "USDT"),
        Pair("ETHBTC",  "ETH", "BTC"),
        Pair("ETHUSDT", "ETH", "USDT"),
    ]
    triangles = MarketGraph(pairs).build_triangles(["USDT"])
    index = build_symbol_index(triangles)

    # 2. Книги через менеджер (веха 2)
    mgr = BookManager([p.symbol for p in pairs])
    mgr.init_book("BTCUSDT", [[60000.0, 5.0]], [[60000.0, 5.0]], 1)
    mgr.init_book("ETHBTC",  [[0.05, 100.0]],  [[0.05, 100.0]],  1)
    mgr.init_book("ETHUSDT", [[3010.0, 50.0]], [[3000.0, 50.0]], 1)

    # 3. Состояние (им управляет Telegram): сумма 1000, порог 0.1%
    state = RuntimeState(mode="dry", amount_usdt=1000.0, threshold_pct=0.1)

    # 4. Куда уходят сигналы (пока — просто счётчик + лог)
    caught: list[Signal] = []
    def on_signal(sig: Signal) -> None:
        caught.append(sig)

    metrics = Metrics()
    scanner = Scanner(
        index=index,
        book_provider=lambda: mgr.snapshot_books(),
        state=state,
        fee_provider=lambda s: 0.0,
        anomaly_pct=3.0,
        on_signal=on_signal,
        metrics=metrics,
    )

    # 5. Прогоняем серию обновлений ETHUSDT с разной bid-ценой:
    #    чем выше bid на продаже ETH, тем выгоднее связка USDT->BTC->ETH->USDT.
    print("\n=== Серия обновлений ETHUSDT (реактивный пересчёт) ===")
    for i in range(2000):
        bid = 3000.0 + random.uniform(0, 30)      # плавающая цена
        mgr.init_book("ETHUSDT", [[3010.0, 50.0]], [[bid, 50.0]], 1)
        scanner.on_symbol_update("ETHUSDT")       # триггерим пересчёт связок ETHUSDT

    print(f"поймано сигналов (net > {state.threshold_pct}%): {len(caught)}")
    if caught:
        best = max(caught, key=lambda s: s.evaluation.net_pct)
        print(f"лучший сигнал: {best.evaluation.triangle.path}  "
              f"net={best.evaluation.net_pct:+.4f}%")

    # 6. Проверка санити-фильтра: подсунем нереально выгодную книгу
    print("\n=== Проверка санити-фильтра (аномалия) ===")
    mgr.init_book("ETHUSDT", [[3010.0, 50.0]], [[3500.0, 50.0]], 1)  # +16% — явно битьё
    before = len(caught)
    scanner.on_symbol_update("ETHUSDT")
    print(f"сигналов добавилось: {len(caught) - before} (ожидаем 0 — аномалия отфильтрована)")

    # 7. Метрики скорости
    print("\n=== Скорость пересчёта ===")
    print("  ", scanner.metrics_report())


if __name__ == "__main__":
    main()