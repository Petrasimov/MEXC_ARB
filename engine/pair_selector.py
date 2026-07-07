"""
engine/pair_selector.py — отбор пар и построение треугольников.

Работает на «сырых» данных (dict из exchangeInfo и ticker/24hr), без сети —
поэтому логику легко тестировать на сохранённом снапшоте.

Алгоритм (см. план, раздел «Отбор пар»):
  1. Разобрать exchangeInfo -> список торгуемых Pair.
  2. Оставить пары с quote из {стартовые активы} ∪ {мосты}.
  3. Требование моста: у альта должен быть рынок к мосту, иначе он тупиковый.
  4. Ранжировать альты по объёму 24ч, взять топ N.
  5. Собрать набор пар для подписки и построить треугольники (делегируется ядру).
"""

from __future__ import annotations

from core.models import Pair
from core.triangles import MarketGraph, build_symbol_index
from infra.logging_conf import get_logger

log = get_logger("PAIRS")

# Статусы MEXC, которые считаем «торгуется». MEXC в разных версиях отдаёт
# либо "1"/"ENABLED", либо "TRADING" — принимаем все варианты.
_TRADABLE_STATUSES = {"1", "ENABLED", "TRADING"}


def parse_symbols(exchange_info: dict, default_taker_fee: float) -> list[Pair]:
    """
    Разбирает ответ exchangeInfo в список торгуемых пар.
    Берём takerCommission из данных пары, если он есть, иначе дефолт.
    """
    pairs: list[Pair] = []
    symbols = exchange_info.get("symbols", [])
    for s in symbols:
        # Пара считается торгуемой: спот разрешён И статус в списке «торгуется»
        spot_ok = s.get("isSpotTradingAllowed", False)
        status_ok = str(s.get("status", "")) in _TRADABLE_STATUSES
        if not (spot_ok and status_ok):
            continue
        # Комиссия тейкера: в exchangeInfo может лежать строкой
        taker = s.get("takerCommission")
        try:
            taker_fee = float(taker) if taker is not None else default_taker_fee
        except (TypeError, ValueError):
            taker_fee = default_taker_fee
        pairs.append(Pair(
            symbol=s["symbol"],
            base=s["baseAsset"],
            quote=s["quoteAsset"],
            taker_fee=taker_fee,
        ))
    log.info("торгуемых пар после фильтра статуса: %d", len(pairs))
    return pairs


def _volume_index(tickers: list) -> dict[str, float]:
    """Строит индекс symbol -> объём 24ч в котируемой валюте (quoteVolume)."""
    idx: dict[str, float] = {}
    for t in tickers:
        try:
            idx[t["symbol"]] = float(t.get("quoteVolume", 0.0) or 0.0)
        except (TypeError, ValueError):
            idx[t["symbol"]] = 0.0
    return idx


def build_all(
    pairs: list[Pair],
    start_assets: list[str],
    bridges: list[str],
) -> tuple[list[Pair], list]:
    """
    Строит ВСЕ возможные треугольники из торгуемых пар (без ранжирования по объёму).
    Отбор лучших по спреду делает холодный сканер (engine/cold_scanner).

    Оставляем только пары, чей quote входит в разрешённые (старт USDT + мосты) —
    иначе пара не может участвовать в цикле USDT→...→USDT через мост.
    Возвращает (все подходящие пары, все треугольники).
    """
    allowed_quotes = set(start_assets) | set(bridges)
    quoted = [p for p in pairs if p.quote in allowed_quotes]
    log.info("пар с разрешённым quote (USDT/мосты): %d", len(quoted))

    graph = MarketGraph(quoted)
    triangles = graph.build_triangles(list(start_assets))
    log.info("построено ВСЕХ треугольников: %d (пар: %d)", len(triangles), len(quoted))

    return quoted, triangles


def select_universe(
    pairs: list[Pair],
    tickers: list,
    start_assets: list[str],
    bridges: list[str],
    top_n: int,
) -> tuple[list[Pair], list]:
    """
    УСТАРЕЛО (отбор по объёму 24ч). Оставлено для обратной совместимости и офлайн-демо.
    Боевой путь теперь: build_all(...) + холодный сканбор по спреду.
    """
    allowed_quotes = set(start_assets) | set(bridges)
    bridge_set = set(bridges)
    vol = _volume_index(tickers)

    quoted = [p for p in pairs if p.quote in allowed_quotes]
    log.info("пар с разрешённым quote (USDT/мосты): %d", len(quoted))

    base_quotes: dict[str, set[str]] = {}
    for p in quoted:
        base_quotes.setdefault(p.base, set()).add(p.quote)

    start_set = set(start_assets)
    candidate_alts = []
    for base, quotes in base_quotes.items():
        if base in start_set or base in bridge_set:
            continue
        if bool(quotes & start_set) and bool(quotes & bridge_set):
            candidate_alts.append(base)
    log.info("альтов, способных встать в треугольник (есть мост): %d", len(candidate_alts))

    start0 = start_assets[0]
    candidate_alts.sort(key=lambda base: vol.get(f"{base}{start0}", 0.0), reverse=True)
    top_alts = set(candidate_alts[:top_n])
    log.info("взято топ-%d альтов по объёму 24ч: %d", top_n, len(top_alts))

    universe_assets = start_set | bridge_set | top_alts
    universe_pairs = [
        p for p in quoted
        if p.base in universe_assets and p.quote in universe_assets
    ]
    log.info("пар в наборе для подписки: %d", len(universe_pairs))

    graph = MarketGraph(universe_pairs)
    triangles = graph.build_triangles(list(start_assets))
    log.info("построено треугольников: %d", len(triangles))

    return universe_pairs, triangles


def build_index(triangles: list) -> dict:
    """Обёртка над ядром: индекс symbol -> треугольники (для реактивного пересчёта)."""
    index = build_symbol_index(triangles)
    log.info("индекс symbol->треугольники: %d символов", len(index))
    return index