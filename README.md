# Nexus AI Triage — Workshop Starter

A tiny full-stack app: something sends a sensor reading, an AI decides how
serious it is, a dashboard shows the result.

Works for **hardware teams** (an ESP32 POSTs to `/ingest`) and
**software teams** (a browser form POSTs to `/ingest`). Same endpoint.

## Run it in 3 commands

```bash
pip install -r requirements.txt
cp .env.example .env        # PROVIDER=mock needs no key at all
uvicorn app.main:app --reload
```

Open http://localhost:8000

## The three providers

Set `PROVIDER` in `.env`. Nothing else in the app changes.

| `PROVIDER` | Cost | Needs | Use when |
|---|---|---|---|
| `mock` | free | nothing | learning the structure; demo-day fallback |
| `ollama` | free | Ollama + `ollama pull llama3.2:1b` | real AI, no money, works offline |
| `claude` | ~$0.0013/call | `ANTHROPIC_API_KEY` | best quality |
| `hybrid` | same as its backend | `HYBRID_BACKEND` | **ship this one** |

## Measured on this repo (8 cases x 3 runs, `bench.py`)

Objective test: a value inside the range must classify `normal`; outside must not.

| Provider | Correct | Unstable | Latency |
|---|---|---|---|
| `mock` (pure arithmetic) | **100%** | 0/8 | 0.002 ms |
| `ollama` llama3.2:**1b** | 38% | 5/8 | 6.1 s |
| `ollama` llama3.2:**3b** | 83% | 4/8 | 9.0 s |
| `hybrid` (code + 3b) | **100%** | **0/8** | 7.5 s median, 26 s worst |

Reproduce: `python bench.py llama3.2:3b` and `python bench.py hybrid:llama3.2:3b`

### Read that table again

`llama3.2:3b` classified **87 C as `normal`** when the normal range was 20-60 C,
on two of three runs. That is a dangerous false negative in a safety app.

**Never ask a language model to compare two numbers.** `low <= value <= high`
is free, instant, always correct, and unit-testable. Use the model for the thing
code cannot do: writing a sentence a human actually wants to read.

That is exactly what `hybrid` does, and why it scores 100%.

## ⚠️ The trap that will cost you a day

`ollama` talks to `http://localhost:11434`.

- On **your laptop**, `localhost` = your laptop. Ollama is there. Works.
- On a **cloud server**, `localhost` = the server. Ollama is NOT there. Fails.

You cannot fix this by installing Ollama on a free server: free tiers have
roughly 512MB of RAM, a small model wants around 4GB.

So pick your target *before* you build:

- **Deploying to a public URL?** Use `claude` (or any cloud API).
- **Running on a laptop at your booth?** Use `ollama`. No wifi needed. Free.

Both work with this code. That is the point of `app/ai.py`.

## Where the API key lives

```
static/index.html   PUBLIC   anyone can read every character. NO KEY HERE.
app/ai.py           PRIVATE  runs on the server. holds the key.
```

The browser never sees the key. It only ever talks to your own `/ingest`.

## Files

```
app/ai.py        the only file that knows about AI providers or keys
app/main.py      /ingest  /readings  /health  + serves the frontend
static/index.html  the dashboard, plain HTML, no build step
compare.py       runs one reading through all 3 providers
Dockerfile       for deploying
```

## Never commit your key

`.env` is already in `.gitignore`. If you ever push a key to GitHub,
**assume it is stolen** and rotate it immediately. Deleting the commit is
not enough; it stays in the git history.


---

## The async pattern (why `/ingest` returns before the AI finishes)

Measured on this repo:

| Step | Time |
|---|---|
| `POST /ingest` returns with a correct severity | **~30 ms** |
| the AI explanation arrives afterwards | **9-50 s** |

Same input, five warm runs of the model: 4.7s, 4.9s, 7.5s, 17.1s, 26.4s.
A 5.6x spread. You cannot make a judge stand at your booth for that.

So the endpoint does the fast half and queues the slow half:

```python
severity, low, high = ai.severity_of(r.value, r.normal_range)  # instant, code
... INSERT with ai_status='pending' ...
background.add_task(_fill_in_wording, ...)                     # queued, not awaited
return {"id": ..., "severity": severity, "ai_status": "pending"}
```

The browser shows the severity at once, then polls `GET /readings/{id}`
until `ai_status` flips to `done`.

## Concurrency: what happens when 5 people press the button at once

A local model handles **one request at a time**. Measured, 5 concurrent calls:

| | Fallbacks | Wall clock |
|---|---|---|
| no queue, 60s timeout | **3 of 5 timed out** | 62.6 s |
| one-at-a-time lock, 120s timeout | **0 of 5** | 49.1 s |

Letting everyone start at once made it *slower and less reliable*. The fix is
three lines in `app/ai.py`:

