"""
engine/executor.py — исполнитель сделок (dry-run и live).

Берёт сигнал сканера, спрашивает разрешение у риск-модуля, строит план из 3 ног
и либо логирует его (dry), либо ставит реальные FOK-ордера через REST (live).

Про FOK и честный риск: FOK (fill-or-kill) защищает от ЧАСТИЧНОГО исполнения
одной ноги. Но если нога 1 исполнилась, а нога 2 — нет (FOK убил её), мы остаёмся
с промежуточным активом на руках (например, BTC). Это межноговый риск. MVP-политика
безопасная: при сбое ноги — критический лог, риск-СТОП и остановка до ручного
вмешательства. Авто-раскрутка позиции (unwind) — задача на будущее.

precision/фильтры: реальные price/quantity нужно округлять под фильтры пары
(tickSize, stepSize, minNotional) из exchangeInfo. Здесь — упрощённое округление;
перед live обязательно подключить фильтры пары. В dry это не критично.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from core.models import Triangle
from engine.scanner import Signal
from engine.risk import RiskManager
from infra.config import RuntimeState
from infra.logging_conf import get_logger

log = get_logger("EXEC")

# Тип ордера MEXC для тейкер-ног. ВНИМАНИЕ: точное имя проверить в доках MEXC
# (варианты: 'FILL_OR_KILL' / 'FOK'). Вынесено в константу — легко поменять.
FOK = "FILL_OR_KILL"


@dataclass
class LegPlan:
    """План одной ноги: что и как ставим."""
    symbol: str
    side: str            # BUY | SELL
    price: float         # лимитная цена с запасом на проскальзывание
    quantity: float      # объём в базовом активе
    from_asset: str
    to_asset: str


@dataclass
class ExecReport:
    """Итог обработки сигнала исполнителем."""
    ok: bool
    mode: str                       # 'dry' | 'live'
    legs: list = field(default_factory=list)   # список LegPlan
    note: str = ""


class Executor:
    """Превращает сигнал в действия. Безопасен по умолчанию (dry)."""

    def __init__(self, rest, state: RuntimeState, risk: RiskManager,
                 slippage_pct: float = 0.05):
        self._rest = rest                       # MexcRestClient (или None в dry-тестах)
        self._state = state
        self._risk = risk
        self._slip = slippage_pct / 100.0       # запас цены, доля

    async def handle_signal(self, signal: Signal) -> Optional[ExecReport]:
        """Главная точка: риск-проверка -> план -> dry-лог или live-исполнение."""
        allow, reason = self._risk.check(signal.amount_usdt)
        if not allow:
            log.info("сигнал пропущен риск-модулем: %s", reason)
            return None

        plan = self._build_plan(signal)

        if self._state.mode != "live":
            self._log_plan("DRY (ордера НЕ ставятся)", signal, plan)
            return ExecReport(ok=True, mode="dry", legs=plan, note="dry-run")

        return await self._execute_live(signal, plan)

    def _build_plan(self, signal: Signal) -> list[LegPlan]:
        """Строит план 3 ног из оценки: цены с запасом, объёмы по ногам."""
        ev = signal.evaluation
        tri: Triangle = ev.triangle
        amounts = [signal.amount_usdt] + list(ev.leg_amounts)   # вход каждой ноги

        plan: list[LegPlan] = []
        for i, leg in enumerate(tri.legs):
            inp = amounts[i]            # сколько тратим на входе ноги
            out = ev.leg_amounts[i]     # сколько получаем (после комиссии)
            if leg.side == "BUY":
                # тратим quote(inp), получаем base(out); цена = quote за base
                price = inp / out if out else 0.0
                price *= (1.0 + self._slip)          # платим чуть выше — чтобы FOK исполнился
                qty = out
            else:
                # тратим base(inp), получаем quote(out); цена = quote за base
                price = out / inp if inp else 0.0
                price *= (1.0 - self._slip)          # продаём чуть ниже — чтобы FOK исполнился
                qty = inp
            plan.append(LegPlan(leg.symbol, leg.side, price, qty,
                                leg.from_asset, leg.to_asset))
        return plan

    async def _execute_live(self, signal: Signal, plan: list[LegPlan]) -> ExecReport:
        """Ставит 3 FOK-ноги последовательно. При сбое — стоп и алерт."""
        self._log_plan("LIVE (реальные ордера)", signal, plan)
        for i, leg in enumerate(plan, start=1):
            try:
                resp = await self._rest.place_order(
                    symbol=leg.symbol, side=leg.side, order_type=FOK,
                    quantity=round(leg.quantity, 6), price=round(leg.price, 8),
                )
            except Exception as e:                   # noqa: BLE001
                return self._abort(i, f"ошибка постановки: {e}")

            if not self._is_filled(resp):
                return self._abort(i, f"нога не исполнена (FOK): {resp}")

        # Все ноги прошли — фиксируем результат. PnL оценочный (по нетто-спреду).
        pnl = signal.amount_usdt * (signal.evaluation.net_pct / 100.0)
        self._risk.register_trade(pnl)
        log.info("связка исполнена целиком, оценочный PnL=%.4f USDT", pnl)
        return ExecReport(ok=True, mode="live", legs=plan, note="исполнено")

    def _abort(self, leg_no: int, reason: str) -> ExecReport:
        """Аварийное прекращение: возможен зависший актив -> риск-СТОП."""
        msg = f"СБОЙ на ноге {leg_no}: {reason}"
        log.error(msg)
        if leg_no > 1:
            log.error("ВНИМАНИЕ: предыдущие ноги исполнены — возможен зависший актив!")
        self._risk.halt(msg)
        return ExecReport(ok=False, mode="live", note=msg)

    @staticmethod
    def _is_filled(resp: dict) -> bool:
        """Считает ордер исполненным по статусу из ответа MEXC."""
        if not isinstance(resp, dict):
            return False
        status = str(resp.get("status", "")).upper()
        return status in {"FILLED", "PARTIALLY_FILLED"}  # для FOK ждём FILLED

    def _log_plan(self, header: str, signal: Signal, plan: list[LegPlan]) -> None:
        """Печатает план сделки в лог (одинаково для dry и live)."""
        ev = signal.evaluation
        log.info("=== %s | %s | net=%.4f%% | сумма=%.2f ===",
                 header, ev.triangle.path, ev.net_pct, signal.amount_usdt)
        for i, leg in enumerate(plan, start=1):
            log.info("  нога %d: %s %s qty=%.6f price=%.8f (%s→%s)",
                     i, leg.side, leg.symbol, leg.quantity, leg.price,
                     leg.from_asset, leg.to_asset)