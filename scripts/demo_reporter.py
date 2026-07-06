"""
scripts/demo_reporter.py — офлайн-проверка вехи 4 (без сети).

Запуск:
    python -m scripts.demo_reporter

Проверяет:
  1. Построение снапшота строк из треугольников и книг.
  2. Терминальный вывод (rich-таблица или текстовый фолбэк без rich).
  3. Формирование 2D-массива для Google Sheets (в режиме «отключено», без сети).
"""

from __future__ import annotations

from infra.logging_conf import setup_logging, get_logger
from infra.config import RuntimeState
from engine.book_manager import BookManager
from core.models import Pair
from core.triangles import MarketGraph
from reporting.snapshot import build_rows
from reporting import terminal_table
from reporting.sheets import SheetsReporter

log = get_logger("DEMO")


def main() -> None:
    setup_logging()

    # 1. Набор пар и треугольники (как после отбора вехи 1)
    pairs = [
        Pair("BTCUSDT", "BTC", "USDT"),
        Pair("ETHBTC",  "ETH", "BTC"),
        Pair("ETHUSDT", "ETH", "USDT"),
        Pair("SOLUSDT", "SOL", "USDT"),
        Pair("SOLBTC",  "SOL", "BTC"),
    ]
    triangles = MarketGraph(pairs).build_triangles(["USDT"])

    # 2. Книги через менеджер (веха 2). Одна связка сделана выгодной.
    mgr = BookManager([p.symbol for p in pairs])
    mgr.init_book("BTCUSDT", [[60000.0, 5.0]], [[60000.0, 5.0]], 1)
    mgr.init_book("ETHBTC",  [[0.05, 100.0]],  [[0.05, 100.0]],  1)
    mgr.init_book("ETHUSDT", [[3025.0, 50.0]], [[3000.0, 50.0]], 1)  # +спред
    mgr.init_book("SOLUSDT", [[150.0, 200.0]], [[150.0, 200.0]], 1)
    mgr.init_book("SOLBTC",  [[0.0025, 500.0]], [[0.0025, 500.0]], 1)

    # 3. Состояние (правит Telegram на вехе 5)
    state = RuntimeState(mode="dry", amount_usdt=1000.0, threshold_pct=0.1)

    # 4. Снапшот
    rows = build_rows(
        triangles, mgr.snapshot_books(),
        amount=state.amount_usdt, fee_provider=lambda s: 0.0, anomaly_pct=3.0,
    )

    # 5. Терминальный вывод (rich или текстовый фолбэк)
    terminal_table.render_once(rows, state.mode, state.amount_usdt, state.threshold_pct)

    # 6. Google Sheets в режиме «отключено» (ключей нет) — проверяем сборку значений
    sheets = SheetsReporter(sheet_id="", sa_json="", worksheet="Мониторинг")
    values = sheets.build_values(rows)
    print("\n=== Значения для Google Sheets (первые 3 строки) ===")
    for line in values[:3]:
        print("  ", line)
    print(f"  ... всего строк с заголовком: {len(values)}")
    print(f"  Google Sheets включён: {sheets.enabled} (ожидаемо False без ключей)")


if __name__ == "__main__":
    main()