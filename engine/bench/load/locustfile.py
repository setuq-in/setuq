"""Locust load harness for Setuq's FastAPI app.

Run against a live `uvicorn app.main:app` started with a local, non-paid
LLM provider (see README.md in this directory) so throughput reflects
pipeline + HTTP capacity rather than provider latency.

    cd engine && locust -f bench/load/locustfile.py --host http://localhost:8000
"""
import json
import random

from locust import HttpUser, task, between

_EASY = [
    "List all events from yesterday in the firewall index",
    "Show me top 5 user agents seen today",
    "Count alerts grouped by severity in the last 24 hours",
]
_MULTI = [
    "Investigate why our auth service crashed yesterday — find the root cause across logs and metrics",
    "Compare login failure rates this week vs last week and identify which users had the biggest increase",
]
_GUARD = [
    "DELETE all logs older than a year from the main index",
    "Show me everything across all indexes for the last 365 days",
]


def _pick() -> str:
    r = random.random()
    if r < 0.70:
        return random.choice(_EASY)
    if r < 0.90:
        return random.choice(_MULTI)
    return random.choice(_GUARD)


class SetuqUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(4)
    def query(self):
        self.client.post("/api/query", json={"query": _pick()}, name="POST /api/query")

    @task(1)
    def stream(self):
        with self.client.get(
            "/api/query/stream",
            params={"query": random.choice(_EASY)},
            stream=True,
            name="GET /api/query/stream",
            catch_response=True,
        ) as resp:
            saw_terminal = False
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8", errors="ignore") if isinstance(line, bytes) else line
                if not text.startswith("data:"):
                    continue
                try:
                    payload = json.loads(text[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
                if payload.get("step") in ("done", "error"):
                    saw_terminal = True
                    break
            resp.success() if saw_terminal else resp.failure("no terminal SSE event")
