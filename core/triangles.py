"""
core/triangles.py — построение треугольных связок из набора пар.

Отвечает за:
  1. Граф активов (какой актив с каким связан рынком).
  2. Определение направления ноги (BUY/SELL, сторона книги).
  3. Перечисление всех ориентированных циклов длины 3 от заданных стартовых активов.
  4. Индекс symbol -> [треугольники] для реактивного пересчёта (используется сканером).
"""

from __future__ import annotations
from typing import Optional

from .models import Pair, Leg, Triangle


class MarketGraph:
    """Граф активов, построенный из списка пар. Умеет перечислять треугольники."""

    def __init__(self, pairs: list[Pair]):
        # Список пар и быстрый доступ по символу
        self.pairs = list(pairs)
        self.by_symbol: dict[str, Pair] = {p.symbol: p for p in pairs}
        # Прямой поиск пары по (base, quote)
        self._lookup: dict[tuple[str, str], Pair] = {}
        # Ненаправленная смежность: какие активы напрямую связаны рынком
        self.adj: dict[str, set[str]] = {}
        for p in pairs:
            self._lookup[(p.base, p.quote)] = p
            self.adj.setdefault(p.base, set()).add(p.quote)
            self.adj.setdefault(p.quote, set()).add(p.base)

    def resolve_leg(self, from_asset: str, to_asset: str) -> Optional[Leg]:
        """
        Определяет, как конвертировать from_asset -> to_asset одной сделкой.
        BUY  : пара (to, from)  -> трачу quote(=from), получаю base(=to), беру ask.
        SELL : пара (from, to)  -> трачу base(=from), получаю quote(=to), беру bid.
        Возвращает None, если прямого рынка нет.
        """
        p = self._lookup.get((to_asset, from_asset))
        if p is not None:
            return Leg(p.symbol, "BUY", "asks", from_asset, to_asset)
        p = self._lookup.get((from_asset, to_asset))
        if p is not None:
            return Leg(p.symbol, "SELL", "bids", from_asset, to_asset)
        return None

    def build_triangles(self, start_assets: list[str]) -> list[Triangle]:
        """
        Перечисляет все ориентированные циклы длины 3, начинающиеся и
        заканчивающиеся в одном из start_assets.

        Направление важно: S→A→B→S и S→B→A→S — разные возможности, обе сохраняем.
        """
        triangles: list[Triangle] = []
        seen: set[tuple] = set()

        for S in start_assets:
            if S not in self.adj:
                continue
            for A in self.adj[S]:                       # нога 1: S -> A
                if A == S:
                    continue
                for B in self.adj[A]:                   # нога 2: A -> B
                    if B == S or B == A:
                        continue
                    if S not in self.adj.get(B, set()): # нужна нога 3: B -> S
                        continue

                    leg1 = self.resolve_leg(S, A)
                    leg2 = self.resolve_leg(A, B)
                    leg3 = self.resolve_leg(B, S)
                    if not (leg1 and leg2 and leg3):
                        continue

                    key = (S, leg1.symbol, leg2.symbol, leg3.symbol)
                    if key in seen:
                        continue
                    seen.add(key)
                    triangles.append(Triangle(S, (leg1, leg2, leg3)))

        return triangles


def build_symbol_index(triangles: list[Triangle]) -> dict[str, list[Triangle]]:
    """
    Строит индекс symbol -> список треугольников, где эта пара участвует.

    Нужен для реактивного пересчёта: когда по WS приходит обновление книги
    конкретного символа, пересчитываем ТОЛЬКО связанные с ним треугольники,
    а не все подряд.
    """
    index: dict[str, list[Triangle]] = {}
    for tri in triangles:
        for symbol in tri.symbols:
            index.setdefault(symbol, []).append(tri)
    return index
