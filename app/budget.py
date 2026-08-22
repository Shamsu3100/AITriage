"""A spend cap you control in code.

The Console spend limit is your real safety net. This is the second one:
it stops a runaway loop *before* the request is sent, and it works even
when a student is running the app on their own laptop at 3am.
"""
import json
import os
import threading
from datetime import date
from pathlib import Path

# Anthropic list prices, USD per 1 MILLION tokens.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-opus-5":    (5.00, 25.00),
}

MAX_USD_PER_DAY = float(os.getenv("MAX_USD_PER_DAY", "1.00"))
MAX_CALLS_PER_DAY = int(os.getenv("MAX_CALLS_PER_DAY", "500"))
STATE = Path(os.getenv("BUDGET_FILE", "budget.json"))

_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def _load():
    try:
        d = json.loads(STATE.read_text())
    except (OSError, ValueError):
        d = {}
    if d.get("day") != date.today().isoformat():      # resets at midnight
        d = {"day": date.today().isoformat(), "calls": 0, "usd": 0.0}
    return d


def cost_of(model, input_tokens, output_tokens):
    """Exact cost from the token counts the API reports back."""
    in_price, out_price = PRICES.get(model, (0.0, 0.0))
    return (input_tokens / 1e6) * in_price + (output_tokens / 1e6) * out_price


def check():
    """Call BEFORE spending money. Raises rather than charging you."""
    with _lock:
        d = _load()
        if d["calls"] >= MAX_CALLS_PER_DAY:
            raise BudgetExceeded(
                f"Daily call cap reached ({d['calls']}/{MAX_CALLS_PER_DAY}). "
                f"Raise MAX_CALLS_PER_DAY or wait until tomorrow.")
        if d["usd"] >= MAX_USD_PER_DAY:
            raise BudgetExceeded(
                f"Daily spend cap reached (${d['usd']:.4f}/${MAX_USD_PER_DAY:.2f}).")


def record(model, input_tokens, output_tokens):
    """Call AFTER a successful request, with the real usage numbers."""
    usd = cost_of(model, input_tokens, output_tokens)
    with _lock:
        d = _load()
        d["calls"] += 1
        d["usd"] = round(d["usd"] + usd, 6)
        STATE.write_text(json.dumps(d))
    return usd


def status():
    d = _load()
    return {**d,
            "usd_limit": MAX_USD_PER_DAY,
            "calls_limit": MAX_CALLS_PER_DAY,
            "usd_remaining": round(max(0.0, MAX_USD_PER_DAY - d["usd"]), 6)}
