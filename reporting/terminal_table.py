"""
reporting/terminal_table.py — вывод живой таблицы связок в терминал.

Использует библиотеку rich (красивая авто-обновляемая таблица). Импорт rich
защищён: если библиотека не установлена, работает простой текстовый фолбэк —
так модуль не ломается в средах без rich.
"""

from __future__ import annotations
from typing import Optional

from reporting.snapshot import Row
from infra.logging_conf import get_logger

log = get_logger("RPT")

# Защищённый импорт rich — при отсутствии используем текстовый фолбэк
try:
    from rich.table import Table
    from rich.console import Console
    _RICH_OK = True
    _console = Console()
except Exception:                        # noqa: BLE001
    _RICH_OK = False
    _console = None


def _fmt(value: Optional[float], spec: str) -> str:
    """Форматирует число либо возвращает прочерк для None."""
    return format(value, spec) if value is not None else "—"


def build_table(rows: list[Row], mode: str, amount: float,
                threshold: float, top: int = 15):
    """
    Строит rich-таблицу из строк снапшота (топ-N по спреду).
    Возвращает объект Table (для rich.Live) или None, если rich недоступен.
    """
    if not _RICH_OK:
        return None

    title = (f"MEXC арбитраж — режим {mode} | "
             f"сумма {amount:.0f} USDT | порог {threshold:.3f}%")
    table = Table(title=title, expand=True)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Связка")
    table.add_column("Тип", justify="center")
    table.add_column("Нетто %", justify="right")
    table.add_column("Брутто %", justify="right")
    table.add_column("Результат", justify="right")
    table.add_column("Статус")

    for r in rows[:top]:
        # Цвет строки: зелёный — прошла порог, красный — аномалия/нет ликвидности
        if r.net_pct is not None and r.net_pct > threshold and r.status == "норм":
            style = "green"
        elif r.status == "норм":
            style = "yellow"
        else:
            style = "red"

        table.add_row(
            str(r.rank),
            r.path,
            r.pattern,
            _fmt(r.net_pct, "+.4f"),
            _fmt(r.gross_pct, "+.4f"),
            _fmt(r.result, ".4f"),
            r.status,
            style=style,
        )
    return table


def render_once(rows: list[Row], mode: str, amount: float,
                threshold: float, top: int = 15) -> None:
    """
    Разовый вывод таблицы в терминал (для демо и отладки).
    Если rich есть — печатает красивую таблицу, иначе — простой текст.
    """
    if _RICH_OK:
        table = build_table(rows, mode, amount, threshold, top)
        _console.print(table)
        return

    # Текстовый фолбэк без rich
    print(f"\n=== MEXC арбитраж — {mode} | сумма {amount:.0f} | порог {threshold:.3f}% ===")
    print(f"{'#':>3}  {'Связка':<32} {'Тип':<4} {'Нетто %':>9} {'Брутто %':>9} "
          f"{'Результат':>11}  Статус")
    for r in rows[:top]:
        print(f"{r.rank:>3}  {r.path:<32} {r.pattern:<4} "
              f"{_fmt(r.net_pct, '+.4f'):>9} {_fmt(r.gross_pct, '+.4f'):>9} "
              f"{_fmt(r.result, '.4f'):>11}  {r.status}")