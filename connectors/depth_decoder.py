r"""
connectors/depth_decoder.py — декодер depth-фреймов MEXC (Protocol Buffers).

ЭТО ЕДИНСТВЕННЫЙ модуль, который знает про protobuf. Все остальные модули
работают с чистой структурой DepthUpdate. Так protobuf-специфика не «протекает»
в логику книг — её легко тестировать и при желании заменить.

┌─ ЧТО СДЕЛАТЬ ЛОКАЛЬНО (там, где есть интернет) ─────────────────────────────┐
│ 1. Склонировать схемы:                                                      │
│      git clone https://github.com/mexcdevelop/websocket-proto               │
│ 2. Скомпилировать в Python (нужен protoc или grpcio-tools):                 │
│      python -m grpc_tools.protoc -I websocket-proto \                       │
│              --python_out=connectors/proto websocket-proto/*.proto          │
│ 3. Появятся файлы *_pb2.py в connectors/proto/. Тогда:                      │
│      - раскомментировать блок РЕАЛЬНОГО ДЕКОДЕРА ниже;                       │
│      - удалить/оставить заглушку _decode_stub как фолбэк.                    │
└─────────────────────────────────────────────────────────────────────────────┘

Формат MEXC (канал spot@public.aggre.depth.v3.api.pb@100ms@SYMBOL):
обёртка PushDataV3ApiWrapper содержит publicAggreDepths с полями:
  - asks[] / bids[]: элементы с price и quantity (строки);
  - fromVersion / toVersion: границы версий инкремента.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from infra.logging_conf import get_logger

log = get_logger("WS")


@dataclass
class DepthUpdate:
    """
    Разобранное depth-обновление в чистом виде (без protobuf).
    Именно это отдаётся в book_manager — контракт между сетью и книгами.
    """
    symbol: str
    bids: list = field(default_factory=list)   # [[price, qty], ...]
    asks: list = field(default_factory=list)   # [[price, qty], ...]
    from_version: int = 0
    to_version: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# РЕАЛЬНЫЙ ДЕКОДЕР (раскомментировать после компиляции .proto).
# ─────────────────────────────────────────────────────────────────────────────
# from connectors.proto import PushDataV3ApiWrapper_pb2 as pb
#
# def decode(raw: bytes) -> Optional[DepthUpdate]:
#     """Десериализует бинарный фрейм MEXC в DepthUpdate."""
#     wrapper = pb.PushDataV3ApiWrapper()
#     wrapper.ParseFromString(raw)
#     # Нужен только канал глубины; прочие типы (сделки и т.п.) пропускаем
#     if not wrapper.HasField("publicAggreDepths"):
#         return None
#     d = wrapper.publicAggreDepths
#     return DepthUpdate(
#         symbol=wrapper.symbol,
#         bids=[[float(x.price), float(x.quantity)] for x in d.bids],
#         asks=[[float(x.price), float(x.quantity)] for x in d.asks],
#         from_version=int(d.fromVersion),
#         to_version=int(d.toVersion),
#     )


# ─────────────────────────────────────────────────────────────────────────────
# ЗАГЛУШКА для разработки без .proto: принимает уже готовый dict
# (в такой же форме, как реальный декодер вернёт после компиляции).
# Позволяет тестировать book_manager офлайн на синтетических данных.
# ─────────────────────────────────────────────────────────────────────────────
def decode_dict(msg: dict) -> Optional[DepthUpdate]:
    """
    Разбирает уже-декодированное сообщение-словарь (для тестов и отладки).
    Форма: {'symbol', 'bids', 'asks', 'fromVersion', 'toVersion'}.
    """
    try:
        return DepthUpdate(
            symbol=msg["symbol"],
            bids=[[float(p), float(q)] for p, q in msg.get("bids", [])],
            asks=[[float(p), float(q)] for p, q in msg.get("asks", [])],
            from_version=int(msg.get("fromVersion", 0)),
            to_version=int(msg.get("toVersion", 0)),
        )
    except (KeyError, TypeError, ValueError) as e:
        log.error("не удалось разобрать сообщение: %s", e)
        return None


# Единая точка входа. Сейчас указывает на заглушку; после компиляции .proto
# переключить на реальный decode(raw: bytes).
def decode(raw) -> Optional[DepthUpdate]:
    """
    Точка входа декодера. Пока raw — это dict (заглушка).
    После компиляции .proto заменить тело на разбор bytes реальным декодером.
    """
    if isinstance(raw, dict):
        return decode_dict(raw)
    log.warning("получены bytes, но реальный protobuf-декодер ещё не включён")
    return None
