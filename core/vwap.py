"""
core/vwap.py — расчёт реальной цены исполнения тейкером по глубине стакана.

Ключевая идея: нельзя считать по лучшей цене (top-of-book). Нужно «пройти»
уровни стакана на сумму пользователя и получить реальную среднюю цену.
Если глубины не хватило на всю сумму — связка неисполнима на этом объёме.

Формат стакана:
  asks = [[price, qty], ...] по возрастанию цены
  bids = [[price, qty], ...] по убыванию цены
  qty — в базовом активе пары.
"""

from __future__ import annotations
from typing import Optional

# Порог «остатка», ниже которого считаем, что сумму удалось набрать полностью.
# Защищает от накопления ошибок float.
_EPS = 1e-9


def walk_buy(asks: list, quote_amount: float, fee_rate: float) -> Optional[float]:
    """
    BUY: трачу quote_amount, получаю base. Идём по ask снизу вверх.
    Комиссия тейкера вычитается из полученного base.
    Возвращает объём base на выходе, либо None если глубины не хватает.
    """
    base_out = 0.0
    quote_left = quote_amount
    for price, qty in asks:
        level_cost = price * qty                 # стоимость всего уровня в quote
        if quote_left >= level_cost:
            base_out += qty                      # забираем уровень целиком
            quote_left -= level_cost
        else:
            base_out += quote_left / price       # добираем часть последнего уровня
            quote_left = 0.0
            break
    if quote_left > _EPS:                         # прошли всю книгу, объёма мало
        return None
    return base_out * (1.0 - fee_rate)


def walk_sell(bids: list, base_amount: float, fee_rate: float) -> Optional[float]:
    """
    SELL: трачу base_amount, получаю quote. Идём по bid сверху вниз.
    Комиссия тейкера вычитается из полученного quote.
    Возвращает объём quote на выходе, либо None если глубины не хватает.
    """
    quote_out = 0.0
    base_left = base_amount
    for price, qty in bids:
        if base_left >= qty:
            quote_out += price * qty             # продаём весь уровень
            base_left -= qty
        else:
            quote_out += price * base_left       # продаём остаток в последний уровень
            base_left = 0.0
            break
    if base_left > _EPS:
        return None
    return quote_out * (1.0 - fee_rate)
