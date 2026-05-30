# depcon-target — FastAPI Example Service

This is the service that Depcon validates. Your job is to implement `main.py` so it
satisfies the contract below. Depcon's smoke tests, Dynatrace queries, and agent
prompts are all written against this exact contract — don't change endpoint paths,
port, or service name without updating `depcon.toml` too.

---

## What you're building

A small FastAPI app (`main.py`) with three endpoints and full OpenTelemetry
instrumentation that ships traces and logs to Dynatrace.

---

## Setup

Install dependencies and create the virtual environment:

```bash
cd examples/fastapi-service
uv sync
```

Copy the env file and fill it in:

```bash
cp .env.example .env
# edit .env — you need DT_OTLP_ENDPOINT and DT_API_TOKEN at minimum
```

Start the server (after writing `main.py`):

```bash
# Normal mode
uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload

# With fault injection
CHAOS_MODE=error uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload

# Windows PowerShell
$env:CHAOS_MODE="error"; uv run uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

---

## Endpoint contract

### `GET /health`

- Returns `200 {"status": "ok"}` always
- Depcon polls this on startup until it gets a 200, so it must never block or fail
- No auth, no side effects

### `POST /run`

- Accepts JSON body: `{"input": "<string>"}`
- Returns `200 {"result": "<string>"}` on success
- Returns `400 {"detail": "..."}` if `input` is missing or empty string
- Returns `500` in fault injection modes (see CHAOS_MODE below)
- This is the endpoint smoke tests hit — keep the logic simple

### `GET /metrics` _(optional)_

- Prometheus-format metrics
- Nice to have but not required for the demo

---

## CHAOS_MODE

Read the `CHAOS_MODE` environment variable at startup (default: `"off"`).
Apply the fault behaviour on every request to `POST /run`:

| Value | Behaviour |
|-------|-----------|
| `off` | Normal — return 200 |
| `latency` | Sleep 800 ms before responding (triggers Dynatrace latency alerts) |
| `error` | Return 500 on ~50% of requests (triggers error rate alerts) |
| `panic` | Raise an unhandled exception after the 3rd request (triggers crash/anomaly alerts) |

The value is set via the environment — you don't need a config file or CLI flag for it.
In Docker it'll be set in `docker-compose.yml`; locally, prefix the `uv run` command.

---

## OpenTelemetry instrumentation

This is the most important part. Without correct OTel output, the Dynatrace queries
in the agent loop will find nothing and the demo breaks.

### What to instrument

Every request to any endpoint must produce:

1. **A trace span** with these attributes:
   - `http.method` — GET / POST
   - `http.route` — /health, /run
   - `http.status_code` — the response code
   - `custom.latency_ms` — how long the handler took in milliseconds
   - `error` — boolean, true if status >= 500 or an exception was raised

2. **Structured log output** routed through the OTel logging handler so logs
   appear in Dynatrace correlated with traces

### Service identity — critical

The service name **must** be `depcon-target`. This is what Dynatrace uses to
identify your service, and it's what the DQL queries in `depcon/tools/dynatrace.py`
filter on. Set it as the OTel resource attribute `service.name`.

If this doesn't match, Dynatrace stores the data under a different service and the
agent finds nothing.

### How to set up OTel in FastAPI (the pieces you need)

You need four things wired together:

**1. A Resource** — tells Dynatrace which service this data belongs to.
Set `service.name = "depcon-target"` and `service.version = "0.1.0"`.
Reference: https://opentelemetry.io/docs/languages/python/resources/

**2. An OTLP exporter** — ships data to Dynatrace over HTTP.
The endpoint comes from `DT_OTLP_ENDPOINT` in your `.env`.
You need to set the `Authorization` header to `Api-Token <DT_API_TOKEN>`.
Reference: https://opentelemetry.io/docs/languages/python/exporters/

**3. A TracerProvider + SpanProcessor** — manages span lifecycle and batches
exports. Use `BatchSpanProcessor` wrapping your OTLP exporter.
Call `trace.set_tracer_provider(...)` to make it global.

**4. FastAPI auto-instrumentation** — `FastAPIInstrumentor().instrument_app(app)`
handles creating spans for every request automatically. You only need to add
custom attributes (like `error` bool and `custom.latency_ms`) inside your handlers
by getting the current span: `trace.get_current_span()`.

**Logging** — attach `LoggingHandler` from `opentelemetry.sdk._logs` so your
`logging.getLogger(...)` calls are forwarded to Dynatrace correlated with traces.

### Packages already in `pyproject.toml`

```
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-http
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-logging
```

Run `uv sync` and they're available.

### Useful references

- Python OTel getting started: https://opentelemetry.io/docs/languages/python/getting-started/
- OTel FastAPI instrumentation: https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html
- Dynatrace OTel ingestion: https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry
- Dynatrace OTLP endpoint setup: https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry/getting-started/otlp-export

---

## Verifying it works

### 1. Check the service is up

```bash
curl http://localhost:8080/health
# expected: {"status":"ok"}
```

### 2. Check the run endpoint

```bash
# Should return 200
curl -X POST http://localhost:8080/run \
  -H "Content-Type: application/json" \
  -d '{"input": "hello"}'

# Should return 400
curl -X POST http://localhost:8080/run \
  -H "Content-Type: application/json" \
  -d '{"input": ""}'
```

### 3. Check traces appear in Dynatrace

1. Go to your Dynatrace tenant → **Services**
2. Look for a service named **`depcon-target`**
3. Click into it → **Distributed traces** → you should see spans from your requests
4. Each span should have `http.status_code`, `http.method`, `http.route`

### 4. Check logs appear in Dynatrace

1. Dynatrace → **Logs** → search for `service.name = "depcon-target"`
2. You should see your app's log lines with trace context attached

### 5. Test a fault mode

```bash
CHAOS_MODE=error uv run uvicorn main:app --host 0.0.0.0 --port 8080

# Fire several requests
for i in {1..6}; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/run \
    -H "Content-Type: application/json" -d '{"input":"test"}'
done
# expect a mix of 200 and 500
```

Then in Dynatrace → **Services → depcon-target → Error analysis** — you should see
error rate spike. This is what the agent diagnoses. If you see it here, the whole
pipeline will work.

---

## Env vars your code needs

| Variable | Used for |
|----------|---------|
| `DT_OTLP_ENDPOINT` | OTLP exporter endpoint URL |
| `DT_API_TOKEN` | Authorization header for Dynatrace |
| `CHAOS_MODE` | Fault injection mode (`off` default) |

Load them with `python-dotenv` at the top of `main.py`:
```python
from dotenv import load_dotenv
load_dotenv()
```

A `.env.example` is in the repo root. Copy it here too if you want a local one:
```bash
cp ../../.env.example .env
```

---

## File layout (what you create)

```
examples/fastapi-service/
├── main.py          ← you write this
├── pyproject.toml   ← already here, don't change
├── README.md        ← this file
└── .env             ← you create from .env.example (never commit)
```

Docker (`Dockerfile` + `docker-compose.yml`) will be added separately — focus on
getting `main.py` working and verified in Dynatrace first.
