"""
scripts/run.py — главный цикл бота (точка входа для реального запуска).

Новая архитектура (Часть A — отбор по СПРЕДУ, не по объёму):
  REST exchangeInfo -> строим ВСЕ треугольники (движок pair_selector.build_all)
    -> холодный скан по bookTicker: топ треугольников по спреду (cold_scanner)
    -> WS-подписка на пары топа (динамическая, без разрыва)
    -> depth-снапшоты пар топа -> локальные книги
    -> WS-инкременты -> реактивный сканер -> сигналы -> риск -> исполнитель
  фоново: РЕСКАНЕР каждые cfg.rescan_interval сек пересматривает топ и
          переподписывает WS на разницу; параллельно репортёр и Telegram.

Запуск (dry по умолчанию, безопасно): python -m scripts.run
Реальные ордера — только с ключами в .env И режимом LIVE в Telegram.
"""

from __future__ import annotations
import asyncio
import os

from infra.logging_conf import setup_logging, get_logger
from infra.config import Config, RuntimeState, START_ASSETS, BRIDGES
from connectors.mexc_rest import MexcRestClient
from connectors.mexc_ws import MexcWsClient
from connectors.depth_decoder import DepthUpdate
from engine.pair_selector import build_all, parse_symbols, build_index
from engine.cold_scanner import cold_scan
from engine.book_manager import BookManager
from engine.scanner import Scanner
from engine.risk import RiskManager, RiskLimits
from engine.executor import Executor
from reporting.reporter import Reporter
from reporting.sheets import SheetsReporter

log = get_logger("APP")

_DEPTH_LIMIT = 100          # глубина снапшота для пар топа (хватает для VWAP на разумную сумму)


async def main() -> None:
    setup_logging()
    cfg = Config.from_env()
    state = RuntimeState()

    async with MexcRestClient(cfg) as rest:
        # 1. Все пары и ВСЕ треугольники (без ранжирования по объёму)
        exchange_info = await rest.exchange_info()
        pairs = parse_symbols(exchange_info, cfg.default_taker_fee)
        all_pairs, all_triangles = build_all(pairs, list(START_ASSETS), list(BRIDGES))
        index = build_index(all_triangles)
        fee_by_symbol = {p.symbol: p.taker_fee for p in all_pairs}
        fee_provider = lambda s: fee_by_symbol.get(s, cfg.default_taker_fee)

        # 2. Холодный скан: выбираем топ треугольников по спреду
        book_ticker = await rest.book_ticker()
        cold = cold_scan(all_triangles, book_ticker, state.amount_usdt,
                         fee_provider, cfg.top_n)
        active_symbols = list(cold.symbols)

        # 3. Книги + resync через REST-снапшот
        async def resync(symbol: str):
            snap = await rest.depth(symbol, limit=_DEPTH_LIMIT)
            return snap.get("bids", []), snap.get("asks", []), int(snap.get("lastUpdateId", 0))

        books = BookManager([p.symbol for p in all_pairs], resync_cb=resync)

        # 4. Стартовые снапшоты только для пар топа
        await _load_snapshots(rest, books, active_symbols)

        # 5. Риск + исполнитель
        risk = RiskManager(RiskLimits(
            max_amount_usdt=cfg.risk_max_amount, cooldown_sec=cfg.risk_cooldown_sec,
            max_trades_per_day=cfg.risk_max_trades_day, daily_loss_limit_usdt=cfg.risk_daily_loss,
        ))
        executor = Executor(rest, state, risk, slippage_pct=cfg.slippage_pct)

        # 6. Сканер (на сигнал — исполнение, если мониторинг включён)
        def on_signal(sig):
            if state.running:
                asyncio.create_task(executor.handle_signal(sig))

        scanner = Scanner(index=index, book_provider=books.snapshot_books, state=state,
                          fee_provider=fee_provider, anomaly_pct=cfg.anomaly_pct,
                          on_signal=on_signal)

        # 7. WS-обработчик
        async def on_update(upd: DepthUpdate):
            await books.on_update(upd)
            if state.running:
                scanner.on_symbol_update(upd.symbol)

        ws = MexcWsClient(cfg.ws_base, active_symbols, on_update)

        # 8. Репортёр показывает топ-треугольники (по холодному скану), обновляется в run
        reporter_holder = {"triangles": cold.triangles}
        sheets = SheetsReporter(sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
                                sa_json=os.getenv("GOOGLE_SA_JSON", ""))
        reporter = Reporter(reporter_holder["triangles"], books.snapshot_books, state,
                            fee_provider, cfg.anomaly_pct, sheets=sheets, interval=1.5)

        # 9. Фоновый ресканер: пересматривает топ и переподписывает WS
        async def rescanner():
            while True:
                await asyncio.sleep(cfg.rescan_interval)
                try:
                    bt = await rest.book_ticker()
                    new_cold = cold_scan(all_triangles, bt, state.amount_usdt,
                                         fee_provider, cfg.top_n)
                    new_syms = set(new_cold.symbols)
                    added = new_syms - ws.current_symbols()
                    # Порядок важен: СНАЧАЛА подписываемся на WS (обновления начинают
                    # копиться), ПОТОМ берём снапшот — так снапшот свежее первых
                    # событий и книга continues без «дырки». Остаток чинит resync.
                    await ws.set_symbols(list(new_syms))
                    if added:
                        await _load_snapshots(rest, books, list(added))
                    # обновить набор треугольников в репортёре
                    reporter.set_triangles(new_cold.triangles)
                except Exception as e:                # noqa: BLE001
                    log.error("ресканер: ошибка цикла: %s", e)

        # 10. Telegram-пульт
        tasks = [ws.run(), reporter.run(), rescanner()]
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            from control.telegram_bot import TelegramPanel
            panel = TelegramPanel(token, int(chat_id), state)
            tasks.append(panel.start())
        else:
            log.warning("Telegram не задан — работаем без пульта, мониторинг включаю автоматически")
            state.running = True

        log.info("бот запущен: всего треугольников %d, в топе %d, пар на WS %d, ресканер %.1fс, режим=%s",
                 len(all_triangles), len(cold.triangles), len(active_symbols),
                 cfg.rescan_interval, state.mode)
        await asyncio.gather(*tasks)


async def _load_snapshots(rest, books, symbols: list) -> None:
    """Последовательно грузит depth-снапшоты и инициализирует книги."""
    for sym in symbols:
        try:
            snap = await rest.depth(sym, limit=_DEPTH_LIMIT)
            books.init_book(sym, snap.get("bids", []), snap.get("asks", []),
                            int(snap.get("lastUpdateId", 0)))
        except Exception as e:                        # noqa: BLE001
            log.error("не удалось загрузить снапшот %s: %s", sym, e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("остановлено пользователем")