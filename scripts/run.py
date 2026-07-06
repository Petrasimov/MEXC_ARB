"""
scripts/run.py — главный цикл бота (точка входа для реального запуска).

Связывает все слои в единый рабочий цикл:
  REST (exchangeInfo/ticker) -> отбор пар и треугольников
    -> REST-снапшоты стаканов -> локальные книги
    -> WebSocket-инкременты -> реактивный сканер -> сигналы
    -> риск-модуль -> исполнитель (dry/live)
  параллельно: репортёр (терминал + Google Sheets) и Telegram-пульт.

Запуск (dry по умолчанию, безопасно):
    python -m scripts.run

Реальные ордера возможны ТОЛЬКО когда:
  - в .env заданы MEXC_API_KEY/MEXC_API_SECRET;
  - в Telegram включён режим LIVE (с подтверждением).
Без ключей приватные запросы физически не уходят — dry-run полностью безопасен.
"""

from __future__ import annotations
import asyncio
import os

from infra.logging_conf import setup_logging, get_logger
from infra.config import Config, RuntimeState, START_ASSETS, BRIDGES
from connectors.mexc_rest import MexcRestClient
from connectors.mexc_ws import MexcWsClient
from connectors.depth_decoder import DepthUpdate
from engine.pair_selector import select_universe, parse_symbols, build_index
from engine.book_manager import BookManager
from engine.scanner import Scanner
from engine.risk import RiskManager, RiskLimits
from engine.executor import Executor
from reporting.reporter import Reporter
from reporting.sheets import SheetsReporter

log = get_logger("APP")


async def main() -> None:
    setup_logging()
    cfg = Config.from_env()
    state = RuntimeState()          # dry по умолчанию, running=False до старта в Telegram

    async with MexcRestClient(cfg) as rest:
        # 1. Отбор пар и построение треугольников
        exchange_info = await rest.exchange_info()
        tickers = await rest.ticker_24hr()
        pairs = parse_symbols(exchange_info, cfg.default_taker_fee)
        universe, triangles = select_universe(
            pairs, tickers, list(START_ASSETS), list(BRIDGES), cfg.top_n
        )
        index = build_index(triangles)
        symbols = [p.symbol for p in universe]
        fee_by_symbol = {p.symbol: p.taker_fee for p in universe}
        fee_provider = lambda s: fee_by_symbol.get(s, cfg.default_taker_fee)

        # 2. Книги + колбэк пересинхронизации через REST-снапшот
        async def resync(symbol: str):
            snap = await rest.depth(symbol, limit=5000)
            return snap.get("bids", []), snap.get("asks", []), int(snap.get("lastUpdateId", 0))

        books = BookManager(symbols, resync_cb=resync)

        # 3. Стартовые снапшоты стаканов (последовательно, чтобы не превысить лимиты)
        for sym in symbols:
            try:
                snap = await rest.depth(sym, limit=5000)
                books.init_book(sym, snap.get("bids", []), snap.get("asks", []),
                                int(snap.get("lastUpdateId", 0)))
            except Exception as e:                   # noqa: BLE001
                log.error("не удалось загрузить снапшот %s: %s", sym, e)

        # 4. Риск-модуль и исполнитель
        risk = RiskManager(RiskLimits(
            max_amount_usdt=cfg.risk_max_amount,
            cooldown_sec=cfg.risk_cooldown_sec,
            max_trades_per_day=cfg.risk_max_trades_day,
            daily_loss_limit_usdt=cfg.risk_daily_loss,
        ))
        executor = Executor(rest, state, risk, slippage_pct=cfg.slippage_pct)

        # 5. Сканер: на сигнал — планируем исполнение (только если мониторинг включён)
        def on_signal(sig):
            if state.running:
                asyncio.create_task(executor.handle_signal(sig))

        scanner = Scanner(
            index=index, book_provider=books.snapshot_books, state=state,
            fee_provider=fee_provider, anomaly_pct=cfg.anomaly_pct, on_signal=on_signal,
        )

        # 6. WS-обработчик: применяем обновление к книге и реактивно пересчитываем
        async def on_update(upd: DepthUpdate):
            await books.on_update(upd)
            if state.running:
                scanner.on_symbol_update(upd.symbol)

        ws = MexcWsClient(cfg.ws_base, symbols, on_update)

        # 7. Репортёр (терминал + Google Sheets, если заданы ключи)
        sheets = SheetsReporter(
            sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
            sa_json=os.getenv("GOOGLE_SA_JSON", ""),
        )
        reporter = Reporter(triangles, books.snapshot_books, state, fee_provider,
                            cfg.anomaly_pct, sheets=sheets, interval=1.5)

        # 8. Telegram-пульт (если задан токен)
        tasks = [ws.run(), reporter.run()]
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            from control.telegram_bot import TelegramPanel
            panel = TelegramPanel(token, int(chat_id), state)
            tasks.append(panel.start())
        else:
            log.warning("Telegram не задан (нет токена) — работаем без пульта, "
                        "мониторинг включаю автоматически")
            state.running = True

        log.info("бот запущен: %d пар, %d треугольников, режим=%s",
                 len(symbols), len(triangles), state.mode)
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("остановлено пользователем")