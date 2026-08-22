"""The ONLY file that knows which AI provider we use, and the only one
that ever touches an API key. Everything else in the app is unaware."""
import json
import os
import re
import threading
import requests
from typing import Literal
from pydantic import BaseModel, Field

from app import budget

# A local model runs ONE request at a time. Without this lock, 5 callers all
# start their 60s clock together and 4 of them time out. With it, they queue:
# slower for the last one, but everybody gets a real answer.
_local_model_lock = threading.Semaphore(int(os.getenv("OLLAMA_CONCURRENCY", "1")))

PROVIDER = os.getenv("PROVIDER", "mock")   # mock | ollama | claude | hybrid
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")

SYSTEM = (
    "You triage sensor readings from student engineering prototypes. "
    "Compare the reading against the stated normal range. "
    "Inside the range -> normal. Outside it -> warning. Far outside -> critical. "
    "Be terse."
)


class Triage(BaseModel):
    """The contract. Every provider must return exactly this shape."""
    # Literal, not str. A description is a SUGGESTION the model may ignore.
    # A Literal becomes an enum in the JSON schema, which is ENFORCED.
    severity: Literal["normal", "warning", "critical"]
    reason: str = Field(description="one short sentence, max 15 words")
    action: str = Field(description="one concrete next step")


def _prompt(sensor, value, unit, normal_range):
    return f"Sensor: {sensor}\nReading: {value} {unit}\nNormal range: {normal_range}"


# ---------- provider 1: mock. Free. No key. No internet. ----------
def _mock(sensor, value, unit, normal_range):
    """Plain arithmetic. No AI at all. Reads the range instead of guessing."""
    nums = re.findall(r"\d+(?:\.\d+)?", normal_range)   # "20-60 C" -> ["20","60"]
    if len(nums) < 2:
        return Triage(severity="normal", reason="[MOCK] No range given.",
                      action="Provide a normal_range like '20-60 C'.")
    low, high = float(nums[0]), float(nums[1])
    span = (high - low) or 1
    if low <= value <= high:
        sev, why = "normal", "inside the normal range"
    elif value < low - span or value > high + span:
        sev, why = "critical", "far outside the normal range"
    else:
        sev, why = "warning", "just outside the normal range"
    return Triage(
        severity=sev,
        reason=f"[MOCK] {value}{unit} is {why} ({low}-{high}).",
        action="Set PROVIDER=ollama or claude for a real model.",
    )


# ---------- provider 2: Claude. Paid, cents. Best quality. ----------
def _claude(sensor, value, unit, normal_range):
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is empty. Add it to .env")
    client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from the environment
    try:
        budget.check()                  # refuse BEFORE spending, not after
        r = client.with_options(timeout=15.0).messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(sensor, value, unit, normal_range)}],
            output_format=Triage,           # forces valid JSON matching the class above
        )
        budget.record(CLAUDE_MODEL, r.usage.input_tokens, r.usage.output_tokens)
        return r.parsed_output
    except anthropic.AuthenticationError:
        raise RuntimeError("Key invalid or revoked. Rotate it.")
    except anthropic.RateLimitError:
        raise RuntimeError("Rate limited. Retry shortly.")
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        raise RuntimeError("Could not reach Anthropic. Check the network.")


# ---------- provider 3: Ollama. Free forever. Runs on the laptop. ----------
def _ollama(sensor, value, unit, normal_range):
    try:
        r = requests.post("http://localhost:11434/api/chat", timeout=60, json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": Triage.model_json_schema(),   # same schema, local model
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _prompt(sensor, value, unit, normal_range)},
            ],
        })
        if r.status_code == 404:
            raise RuntimeError(
                f"Model {OLLAMA_MODEL!r} is not downloaded. Run: ollama pull {OLLAMA_MODEL}")
        r.raise_for_status()
        return Triage.model_validate_json(r.json()["message"]["content"])
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Ollama is not running. Start it with: ollama serve")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise RuntimeError(f"Ollama returned something unparseable: {e}")


