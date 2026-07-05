"""
scripts/demo_book_manager.py — офлайн-проверка вехи 2 (без сети).

Запуск:
    python -m scripts.demo_book_manager

Проверяет:
  1. Инициализацию книги снапшотом.
  2. Применение корректных инкрементов (абсолютный объём, удаление уровня).
  3. Обнаружение разрыва версий и авто-пересинхронизацию через колбэк.
  4. Интеграцию: книга -> VWAP-расчёт спреда (связка с ядром).
"""

from __future__ import annotations
import asyncio

from infra.logging_conf import setup_logging, get_logger
from connectors.depth_decoder import decode_dict
from engine.book_manager import BookManager
from core.models import Pair, Leg, Triangle
from core.spread import evaluate_triangle

log = get_logger("DEMO")


async def main() -> None:
    setup_logging()

    symbol = "BTCUSDT"

    # Колбэк пересинхронизации: имитируем REST-снапшот (в реале — поход в /api/v3/depth)
    async def fake_resync(sym: str):
        log.info("%s: fake_resync выдал свежий снапшот", sym)
        return ([[60000.0, 2.0]], [[60010.0, 2.0]], 1000)   # bids, asks, version

    mgr = BookManager([symbol], resync_cb=fake_resync)

    # 1. Снапшот
    mgr.init_book(symbol, bids=[[60000.0, 1.0]], asks=[[60010.0, 1.0]], version=100)

    # 2. Корректный инкремент: version 100 -> 101, добавляем уровень и меняем объём
    upd = decode_dict({
        "symbol": symbol,
        "bids": [[59990.0, 3.0]],           # новый уровень
        "asks": [[60010.0, 0.5]],           # заменяем объём (абсолютный!)
        "fromVersion": 101, "toVersion": 101,
    })
    await mgr.on_update(upd)
    print("\n=== После корректного инкремента ===")
    print("  ", mgr.book_for(symbol))

    # 3. Удаление уровня: объём 0 убирает ценовой уровень
    upd = decode_dict({
        "symbol": symbol,
        "bids": [[59990.0, 0.0]],           # удаляем ранее добавленный уровень
        "asks": [],
        "fromVersion": 102, "toVersion": 102,
    })
    await mgr.on_update(upd)
    print("\n=== После удаления уровня (объём 0) ===")
    print("  ", mgr.book_for(symbol))

    # 4. РАЗРЫВ версий: ожидаем 103, а пришло from=105 -> должна сработать resync
    upd = decode_dict({
        "symbol": symbol,
        "bids": [[59980.0, 1.0]], "asks": [],
        "fromVersion": 105, "toVersion": 105,
    })
    await mgr.on_update(upd)
    print("\n=== После разрыва версий -> пересинхронизация ===")
    print("  ", mgr.book_for(symbol))

    # 5. Интеграция с ядром: считаем спред простого треугольника на живых книгах.
    #    Соберём три книги и один треугольник USDT->BTC->ETH->USDT.
    mgr2 = BookManager(["BTCUSDT", "ETHBTC", "ETHUSDT"])
    mgr2.init_book("BTCUSDT", [[60000.0, 5.0]], [[60000.0, 5.0]], 1)
    mgr2.init_book("ETHBTC",  [[0.05, 100.0]],  [[0.05, 100.0]],  1)
    mgr2.init_book("ETHUSDT", [[3010.0, 50.0]], [[3000.0, 50.0]], 1)

    triangle = Triangle("USDT", (
        Leg("BTCUSDT", "BUY",  "asks", "USDT", "BTC"),
        Leg("ETHBTC",  "BUY",  "asks", "BTC",  "ETH"),
        Leg("ETHUSDT", "SELL", "bids", "ETH",  "USDT"),
    ))
    books = mgr2.snapshot_books()
    ev = evaluate_triangle(triangle, books, start_amount=1000.0, fee_provider=lambda s: 0.0)
    print("\n=== Интеграция книги -> VWAP-спред ===")
    print(f"  связка: {ev.triangle}")
    print(f"  gross: {ev.gross_pct:+.4f}%   результат: {ev.final_amount:.4f} USDT")


if __name__ == "__main__":
    asyncio.run(main())
