"""
scripts/demo_executor.py — офлайн-проверка вехи 6 (без сети, без ордеров).

Запуск:
    python -m scripts.demo_executor

Проверяет БЕЗ реальных ордеров:
  1. Построение плана сделки из сигнала (3 ноги: цены с запасом, объёмы).
  2. Dry-режим: план логируется, ордера НЕ ставятся.
  3. Риск-модуль: лимит суммы, кулдаун, дневной лимит сделок.
"""

from __future__ import annotations
import asyncio

from infra.logging_conf import setup_logging, get_logger
from infra.config import RuntimeState
from engine.book_manager import BookManager
from engine.risk import RiskManager, RiskLimits
from engine.executor import Executor
from engine.scanner import Signal
from core.models import Pair
from core.triangles import MarketGraph
from core.spread import evaluate_triangle

log = get_logger("DEMO")


def _make_signal() -> Signal:
    """Готовит один выгодный сигнал на синтетических книгах."""
    pairs = [Pair("BTCUSDT", "BTC", "USDT"),
             Pair("ETHBTC", "ETH", "BTC"),
             Pair("ETHUSDT", "ETH", "USDT")]
    tri = MarketGraph(pairs).build_triangles(["USDT"])[0]
    mgr = BookManager([p.symbol for p in pairs])
    mgr.init_book("BTCUSDT", [[60000.0, 5.0]], [[60000.0, 5.0]], 1)
    mgr.init_book("ETHBTC",  [[0.05, 100.0]],  [[0.05, 100.0]],  1)
    mgr.init_book("ETHUSDT", [[3025.0, 50.0]], [[3000.0, 50.0]], 1)
    ev = evaluate_triangle(tri, mgr.snapshot_books(), 1000.0, lambda s: 0.0)
    return Signal(evaluation=ev, amount_usdt=1000.0, threshold_pct=0.1)


async def main() -> None:
    setup_logging()
    state = RuntimeState(mode="dry", amount_usdt=1000.0, threshold_pct=0.1)
    risk = RiskManager(RiskLimits(max_amount_usdt=5000.0, cooldown_sec=0.0,
                                  max_trades_per_day=3, daily_loss_limit_usdt=50.0))
    # rest=None: в dry-режиме сеть не нужна
    executor = Executor(rest=None, state=state, risk=risk, slippage_pct=0.05)

    signal = _make_signal()

    # 1-2. Dry-режим: строим план и логируем, ордера не ставятся
    print("\n=== DRY: план сделки (ордера НЕ ставятся) ===")
    report = await executor.handle_signal(signal)
    print(f"  результат: ok={report.ok}, режим={report.mode}, ног={len(report.legs)}")
    for i, leg in enumerate(report.legs, 1):
        print(f"    нога {i}: {leg.side} {leg.symbol} qty={leg.quantity:.6f} "
              f"price={leg.price:.4f}")

    # 3. Риск-модуль: превышение суммы
    print("\n=== Риск: превышение лимита суммы ===")
    big = Signal(evaluation=signal.evaluation, amount_usdt=999999.0, threshold_pct=0.1)
    r = await executor.handle_signal(big)
    print(f"  сигнал на 999999 USDT: {'пропущен' if r is None else 'прошёл'} (ожидаем пропущен)")

    # 3b. Риск: дневной лимит сделок (регистрируем сделки вручную)
    print("\n=== Риск: дневной лимит сделок (лимит 3) ===")
    for i in range(4):
        allow, reason = risk.check(1000.0)
        print(f"  попытка {i+1}: {'разрешено' if allow else 'запрещено — ' + reason}")
        if allow:
            risk.register_trade(pnl_usdt=1.0)

    print("\n=== Статус риск-модуля ===")
    print("  ", risk.status())


if __name__ == "__main__":
    asyncio.run(main())