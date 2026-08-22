"""Run the SAME reading through every provider. This generates your slide."""
import os, time
from dotenv import load_dotenv
load_dotenv()

CASES = [
    ("water_temp",  87.0, "C",  "20-60 C"),
    ("water_temp",  55.0, "C",  "20-60 C"),
    ("soil_moisture", 8.0, "%", "30-70 %"),
]

for provider in ["mock", "ollama", "claude"]:
    os.environ["PROVIDER"] = provider
    import importlib; from app import ai; importlib.reload(ai)
    print(f"\n{'='*66}\n  PROVIDER: {provider}\n{'='*66}")
    for sensor, value, unit, rng in CASES:
        t0 = time.time()
        try:
            r = ai.triage(sensor, value, unit, rng)
            print(f"  {sensor} = {value}{unit}  (normal {rng})   [{time.time()-t0:.2f}s]")
            print(f"    severity : {r.severity}")
            print(f"    reason   : {r.reason}")
            print(f"    action   : {r.action}")
        except Exception as e:
            print(f"  {sensor} = {value}{unit}  ->  SKIPPED: {e}")
