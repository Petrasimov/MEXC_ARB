"""
scripts/demo_telegram.py — офлайн-проверка вехи 5 (без реального Telegram).

Запуск:
    python -m scripts.demo_telegram

Проверяет БЕЗ подключения к Telegram:
  1. Рендер текста пульта из RuntimeState.
  2. Валидацию ручного ввода суммы и порога (правильные и ошибочные значения).
  3. Симуляцию переключений: старт/стоп, dry/live, изменение суммы/порога.

Полноценный запуск бота — на машине с токеном (см. .env: TELEGRAM_BOT_TOKEN).
"""

from __future__ import annotations

from infra.logging_conf import setup_logging, get_logger
from infra.config import RuntimeState
from control.panel import render_panel_text, parse_amount, parse_threshold

log = get_logger("DEMO")


def main() -> None:
    setup_logging()
    state = RuntimeState(mode="dry", amount_usdt=1000.0, threshold_pct=0.2)

    # 1. Рендер пульта
    print("\n=== Текст пульта (начальное состояние) ===")
    print(render_panel_text(state, updated_at="15:42:10"))

    # 2. Валидация ввода суммы
    print("\n=== Валидация ввода суммы ===")
    for raw in ["1500", "2,5", "0", "-100", "abc", "9999999999"]:
        value, err = parse_amount(raw)
        verdict = f"OK -> {value}" if err is None else f"ОТКЛОНЕНО ({err})"
        print(f"  '{raw}': {verdict}")

    # 3. Валидация ввода порога
    print("\n=== Валидация ввода порога ===")
    for raw in ["0.35", "0,5", "0", "100", "xyz"]:
        value, err = parse_threshold(raw)
        verdict = f"OK -> {value}" if err is None else f"ОТКЛОНЕНО ({err})"
        print(f"  '{raw}': {verdict}")

    # 4. Симуляция работы пульта (меняем состояние как это делали бы кнопки)
    print("\n=== Симуляция переключений ===")
    state.running = True
    print("после Старт:   ", _one_line(state))
    value, _ = parse_amount("2500")
    state.amount_usdt = value
    print("после суммы:   ", _one_line(state))
    value, _ = parse_threshold("0.35")
    state.threshold_pct = value
    print("после порога:  ", _one_line(state))
    state.mode = "live"            # в боте — только после подтверждения
    print("после LIVE:    ", _one_line(state))
    state.running = False
    print("после Стоп:    ", _one_line(state))


def _one_line(state: RuntimeState) -> str:
    """Короткая строка состояния для наглядности."""
    run = "работает" if state.running else "остановлен"
    return f"[{run}] режим={state.mode} сумма={state.amount_usdt:.0f} порог={state.threshold_pct:.2f}%"


if __name__ == "__main__":
    main()