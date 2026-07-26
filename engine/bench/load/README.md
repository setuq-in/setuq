# Load test (Phase 2)

Locust harness for `POST /api/query` and `GET /api/query/stream`, weighted
70% easy / 20% multi-step / 10% guardrail-triggering queries, plus an SSE
task that asserts every stream ends with a `done` or `error` step event.

## Provider note

There is **no `stub`/`mock` `LLM_PROVIDER`** wired into the running app —
`app/llm/factory.py` only accepts `openai`, `anthropic`, `gemini`, or
`ollama` (see `_make_base_provider`). To load-test without paying for (or
being bottlenecked by) a hosted LLM, run the app against a **local Ollama**
instance instead:

    ollama pull llama3.1        # any small local model works
    ollama serve                # default http://localhost:11434

    cd engine
    LLM_PROVIDER=ollama LLM_MODEL=llama3.1 RATE_LIMIT_ENABLED=false \
      uvicorn app.main:app --port 8000

This still isn't zero-latency (Ollama does real inference), but it avoids
paid API calls and external network variance, keeping the load test focused
on pipeline + HTTP capacity rather than a third-party provider's latency.
`API_KEY` is empty by default (dev mode, auth disabled) so no bearer token
is needed against a local run.

## Run 1 — raw capacity (limiter off)

Start the app with `RATE_LIMIT_ENABLED=false` (above), then ramp 1→100 users:

    cd engine
    locust -f bench/load/locustfile.py --host http://localhost:8000 \
      --users 100 --spawn-rate 10 --run-time 3m --headless

Record RPS, p95/p99 latency, error %, and the "knee" (user count where
latency starts climbing faster than throughput).

### Interpreting error% in Run 1

Some of the reported error% is intentional guardrail rejection (HTTP 422) from the ~10% guardrail-query bucket, not infra failures. To measure raw capacity, exclude these 422s from your count (or re-run without the guardrail bucket). Note: guardrail hits depend on the LLM's SPL translation of raw NL prompts; local Ollama may not reliably trigger them, so error% will vary by model.

## Run 2 — limiter correctness (limiter on)

Restart the app without `RATE_LIMIT_ENABLED=false` (i.e. leave it at its
default `true`, or set it explicitly):

    cd engine
    LLM_PROVIDER=ollama LLM_MODEL=llama3.1 uvicorn app.main:app --port 8000

Then run the same load:

    locust -f bench/load/locustfile.py --host http://localhost:8000 \
      --users 100 --spawn-rate 10 --run-time 3m --headless

Expect `429 Too Many Requests` once a client IP exceeds `RATE_LIMIT_PER_IP`
(default `60/minute`). Verify the 429 rate roughly tracks
`(sustained RPS per IP - 1)/min` above the limit, and that SSE tasks still
end with a terminal `done`/`error` event (not a hang) even while rate-limited.

## Dry-run (no app needed)

Confirms the file parses and registers `SetuqUser` without requiring a
running server (connection errors are expected/fine):

    cd engine
    locust -f bench/load/locustfile.py --host http://localhost:8000 \
      --headless -u 1 -r 1 --run-time 3s
