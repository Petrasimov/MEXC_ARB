"""
engine/risk.py — риск-модуль: проверки перед каждой сделкой.

Задача — не дать боту навредить: ограничить сумму, частоту и потери. Все проверки
собраны в одном месте, исполнитель спрашивает разрешение перед КАЖДОЙ сделкой.

Проверки:
  - максимальная сумма на сделку;
  - кулдаун (минимальная пауза между сделками);
  - дневной лимит числа сделок;
  - дневной лимит потерь (при просадке — аварийная остановка на день);
  - аварийный «стоп» (halt), который может выставить исполнитель при сбое.

Состояние (счётчики дня) сбрасывается автоматически при смене календарного дня.
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import date

from infra.logging_conf import get_logger

log = get_logger("EXEC")


@dataclass
class RiskLimits:
    """Границы риска. Задаются из конфига, не хардкодятся в логике."""
    max_amount_usdt: float = 1000.0      # максимум на одну связку
    cooldown_sec: float = 5.0            # пауза между сделками
    max_trades_per_day: int = 50         # дневной лимит числа сделок
    daily_loss_limit_usdt: float = 50.0  # стоп на день при такой суммарной просадке


class RiskManager:
    """Хранит счётчики дня и решает, можно ли исполнять очередной сигнал."""

    def __init__(self, limits: RiskLimits):
        self._limits = limits
        self._last_trade_ts = 0.0
        self._trades_today = 0
        self._pnl_today = 0.0
        self._day = date.today()
        self._halted = False
        self._halt_reason = ""

    def check(self, amount_usdt: float) -> tuple[bool, str]:
        """
        Разрешить сделку на amount_usdt? Возвращает (можно, причина).
        Причина полезна для лога и Telegram, даже когда можно ('ok').
        """
        self._rollover_day()

        if self._halted:
            return False, f"аварийная остановка: {self._halt_reason}"
        if amount_usdt > self._limits.max_amount_usdt:
            return False, (f"сумма {amount_usdt:.2f} > лимита "
                           f"{self._limits.max_amount_usdt:.2f}")
        wait = self._limits.cooldown_sec - (time.time() - self._last_trade_ts)
        if wait > 0:
            return False, f"кулдаун ещё {wait:.1f}с"
        if self._trades_today >= self._limits.max_trades_per_day:
            return False, f"дневной лимит сделок ({self._limits.max_trades_per_day}) исчерпан"
        if self._pnl_today <= -self._limits.daily_loss_limit_usdt:
            self.halt("достигнут дневной лимит потерь")
            return False, self._halt_reason
        return True, "ok"

    def register_trade(self, pnl_usdt: float) -> None:
        """Фиксирует состоявшуюся сделку и её результат (PnL в USDT)."""
        self._last_trade_ts = time.time()
        self._trades_today += 1
        self._pnl_today += pnl_usdt
        log.info("сделка учтена: PnL=%.4f, за день=%.4f, сделок=%d",
                 pnl_usdt, self._pnl_today, self._trades_today)
        if self._pnl_today <= -self._limits.daily_loss_limit_usdt:
            self.halt("достигнут дневной лимит потерь")

    def halt(self, reason: str) -> None:
        """Аварийная остановка: до ручного сброса сделки запрещены."""
        if not self._halted:
            self._halted = True
            self._halt_reason = reason
            log.error("РИСК-СТОП: %s", reason)

    def reset_halt(self) -> None:
        """Ручной сброс аварийной остановки (например, из Telegram)."""
        self._halted = False
        self._halt_reason = ""
        log.info("аварийная остановка снята")

    def status(self) -> dict:
        """Текущее состояние риск-модуля (для статуса/лога)."""
        self._rollover_day()
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "trades_today": self._trades_today,
            "pnl_today": self._pnl_today,
        }

    def _rollover_day(self) -> None:
        """Сбрасывает дневные счётчики при наступлении нового дня."""
        today = date.today()
        if today != self._day:
            self._day = today
            self._trades_today = 0
            self._pnl_today = 0.0
            log.info("новый день — дневные счётчики риска сброшены")