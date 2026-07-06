r"""
connectors/depth_decoder.py — декодер depth-фреймов MEXC (Protocol Buffers).

ЭТО ЕДИНСТВЕННЫЙ модуль, который знает про protobuf. Все остальные модули
работают с чистой структурой DepthUpdate. Так protobuf-специфика не «протекает»
в логику книг — её легко тестировать и при желании заменить.

Схемы (из mexcdevelop/websocket-proto) уже скомпилированы в connectors/proto/*_pb2.py.
Точная структура (проверено по .proto):
  PushDataV3ApiWrapper:
    - channel   (string, поле 1)      — имя канала
    - symbol    (string, поле 3)      — торговая пара
    - oneof body -> publicAggreDepths (поле 313) — тело depth-канала
  PublicAggreDepthsV3Api:
    - asks[] / bids[] (PublicAggreDepthV3ApiItem)
    - fromVersion / toVersion (СТРОКИ! приводим к int)
  PublicAggreDepthV3ApiItem:
    - price / quantity (СТРОКИ! приводим к float)

Важно: тело выбирается через oneof, поэтому проверяем WhichOneof('body'),
а не HasField.
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
# РЕАЛЬНЫЙ ДЕКОДЕР protobuf.
# Импорт обёрнут в try/except: если по какой-то причине _pb2 недоступны
# (напр. не скомпилированы), модуль всё равно импортируется и работает
# заглушка decode_dict — это удобно для офлайн-тестов логики книг.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from connectors.proto import PushDataV3ApiWrapper_pb2 as _pb
    _PROTO_OK = True
except Exception as e:                                # noqa: BLE001
    log.warning("protobuf-схемы не загружены (%s) — доступна только заглушка", e)
    _PROTO_OK = False


def decode(raw) -> Optional[DepthUpdate]:
    """
    Единая точка входа декодера.
    - bytes  -> разбор реальным protobuf-декодером (боевой путь);
    - dict   -> заглушка decode_dict (для офлайн-тестов).
    Возвращает DepthUpdate либо None, если сообщение не является depth-каналом.
    """
    if isinstance(raw, dict):
        return decode_dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        return _decode_bytes(bytes(raw))
    log.warning("decode: неподдерживаемый тип %s", type(raw).__name__)
    return None


def _decode_bytes(raw: bytes) -> Optional[DepthUpdate]:
    """Десериализует бинарный фрейм MEXC в DepthUpdate."""
    if not _PROTO_OK:
        log.warning("получены bytes, но protobuf-декодер не загружен")
        return None
    try:
        wrapper = _pb.PushDataV3ApiWrapper()
        wrapper.ParseFromString(raw)
    except Exception as e:                            # noqa: BLE001
        log.error("не удалось распарсить protobuf-фрейм: %s", e)
        return None

    # Нас интересует только канал глубины. Тело выбирается через oneof 'body'.
    if wrapper.WhichOneof("body") != "publicAggreDepths":
        return None                                   # другой канал (сделки и т.п.) — пропуск

    d = wrapper.publicAggreDepths
    try:
        # price/quantity и версии приходят СТРОКАМИ — приводим к числам
        bids = [[float(x.price), float(x.quantity)] for x in d.bids]
        asks = [[float(x.price), float(x.quantity)] for x in d.asks]
        from_version = int(d.fromVersion) if d.fromVersion else 0
        to_version = int(d.toVersion) if d.toVersion else 0
    except (TypeError, ValueError) as e:
        log.error("%s: ошибка приведения типов depth: %s", wrapper.symbol, e)
        return None

    return DepthUpdate(
        symbol=wrapper.symbol,
        bids=bids,
        asks=asks,
        from_version=from_version,
        to_version=to_version,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ЗАГЛУШКА для разработки без сети: принимает уже готовый dict
# (в такой же форме, как реальный декодер вернёт после разбора bytes).
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