```python
_local_model_lock = threading.Semaphore(1)
...
with _local_model_lock:
    resp = requests.post("http://localhost:11434/api/chat", ...)
```

That is what "scalable" means at your scale. Not Kubernetes. A lock.

## Degrade, never fail

`explain()` never raises. If the model is down, slow, or unreachable, the
reading is still stored with a **correct severity** and a plain-English
fallback. During all the failures above, no user ever saw an error.

Your booth demo should be built so the AI is the part that is allowed to fail.


---

# Module 3: Secrets and spend

## Before every push

```bash
python scripts/check_secrets.py
```

Exit code 1 means do not push. It catches both leaks that actually happen:
a key pasted into source, and a `.env` that got past `.gitignore`.

## Why "I deleted it" does not work

Commit a key, then delete it in the next commit. The file is clean:

```python
client = anthropic.Anthropic()   # removed the key, phew
```

Now anyone who clones the repo runs one command:

```bash
git show <old-commit>:app.py
# client = anthropic.Anthropic(api_key="sk-ant-api03-...")
```

Git stores every version forever. Deleting a line adds a new commit; it does
not remove the old one. **A key that has been pushed is a key that is gone.
Rotate it.** Rewriting history (`filter-repo`, BFG) does not help either: by
then it has been cloned, cached, and scanned by bots.

Providers scan public repos and auto-revoke keys they find. Your app dies with
a 401 at the worst possible moment, which is usually during judging.

## The chain, in order

| Where | What lives there |
|---|---|
| `.env` on your laptop | the real key. Never committed. |
| `.gitignore` | contains `.env`. Already set up in this repo. |
| `.env.example` | placeholder only. Safe to commit. Shows others what to fill in. |
| hosting platform env vars | the real key in production. Set in their dashboard, not in code. |
| `static/index.html` | **nothing.** The browser must never see a key. |

## Two spend caps, not one

**1. In the Console.** Set a hard monthly limit on your account. This is the
one that actually protects you. Do it before you write any code.

**2. In the app** (`app/budget.py`). Checks *before* sending the request:

```python
budget.check()                                        # raises if over cap
r = client.messages.parse(...)
budget.record(MODEL, r.usage.input_tokens, r.usage.output_tokens)
```

Cost comes from the token counts the API reports back, not a guess.
Check anytime at `GET /usage`.

Runaway loop with the cap set to $0.01, measured:

```
  call  1: spent $0.0012  total $0.0013
  ...
  call  8: spent $0.0012  total $0.0100
  call  9: BLOCKED - Daily spend cap reached ($0.0100/$0.01)
```

## What things actually cost

One triage call is roughly 500 input + 150 output tokens.

| Model | Per call | What $5 buys |
|---|---|---|
| `claude-haiku-4-5` | $0.00125 | ~4,000 calls |
| `claude-opus-5` | $0.00625 | ~800 calls (5x haiku) |

30 teams x 200 calls each on Haiku = **$7.50 total.** Cost is not your
problem. A leaked key is.


---

# Two front-ends, one backend

```
   ESP32 / sensor ----POST /ingest----+
                                      |
   static/index.html -----------------+---> FastAPI (app/main.py)
                                      |         severity from code
   streamlit_app.py ------------------+         wording from a model
```

`static/index.html` and `streamlit_app.py` do the same job. Swap freely.
**The API is the product; the UI is interchangeable.**

## Why not Streamlit only?

Streamlit has no route handlers. There is no `POST /ingest` in it, so an
ESP32 cannot send it anything. If your prototype has hardware, you need the
FastAPI service. If it is software only, Streamlit alone is fine and is far
less code.

```bash
pip install streamlit
streamlit run streamlit_app.py        # reads API_URL, defaults to localhost
```

## Free hosting, checked August 2026

| | Render (FastAPI) | Streamlit Community Cloud |
|---|---|---|
| Credit card | not required | not required |
| Memory | 512 MB | ~1 GB |
| Sleeps after | **15 minutes** | **12 hours** |
| Cold start | 30-60 s | - |
| Accepts a device POST | **yes** | **no** |

The 15-minute sleep is a demo-day trap: a judge clicks your link and waits
a minute at a blank tab. Plan for it (a keep-alive ping, or open the page
yourself before judging starts).

## Do you need a real API key to deploy?

No. Deploy with `HYBRID_BACKEND=mock` and everything works: Docker, env vars,
a public URL, the cold-start behaviour. Add a key later, in the dashboard.

But be clear about what is possible where:

| Where it runs | Real AI | Free | Key needed |
|---|---|---|---|
| Laptop at your booth | yes, Ollama | yes | no |
| Cloud URL | no, mock only | yes | no |
| Cloud URL | yes, Claude | no (cents) | yes |

**There is no free real-AI option in the cloud here.** Ollama needs roughly
4 GB; Render free gives 512 MB. That is the localhost trap restated as a
number. Pick your target before you build.
