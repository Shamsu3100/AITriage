"""Alternative front-end. Same backend, zero HTML, zero JavaScript.

    pip install streamlit
    streamlit run streamlit_app.py

This does NOT replace app/main.py. It replaces static/index.html.
The FastAPI service still owns /ingest, which is the endpoint your ESP32
posts to. Streamlit has no route handlers, so a device cannot talk to it.
"""
import os
import time

import requests
import streamlit as st

API = os.getenv("API_URL", "http://127.0.0.1:8086")

PRESETS = {
    "Healthcare": [
        ("Body temperature", "body_temp", "C", 36.1, 37.2, 39.4),
        ("Heart rate", "heart_rate", "bpm", 60, 100, 142),
        ("Blood oxygen", "spo2", "%", 95, 100, 88),
    ],
    "Sustainable Solutions": [
        ("Indoor CO2", "co2", "ppm", 400, 1000, 1450),
        ("Room power draw", "power_draw", "W", 0, 3500, 4800),
        ("Waste bin fill", "bin_fill", "%", 0, 80, 96),
    ],
    "Global Impact": [
        ("Soil moisture", "soil_moisture", "%", 30, 70, 8),
        ("Water tank level", "tank_level", "%", 20, 90, 12),
        ("Motor temperature", "motor_temp", "C", 20, 60, 87),
    ],
}
COLOUR = {"normal": "green", "warning": "orange", "critical": "red"}

st.title("AI Triage Dashboard")
st.caption("One codebase. Pick any track; the backend never changes.")

track = st.selectbox("Track", list(PRESETS))
label = st.selectbox("Scenario", [p[0] for p in PRESETS[track]])
name, sensor, unit, low, high, demo = next(p for p in PRESETS[track] if p[0] == label)

col1, col2 = st.columns([1, 2])
value = col1.number_input(f"Reading ({unit})", value=float(demo), step=0.1)
col2.metric("Safe range", f"{low} - {high} {unit}")

if st.button("Send reading", type="primary"):
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{API}/ingest", timeout=10, json={
            "sensor": sensor, "value": value, "unit": unit,
            "normal_range": f"{low}-{high} {unit}"}).json()
    except requests.exceptions.RequestException as e:
        st.error(f"Backend unreachable at {API}. Is FastAPI running?  ({e})")
        st.stop()

    ms = (time.perf_counter() - t0) * 1000
    st.subheader("1 - from code")
    st.markdown(f":{COLOUR[r['severity']]}[**{r['severity'].upper()}**]")
    st.caption(f"rule: {low} <= reading <= {high}   answered in {ms:.0f} ms")

    st.subheader("2 - from the AI model")
    box, t1 = st.empty(), time.perf_counter()
    with st.spinner("waiting for the model..."):
        while time.perf_counter() - t1 < 180:
            row = requests.get(f"{API}/readings/{r['id']}", timeout=10).json()
            if row["ai_status"] == "done":
                box.markdown(f"{row['reason']}  \n*{row['action']}*")
                st.caption(f"written by the model in {time.perf_counter()-t1:.1f}s")
                break
            time.sleep(0.5)

st.divider()
if st.button("Clear history"):
    requests.delete(f"{API}/readings", timeout=10)

try:
    rows = requests.get(f"{API}/readings", timeout=10).json()
    if rows:
        st.dataframe([{
            "time": (r["ts"] or "")[11:],
            "reading": f"{r['sensor']} {r['value']}{r['unit']}",
            "severity (code)": r["severity"],
            "explanation (AI)": r["reason"] if r["ai_status"] == "done" else "queued...",
        } for r in rows], use_container_width=True, hide_index=True)
    else:
        st.caption("no readings yet")
except requests.exceptions.RequestException:
    st.warning(f"Cannot reach {API}")
