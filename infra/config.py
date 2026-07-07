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

# ── Параметры риска и исполнения (веха 6) ────────────────────────────────────
DEFAULT_SLIPPAGE_PCT = 0.05          # запас цены для FOK, % (чтобы нога исполнилась)
DEFAULT_RISK_MAX_AMOUNT = 1000.0     # максимум на одну связку, USDT
DEFAULT_RISK_COOLDOWN_SEC = 5.0      # пауза между сделками, сек
DEFAULT_RISK_MAX_TRADES_DAY = 50     # дневной лимит числа сделок
DEFAULT_RISK_DAILY_LOSS = 50.0       # дневной лимит потерь, USDT
DEFAULT_RESCAN_INTERVAL = 3.0        # период холодного пересмотра топа, сек

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
    # Риск и исполнение
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    risk_max_amount: float = DEFAULT_RISK_MAX_AMOUNT
    risk_cooldown_sec: float = DEFAULT_RISK_COOLDOWN_SEC
    risk_max_trades_day: int = DEFAULT_RISK_MAX_TRADES_DAY
    risk_daily_loss: float = DEFAULT_RISK_DAILY_LOSS
    rescan_interval: float = DEFAULT_RESCAN_INTERVAL

    @classmethod
    def from_env(cls) -> "Config":
        """Собирает конфиг из переменных окружения (с дефолтами)."""
        return cls(
            api_key=os.getenv("MEXC_API_KEY", ""),
            api_secret=os.getenv("MEXC_API_SECRET", ""),
            top_n=int(os.getenv("ARB_TOP_N", DEFAULT_TOP_N)),
            default_taker_fee=float(os.getenv("ARB_TAKER_FEE", DEFAULT_TAKER_FEE)),
            slippage_pct=float(os.getenv("ARB_SLIPPAGE_PCT", DEFAULT_SLIPPAGE_PCT)),
            risk_max_amount=float(os.getenv("ARB_RISK_MAX_AMOUNT", DEFAULT_RISK_MAX_AMOUNT)),
            risk_cooldown_sec=float(os.getenv("ARB_RISK_COOLDOWN", DEFAULT_RISK_COOLDOWN_SEC)),
            risk_max_trades_day=int(os.getenv("ARB_RISK_MAX_TRADES", DEFAULT_RISK_MAX_TRADES_DAY)),
            risk_daily_loss=float(os.getenv("ARB_RISK_DAILY_LOSS", DEFAULT_RISK_DAILY_LOSS)),
            rescan_interval=float(os.getenv("ARB_RESCAN_INTERVAL", DEFAULT_RESCAN_INTERVAL)),
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