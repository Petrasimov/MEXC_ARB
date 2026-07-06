"""
infra/metrics.py — замер скорости работы по этапам.

Простой сборщик таймингов: копит длительности операций и отдаёт перцентили
(p50/p95/p99). Нужен, чтобы видеть реальную задержку детекции сигналов, а не
«среднее по больнице» — всплески p99 сразу видны.

Использование:
    from infra.metrics import Timer, Metrics
    metrics = Metrics()
    with Timer(metrics, "scan"):
        ...  # замеряемый блок
    print(metrics.report("scan"))
"""

from __future__ import annotations
import time
from collections import deque


class Metrics:
    """Хранит замеры по именованным этапам и считает перцентили."""

    def __init__(self, window: int = 5000):
        # По этапу — кольцевой буфер последних длительностей (в миллисекундах)
        self._samples: dict[str, deque] = {}
        self._window = window

    def add(self, stage: str, ms: float) -> None:
        """Добавляет один замер длительности (мс) для этапа."""
        buf = self._samples.get(stage)
        if buf is None:
            buf = deque(maxlen=self._window)
            self._samples[stage] = buf
        buf.append(ms)

    def percentiles(self, stage: str) -> dict:
        """Считает p50/p95/p99, среднее и количество замеров этапа."""
        buf = self._samples.get(stage)
        if not buf:
            return {"n": 0}
        data = sorted(buf)
        n = len(data)

        def pct(p: float) -> float:
            # Индекс перцентиля (nearest-rank), защищённый от выхода за границы
            idx = min(n - 1, int(p / 100.0 * n))
            return data[idx]

        return {
            "n": n,
            "p50": pct(50),
            "p95": pct(95),
            "p99": pct(99),
            "avg": sum(data) / n,
            "max": data[-1],
        }

    def report(self, stage: str) -> str:
        """Человекочитаемая строка со статистикой этапа (для логов/статуса)."""
        s = self.percentiles(stage)
        if s.get("n", 0) == 0:
            return f"{stage}: нет замеров"
        return (f"{stage}: n={s['n']} "
                f"p50={s['p50']:.3f}мс p95={s['p95']:.3f}мс "
                f"p99={s['p99']:.3f}мс max={s['max']:.3f}мс")


class Timer:
    """Контекст-менеджер: замеряет время блока и кладёт в Metrics."""

    def __init__(self, metrics: Metrics, stage: str):
        self._metrics = metrics
        self._stage = stage
        self._start = 0.0

    def __enter__(self) -> "Timer":
        # perf_counter_ns — монотонный таймер высокого разрешения
        self._start = time.perf_counter_ns()
        return self

    def __exit__(self, *exc) -> None:
        elapsed_ms = (time.perf_counter_ns() - self._start) / 1_000_000.0
        self._metrics.add(self._stage, elapsed_ms)