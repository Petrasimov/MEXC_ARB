"""
reporting/reporter.py — общий репортёр (терминал + Google Sheets).

Раз в interval секунд строит снапшот всех связок через build_rows и выводит его
в два места: живую rich-таблицу в терминале и (если подключён) в Google Sheets.
Читает сумму/порог/режим из RuntimeState — их правит Telegram-пульт (веха 5).

Работает как async-задача рядом с WS-клиентом. Запись в Google Sheets — блокирующий
сетевой вызов, поэтому выполняется в отдельном потоке (run_in_executor), чтобы не
тормозить event loop и приём котировок.
"""

from __future__ import annotations
import asyncio
from typing import Callable, Optional

from reporting.snapshot import build_rows, Row
from reporting import terminal_table
from reporting.sheets import SheetsReporter
from infra.config import RuntimeState
from infra.logging_conf import get_logger

log = get_logger("RPT")

# Провайдеры: книги и комиссии
BookProvider = Callable[[], dict]
FeeProvider = Callable[[str], float]


class Reporter:
    """Периодически публикует снапшот связок в терминал и Google Sheets."""

    def __init__(
        self,
        triangles: list,
        book_provider: BookProvider,
        state: RuntimeState,
        fee_provider: FeeProvider,
        anomaly_pct: float,
        sheets: Optional[SheetsReporter] = None,
        interval: float = 1.5,
        show_terminal: bool = True,
    ):
        self._triangles = triangles
        self._books = book_provider
        self._state = state
        self._fee = fee_provider
        self._anomaly_pct = anomaly_pct
        self._sheets = sheets
        self._interval = interval
        self._show_terminal = show_terminal
        self._running = False

    def build_snapshot(self) -> list[Row]:
        """Собирает текущий снапшот строк (для вывода или теста)."""
        return build_rows(
            self._triangles, self._books(),
            self._state.amount_usdt, self._fee, self._anomaly_pct,
        )

    def set_triangles(self, triangles: list) -> None:
        """Обновляет набор отображаемых треугольников (вызывает ресканер)."""
        self._triangles = triangles

    async def run(self) -> None:
        """Основной цикл репортёра. Останавливается через stop()."""
        self._running = True
        # rich.Live для живой перерисовки одной таблицы (если rich доступен)
        live = self._make_live()
        try:
            if live is not None:
                live.__enter__()
            while self._running:
                rows = self.build_snapshot()
                self._publish_terminal(rows, live)
                await self._publish_sheets(rows)
                await asyncio.sleep(self._interval)
        finally:
            if live is not None:
                live.__exit__(None, None, None)

    def stop(self) -> None:
        """Останавливает цикл репортёра."""
        self._running = False

    # ── Вспомогательное ──────────────────────────────────────────────────────

    def _make_live(self):
        """Создаёт rich.Live, если rich установлен; иначе None (текстовый режим)."""
        if not self._show_terminal or not terminal_table._RICH_OK:
            return None
        try:
            from rich.live import Live
            return Live(auto_refresh=False)
        except Exception:                            # noqa: BLE001
            return None

    def _publish_terminal(self, rows: list[Row], live) -> None:
        """Обновляет терминальный вывод (rich.Live или текстовый фолбэк)."""
        if not self._show_terminal:
            return
        table = terminal_table.build_table(
            rows, self._state.mode, self._state.amount_usdt, self._state.threshold_pct,
        )
        if live is not None and table is not None:
            live.update(table, refresh=True)
        else:
            # Фолбэк без rich: печать раз в интервал
            terminal_table.render_once(
                rows, self._state.mode, self._state.amount_usdt, self._state.threshold_pct,
            )

    async def _publish_sheets(self, rows: list[Row]) -> None:
        """Пишет снапшот в Google Sheets в отдельном потоке (не блокируя loop)."""
        if self._sheets is None or not self._sheets.enabled:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._sheets.write, rows)
        except Exception as e:                       # noqa: BLE001
            log.error("ошибка публикации в Google Sheets: %s", e)