# ---------- provider 4: hybrid. The one you should actually ship. ----------
def severity_of(value, normal_range):
    """Pure arithmetic. 100% correct, 0ms, free, unit-testable."""
    nums = re.findall(r"\d+(?:\.\d+)?", normal_range)
    if len(nums) < 2:
        return None, None, None
    low, high = float(nums[0]), float(nums[1])
    span = (high - low) or 1
    if low <= value <= high:
        return "normal", low, high
    if value < low - span or value > high + span:
        return "critical", low, high
    return "warning", low, high


def _hybrid(sensor, value, unit, normal_range):
    """Code decides WHAT (a fact). The model explains WHY (judgment).

    Never ask a language model to compare two numbers. Ask it to do the
    thing code cannot do: write a useful sentence for a human.
    """
    sev, low, high = severity_of(value, normal_range)
    if sev is None:
        return _mock(sensor, value, unit, normal_range)

    reason, action = explain(sensor, value, unit, low, high, sev)
    return Triage(severity=sev, reason=reason, action=action)


def explain(sensor, value, unit, low, high, sev):
    """The SLOW half. Returns (reason, action). Never raises: if the model is
    down or slow, the caller still has a correct severity from code."""
    inner = os.getenv("HYBRID_BACKEND", "ollama")
    fallback = (f"{value}{unit} vs normal {low}-{high}.",
                "No action." if sev == "normal" else "Investigate.")
    if inner == "mock":
        return fallback

    # Most readings are boring. "It is fine" needs no AI, and skipping it
    # keeps the queue free for the readings that actually need explaining.
    if sev == "normal" and os.getenv("EXPLAIN_NORMAL", "false").lower() != "true":
        return (f"{value}{unit} is within the safe range {low}-{high}.", "No action needed.")

    class Advice(BaseModel):
        reason: str = Field(description="under 12 words, must contain the numbers")
        action: str = Field(description="under 10 words, an instruction")

    # Do NOT put meta-instructions ("do not re-classify") in the prompt: a small
    # model will copy them straight into its answer. State facts, ask for output.
    state = {"normal": "inside", "warning": "outside", "critical": "far outside"}[sev]
    prompt = (
        f"An industrial {sensor} sensor reads {value}{unit}. "
        f"Its safe operating range is {low}{unit} to {high}{unit}, "
        f"so the reading is {state} the safe range.\n"
        f"reason: state the measured value and the safe range, under 12 words.\n"
        f"action: what the maintenance engineer should do next, under 10 words."
    )
    try:
        if inner == "claude":
            import anthropic
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is empty. Add it to .env")
            budget.check()
            r = anthropic.Anthropic().with_options(timeout=15.0).messages.parse(
                model=CLAUDE_MODEL, max_tokens=256,
                messages=[{"role": "user", "content": prompt}], output_format=Advice)
            budget.record(CLAUDE_MODEL, r.usage.input_tokens, r.usage.output_tokens)
            a = r.parsed_output
        else:
            with _local_model_lock:      # wait for your turn instead of timing out
                resp = requests.post("http://localhost:11434/api/chat", timeout=120, json={
                    "model": OLLAMA_MODEL, "stream": False,
                    "format": Advice.model_json_schema(),
                    "keep_alive": "30m",          # stop unloading the model between calls
                    "options": {"num_predict": 80,  # hard cap on output length = hard cap on time
                                "temperature": 0.2},
                    "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            a = Advice.model_validate_json(resp.json()["message"]["content"])
        return a.reason, a.action
    except Exception:
        # The model is optional. The severity is not. Degrade, never fail.
        return fallback


PROVIDERS = {"mock": _mock, "ollama": _ollama, "claude": _claude, "hybrid": _hybrid}


def triage(sensor: str, value: float, unit: str, normal_range: str) -> Triage:
    """The rest of the app calls only this. It cannot tell providers apart."""
    if PROVIDER not in PROVIDERS:
        raise RuntimeError(f"Unknown PROVIDER={PROVIDER!r}. Use: {list(PROVIDERS)}")
    return PROVIDERS[PROVIDER](sensor, value, unit, normal_range)
