"""
infra/logging_conf.py — единая настройка логирования.

Каждая запись помечается тегом этапа (WS, BOOK, SCAN, EXEC, TG, REST, PAIRS...),
чтобы при ошибке было сразу видно, на каком шаге скрипта она произошла.

Использование:
    from infra.logging_conf import setup_logging, get_logger
    setup_logging()                 # один раз при старте программы
    log = get_logger("PAIRS")       # логгер конкретного этапа
    log.info("отобрано %d пар", n)
"""

from __future__ import annotations
import logging
import sys

# Формат: время | УРОВЕНЬ | [ЭТАП] | сообщение
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | [%(stage)s] | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Значение тега этапа по умолчанию (если логгер создан без явного этапа)
_DEFAULT_STAGE = "APP"


class _StageFilter(logging.Filter):
    """Фильтр, который проставляет поле stage, если его нет в записи."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "stage"):
            record.stage = _DEFAULT_STAGE
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает корневой логгер: вывод в терминал, единый формат."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_StageFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()          # убираем возможные дубли хендлеров
    root.addHandler(handler)


def get_logger(stage: str) -> logging.LoggerAdapter:
    """
    Возвращает логгер, который автоматически проставляет тег этапа.
    stage — короткий тег: 'WS', 'BOOK', 'SCAN', 'EXEC', 'TG', 'REST', 'PAIRS'.
    """
    base = logging.getLogger(f"mexc_arb.{stage}")
    return logging.LoggerAdapter(base, {"stage": stage})
