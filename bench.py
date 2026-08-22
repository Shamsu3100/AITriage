"""Objective test: if the value is INSIDE the range the answer must be
'normal'; if OUTSIDE it must NOT be 'normal'. No opinion involved."""
import os, sys, time, importlib, statistics

CASES = [  # sensor, value, unit, range, expected_inside
    ("water_temp",    55, "C", "20-60 C",  True),
    ("water_temp",    87, "C", "20-60 C",  False),
    ("water_temp",    35, "C", "20-60 C",  True),
    ("soil_moisture",  8, "%", "30-70 %",  False),
    ("soil_moisture", 50, "%", "30-70 %",  True),
    ("pressure",     101, "kPa", "95-105 kPa", True),
    ("pressure",     140, "kPa", "95-105 kPa", False),
    ("rpm",         1200, "rpm", "800-1500 rpm", True),
]
RUNS = 3

model = sys.argv[1]
if model.startswith("hybrid:"):
    os.environ["PROVIDER"] = "hybrid"; os.environ["OLLAMA_MODEL"] = model.split(":",1)[1]
else:
    os.environ["PROVIDER"] = "ollama"; os.environ["OLLAMA_MODEL"] = model
from app import ai; importlib.reload(ai)

ok = total = 0; lat = []; flips = 0
for sensor, value, unit, rng, inside in CASES:
    answers = []
    for _ in range(RUNS):
        t0 = time.time()
        try:
            sev = ai.triage(sensor, value, unit, rng).severity
        except Exception as e:
            sev = f"ERR:{e}"
        lat.append(time.time() - t0); answers.append(sev)
        total += 1
        if (sev == "normal") == inside:
            ok += 1
    if len(set(answers)) > 1:
        flips += 1
    mark = "OK " if all((a == "normal") == inside for a in answers) else "XX "
    print(f"  {mark}{sensor:14}{value:>5}{unit:<4} in {rng:<13} expect "
          f"{'normal':<8} " .replace("normal", "normal" if inside else "NOT normal")
          + f"got {answers}")

print(f"\n  MODEL {model}")
print(f"  correct     : {ok}/{total}  ({100*ok/total:.0f}%)")
print(f"  unstable    : {flips}/{len(CASES)} cases gave different answers across {RUNS} runs")
print(f"  latency     : median {statistics.median(lat):.1f}s   max {max(lat):.1f}s")
