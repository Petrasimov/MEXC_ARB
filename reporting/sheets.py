"""
reporting/sheets.py — запись снапшота в Google Sheets (общая витрина для команды).

Использует gspread + сервисный аккаунт Google Cloud. Пишет всю таблицу одним
батч-запросом (одна запись = один вызов API), чтобы укладываться в лимиты
Google Sheets (~60 запросов/мин на пользователя). Репортёр вызывает write()
раз в 1-2 секунды.

Колонки соответствуют шаблону arbitrage_monitor_template.xlsx.
Импорт gspread защищён: без библиотеки/ключей модуль работает в режиме
«отключено» и просто логирует это — удобно для разработки без облака.
"""

from __future__ import annotations
from typing import Optional

from reporting.snapshot import Row
from infra.logging_conf import get_logger

log = get_logger("RPT")

# Защищённый импорт gspread
try:
    import gspread
    _GS_OK = True
except Exception:                        # noqa: BLE001
    _GS_OK = False

# Заголовок таблицы — порядок колонок как в xlsx-шаблоне
HEADER = [
    "#", "Связка", "Тип", "Нога 1", "Нога 2", "Нога 3",
    "Цена 1", "Цена 2", "Цена 3", "Результат",
    "Спред нетто %", "Спред брутто %", "Комиссии %",
    "Исполнимо", "Статус", "Обновлено",
]


def _num(value: Optional[float]) -> str:
    """Число в строку для ячейки (пусто для None)."""
    return "" if value is None else repr(value)


def _row_to_cells(r: Row) -> list:
    """Преобразует Row в список ячеек в порядке HEADER."""
    p = (r.prices + [None, None, None])[:3]        # гарантируем 3 цены
    legs = (r.leg_labels + ["", "", ""])[:3]
    return [
        r.rank, r.path, r.pattern,
        legs[0], legs[1], legs[2],
        _num(p[0]), _num(p[1]), _num(p[2]),
        _num(r.result),
        _num(r.net_pct), _num(r.gross_pct), _num(r.fees_pct),
        "да" if r.executable else "нет",
        r.status, r.updated_at,
    ]


class SheetsReporter:
    """Пишет снапшот в лист Google Sheets. Ленивое подключение при первой записи."""

    def __init__(self, sheet_id: str, sa_json: str, worksheet: str = "Мониторинг"):
        self._sheet_id = sheet_id
        self._sa_json = sa_json
        self._ws_name = worksheet
        self._ws = None
        # Включён, только если есть библиотека, id таблицы и путь к ключу
        self.enabled = bool(sheet_id and sa_json and _GS_OK)
        if not self.enabled:
            log.warning("Google Sheets отключён (нет gspread/ключа/ID) — работаем без витрины")

    def connect(self) -> bool:
        """Подключается к таблице и выбирает лист. Возвращает успех."""
        if not self.enabled:
            return False
        try:
            gc = gspread.service_account(filename=self._sa_json)
            sh = gc.open_by_key(self._sheet_id)
            self._ws = sh.worksheet(self._ws_name)
            log.info("Google Sheets подключён: лист '%s'", self._ws_name)
            return True
        except Exception as e:                       # noqa: BLE001
            log.error("не удалось подключиться к Google Sheets: %s", e)
            self.enabled = False
            return False

    def write(self, rows: list[Row]) -> None:
        """Пишет весь снапшот одним батч-запросом (заголовок + строки)."""
        if not self.enabled:
            return
        if self._ws is None and not self.connect():
            return

        values = [HEADER]
        for r in rows:
            values.append(_row_to_cells(r))

        try:
            # Одна запись всей таблицы с A1 — минимум обращений к API
            self._ws.update(values, "A1", value_input_option="RAW")
        except Exception as e:                       # noqa: BLE001
            log.error("ошибка записи в Google Sheets: %s", e)

    def build_values(self, rows: list[Row]) -> list:
        """Возвращает 2D-массив значений (для тестов/отладки без сети)."""
        return [HEADER] + [_row_to_cells(r) for r in rows]