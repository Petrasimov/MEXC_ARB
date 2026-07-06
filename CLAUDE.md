# CLAUDE.md

Контекст проекта для AI-ассистента. Читай это перед изменением кода.

## Что за проект

Бот внутрибиржевого треугольного арбитража на **MEXC** (спот). Ищет циклы
`USDT → A → B → USDT`, считает спред тейкером по глубине стакана, вычитает
3 комиссии и торгует, если net-спред выше порога. Управление — Telegram (кнопки),
мониторинг — терминал + Google Sheets.

## Конвенции кода (соблюдай строго)

- **Комментарии на русском.** У каждой функции, класса, важной константы и блока —
  пояснение по-русски. Английские термины (order book, taker, VWAP, IOC/FOK) оставляем как есть.
- **Маленькие файлы.** Один модуль — одна задача. Если файл разрастается за ~150 строк —
  дели на части. Ядро уже разбито: models / triangles / vwap / spread.
- **Максимальное делегирование.** Логика ядра не знает про сеть; сеть не знает про Telegram.
  `pair_selector` работает на «сырых» dict, а не на живых запросах — чтобы тестировать офлайн.
- **Логи с тегами этапов.** Через `infra.logging_conf.get_logger("ТЕГ")`. Теги:
  `PAIRS`, `WS`, `BOOK`, `SCAN`, `EXEC`, `TG`, `REST`. При ошибке должно быть сразу видно этап.
- **Секреты только из окружения.** Ключи API читаются из `.env` через `infra.config`.
  Никогда не хардкодить ключи и не коммитить `.env` (он в `.gitignore`).

## Критичные факты MEXC (влияют на архитектуру)

- Ордера — **только REST** (`POST /api/v3/order`). Отправки ордеров по WebSocket НЕТ.
  Асимметрия: данные по WS, ордера по REST.
- WS market data — **Protocol Buffers**, не JSON. Каналы `spot@public.aggre.depth.v3.api.pb@100ms@SYMBOL`.
  Нужна компиляция `.proto` из репозитория `mexcdevelop/websocket-proto`.
- Восстановление стакана: REST-снапшот `/api/v3/depth?limit=5000` + инкременты по WS,
  непрерывность по версиям (`fromVersion == предыдущий toVersion + 1`), объём — абсолютный.
- Типы ордеров: MARKET (с `quoteOrderQty`), LIMIT, IOC, FOK. Для арбитража — тейкер.
- **Тестнета нет** — API боевой. Поэтому dry-run обязателен, live — с малых сумм.
- Комиссии часто 0%, но не на всех парах — тянуть из данных, не хардкодить.
- Лимиты: ордера 12 req/s; WS 100 req/s, ≤30 подписок/соединение, соединение ≤24ч,
  авто-дисконнект при простое 30с/60с.
- Аутентификация: HMAC-SHA256, заголовок `X-MEXC-APIKEY`, параметры `timestamp` + `recvWindow`.

## Зафиксированные решения

- Стартовый актив: только **USDT**. Мосты: **USDC, BTC, ETH**.
- Охват пар: курируемый топ-ликвидных (~20-50 альтов), не все пары биржи.
- Режимы: сначала **dry-run**, затем **live** с малыми суммами.
- Тип ордеров для MVP: **FOK-ноги** (ради безопасности от «зависших» активов).
- Язык: Python (MVP) → Go (продакшн позже).

## Структура

```
core/        чистая логика (без сети): models, triangles, vwap, spread — ГОТОВО
connectors/  auth (подпись), mexc_rest (REST-клиент, публичные эндпоинты)
engine/      pair_selector, order_book, book_manager, scanner, executor (FOK), risk — ГОТОВО
infra/       logging_conf (логи с тегами), config (конфиг + RuntimeState)
reporting/   snapshot (строки таблицы), terminal_table (rich), sheets (gspread), reporter (общий) — ГОТОВО
control/     panel (текст+валидация), keyboard (кнопки), states (FSM), telegram_bot (aiogram) — ГОТОВО
scripts/     demo_* — офлайн-проверки вех 1-4
tests/       fixtures со снапшотами exchangeInfo / ticker
```

