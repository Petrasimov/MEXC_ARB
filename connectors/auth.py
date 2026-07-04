"""
connectors/auth.py — подпись приватных запросов MEXC.

MEXC использует HMAC-SHA256: подпись считается над строкой параметров запроса,
включающей timestamp. Готовая подпись добавляется параметром signature.
Публичные эндпоинты (рыночные данные) подписи не требуют.

Документация: заголовок X-MEXC-APIKEY + параметры timestamp/recvWindow + signature.
"""

from __future__ import annotations
import hmac
import hashlib
import time
from urllib.parse import urlencode

# Окно валидности запроса по умолчанию (мс). Защита от задержек/повторов.
DEFAULT_RECV_WINDOW = 5000


def _timestamp_ms() -> int:
    """Текущее время в миллисекундах — обязательный параметр подписи."""
    return int(time.time() * 1000)


def build_query(params: dict) -> str:
    """
    Собирает query string из параметров.
    Важно: подписывать нужно ровно ту строку, что уходит на сервер,
    поэтому сборка строки и подпись используют один и тот же порядок.
    """
    return urlencode(params, doseq=True)


def sign(secret: str, query_string: str) -> str:
    """Считает HMAC-SHA256 подпись строки параметров секретным ключом."""
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def signed_query(secret: str, params: dict,
                 recv_window: int = DEFAULT_RECV_WINDOW) -> str:
    """
    Добавляет timestamp/recvWindow, считает подпись и возвращает
    итоговую query string с параметром signature на конце.
    """
    p = dict(params)                       # копия, чтобы не портить исходник
    p["timestamp"] = _timestamp_ms()
    p["recvWindow"] = recv_window
    qs = build_query(p)
    signature = sign(secret, qs)
    return f"{qs}&signature={signature}"
