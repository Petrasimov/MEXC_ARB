"""
engine/scanner.py — реактивный сканер треугольников.

Ядро «горячего пути»: на каждое обновление книги пересчитывает ТОЛЬКО те
треугольники, где участвует обновившийся символ (через индекс symbol→треугольники),
а не все подряд. В этом скорость.

Шаги на одно обновление:
  1. По символу берём затронутые треугольники из индекса.
  2. Считаем net-спред каждого через VWAP на текущую сумму (ядро core.spread).
  3. Санити-фильтр: аномальный спред = битые/устаревшие данные, отбрасываем.
  4. Если net > порога — формируем сигнал и отдаём в колбдэк (пока это лог).
  5. Замеряем длительность пересчёта (метрики p50/p95/p99).

Сумму, порог и режим сканер читает из RuntimeState атомарно — их правит Telegram.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from core.models import Triangle, Evaluation
from core.spread import evaluate_triangle
from infra.config import RuntimeState
from infra.metrics import Metrics, Timer
from infra.logging_conf import get_logger

log = get_logger("SCAN")


@dataclass
class Signal:
    """Сформированный торговый сигнал по прошедшему порог треугольнику."""
    evaluation: Evaluation
    amount_usdt: float
    threshold_pct: float


# Колбэк на сигнал: сюда уходит сигнал (пока — лог, на вехе 6 — исполнитель).
SignalHandler = Callable[[Signal], None]

# Провайдер комиссий: symbol -> ставка тейкера (доля).
FeeProvider = Callable[[str], float]


class Scanner:
    """Реактивный сканер. Держит индекс, книги и состояние; выдаёт сигналы."""

    def __init__(
        self,
        index: dict,                      # symbol -> [Triangle]
        book_provider: Callable[[], dict],  # () -> {symbol: {'asks','bids'}}
        state: RuntimeState,
        fee_provider: FeeProvider,
        anomaly_pct: float,
        on_signal: Optional[SignalHandler] = None,
        metrics: Optional[Metrics] = None,
    ):
        self._index = index
        self._books = book_provider
        self._state = state
        self._fee = fee_provider
        self._anomaly_pct = anomaly_pct
        self._on_signal = on_signal
        self._metrics = metrics or Metrics()

    def on_symbol_update(self, symbol: str) -> list[Signal]:
        """
        Реакция на обновление книги символа: пересчитывает затронутые связки.
        Возвращает список сигналов (и передаёт их в колбэк, если задан).
        """
        triangles = self._index.get(symbol)
        if not triangles:
            return []                      # символ не участвует ни в одной связке

        with Timer(self._metrics, "scan"):     # замер длительности пересчёта
            signals = self._evaluate(triangles)

        for sig in signals:
            if self._on_signal is not None:
                self._on_signal(sig)
        return signals

    def _evaluate(self, triangles: list[Triangle]) -> list[Signal]:
        """Оценивает связки, применяет санити-фильтр и порог, собирает сигналы."""
        books = self._books()
        amount = self._state.amount_usdt
        threshold = self._state.threshold_pct
        signals: list[Signal] = []

        for tri in triangles:
            ev = evaluate_triangle(tri, books, amount, self._fee)
            if not ev.executable or ev.net_pct is None:
                continue                   # не хватило глубины — пропуск

            # Санити-фильтр: слишком «хороший» спред почти всегда битые данные
            if ev.net_pct >= self._anomaly_pct:
                log.warning("аномалия (битые данные?): %s net=%.4f%% — пропуск",
                            tri.symbols, ev.net_pct)
                continue

            if ev.net_pct > threshold:
                log.info("СИГНАЛ %s net=%.4f%% (порог %.4f%%, сумма %.2f)",
                         tri.path, ev.net_pct, threshold, amount)
                signals.append(Signal(ev, amount, threshold))

        return signals

    def metrics_report(self) -> str:
        """Строка со статистикой скорости пересчёта (для /stats и терминала)."""
        return self._metrics.report("scan")