## Дорожная карта (текущий статус)

1. ✅ REST-коннектор + отбор пар + треугольники
2. ✅ protobuf WebSocket + локальные книги (book_manager, order_book, depth_decoder)
3. ✅ Реактивный сканер (scanner) + метрики скорости (infra/metrics.py, p50/p95/p99)
4. ✅ Отчётность: rich-таблица (reporting/terminal_table) + Google Sheets (reporting/sheets)
5. ✅ Telegram-пульт на кнопках (control/: panel, keyboard, states, telegram_bot; ручной ввод, подтверждение LIVE)
6. ✅ Live-исполнитель (engine/executor, FOK) + риск-модуль (engine/risk) + главный цикл (scripts/run.py)

## protobuf (важно при перекомпиляции схем)

Схемы MEXC скомпилированы в `connectors/proto/*_pb2.py` из `mexcdevelop/websocket-proto`.
Сгенерированные файлы MEXC используют «плоские» импорты (`import X_pb2`), из-за чего
Python не видит их как пакет. Поэтому после КАЖДОЙ перекомпиляции нужно:
  1. убедиться, что в `connectors/proto/` есть `__init__.py`;
  2. поправить импорты на пакетные одной командой:
     `python -c "import re,glob; [open(f,'w',encoding='utf-8').write(re.sub(r'^import (\w+_pb2)', r'from . import \1', open(f,encoding='utf-8').read(), flags=re.M)) for f in glob.glob('connectors/proto/*_pb2.py')]"`
  3. проверить: `python -c "from connectors.depth_decoder import _PROTO_OK; print(_PROTO_OK)"` → должно быть `True`.

Если `_PROTO_OK=False`, декодер молча падает на заглушку (dict) — живые книги читаться не будут.

## Как запускать и проверять

```bash
python -m scripts.demo_pair_selection   # веха 1: отбор пар и спред
python -m scripts.demo_book_manager     # веха 2: книги, версии, пересинхронизация
python -m scripts.demo_scanner          # веха 3: реактивный сканер + метрики
python -m scripts.demo_reporter         # веха 4: снапшот, терминал, Google Sheets
python -m scripts.demo_telegram         # веха 5: рендер пульта + валидация ввода
python -m scripts.demo_executor         # веха 6: исполнитель (dry) + риск-модуль
python -m scripts.run                   # ЗАПУСК бота (dry по умолчанию; live — только с ключами + LIVE в Telegram)
```

Сеть в среде разработки может быть недоступна — логику ядра, книг, сканера и
отчётности проверяем на синтетике/фикстурах, REST/WS-код — на живом MEXC при деплое.
Библиотеки `rich`/`gspread` при отсутствии заменяются фолбэком (текст/режим «отключено»).

## Ключевые файлы для быстрого старта

- `core/triangles.py` — как строятся связки и индекс для реактивного пересчёта.
- `core/spread.py` — как считается net/gross спред с вычетом комиссий.
- `engine/pair_selector.py` — как отбираются пары (фильтр статуса, требование моста, ранжирование).
- `engine/scanner.py` — реактивный пересчёт затронутых связок + сигналы.
- `connectors/depth_decoder.py` — единственный модуль с protobuf (см. раздел protobuf).
- `reporting/reporter.py` — общий репортёр (терминал + Google Sheets), читает RuntimeState.
- `infra/config.py` — все настройки и `RuntimeState` (им управляет Telegram).

## Чего не делать

- Не хардкодить ключи и комиссии.
- Не раздувать файлы — дели на модули.
- Не смешивать слои (ядро не должно импортировать сеть/Telegram).
- Не запускать live без предварительного dry-run (у MEXC нет тестнета).