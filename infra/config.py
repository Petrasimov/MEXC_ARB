"""
infra/config.py — конфигурация бота.

Все настройки в одном месте. Секреты (ключи API) читаются из переменных
окружения и НИКОГДА не хранятся в коде. Изменяемые в рантайме параметры
(режим, сумма, порог, вкл/выкл) вынесены в RuntimeState — их правит Telegram,
а движок читает атомарно на каждой итерации.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field

# ── Константы MEXC ───────────────────────────────────────────────────────────
MEXC_REST_BASE = "https://api.mexc.com"          # база REST API
MEXC_WS_BASE = "wss://wbs-api.mexc.com/ws"        # база WebSocket

# ── Значения по умолчанию (можно переопределить через окружение) ─────────────
DEFAULT_MODE = "dry"          # 'dry' (без ордеров) или 'live'
DEFAULT_AMOUNT_USDT = 100.0   # стартовая сумма связки
DEFAULT_THRESHOLD_PCT = 0.2   # порог net-спреда в процентах
DEFAULT_TOP_N = 30            # сколько топ-ликвидных альтов брать
DEFAULT_TAKER_FEE = 0.0       # ставка тейкера по умолчанию (MEXC часто 0%)
ANOMALY_PCT = 3.0             # спред выше этого % считаем аномалией (битые данные)

# Стартовый актив (база капитала) и мосты для треугольников
START_ASSETS = ["USDT"]
BRIDGES = ["USDC", "BTC", "ETH"]


@dataclass(frozen=True)
class Config:
    """Неизменяемая часть конфигурации (задаётся при старте)."""
    api_key: str = ""
    api_secret: str = ""
    rest_base: str = MEXC_REST_BASE
    ws_base: str = MEXC_WS_BASE
    top_n: int = DEFAULT_TOP_N
    default_taker_fee: float = DEFAULT_TAKER_FEE
    anomaly_pct: float = ANOMALY_PCT
    start_assets: tuple = tuple(START_ASSETS)
    bridges: tuple = tuple(BRIDGES)

    @classmethod
    def from_env(cls) -> "Config":
        """Собирает конфиг из переменных окружения (с дефолтами)."""
        return cls(
            api_key=os.getenv("MEXC_API_KEY", ""),
            api_secret=os.getenv("MEXC_API_SECRET", ""),
            top_n=int(os.getenv("ARB_TOP_N", DEFAULT_TOP_N)),
            default_taker_fee=float(os.getenv("ARB_TAKER_FEE", DEFAULT_TAKER_FEE)),
        )


@dataclass
class RuntimeState:
    """Изменяемое в рантайме состояние — им управляет Telegram-пульт."""
    mode: str = DEFAULT_MODE                 # 'dry' | 'live'
    amount_usdt: float = DEFAULT_AMOUNT_USDT  # сумма связки
    threshold_pct: float = DEFAULT_THRESHOLD_PCT  # порог в %
    running: bool = False                    # запущен ли мониторинг

    def snapshot(self) -> dict:
        """Копия состояния для показа в статусе (Telegram/таблица)."""
        return {
            "mode": self.mode,
            "amount_usdt": self.amount_usdt,
            "threshold_pct": self.threshold_pct,
            "running": self.running,
        }
