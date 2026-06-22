import asyncio
import logging
import re
import time
import httpx
from app.config import Settings
from app.observability import get_tracer

# Transient errors worth retrying
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)

MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds


class SplunkClient:
    def __init__(self, settings: Settings):
        self.base_url = f"https://{settings.SPLUNK_HOST}:{settings.SPLUNK_PORT}"
        self.auth = (settings.SPLUNK_USERNAME, settings.SPLUNK_PASSWORD)
        self._job_timeout = settings.SPLUNK_JOB_TIMEOUT_SECONDS
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            verify=settings.SPLUNK_VERIFY_SSL,
            timeout=30.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    async def execute_spl(self, spl: str) -> list[dict]:
        """Execute an SPL query with retry on transient failures."""
        last_exception: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await self._execute_spl_once(spl)
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exception = exc
                if attempt < MAX_RETRIES:
                    await _async_sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
            except (RuntimeError, httpx.HTTPStatusError):
                raise

        raise ConnectionError(
            f"Cannot reach Splunk at {self.base_url} after {MAX_RETRIES} attempts"
        ) from last_exception

    async def _execute_spl_once(self, spl: str) -> list[dict]:
        from opentelemetry.trace import StatusCode
        tracer = get_tracer()
        with tracer.start_as_current_span("splunk.execute") as span:
            start = time.time()

            clean_spl = spl.strip()
            if re.match(r"^search\s+", clean_spl, re.IGNORECASE):
                clean_spl = re.sub(r"^search\s+", "", clean_spl, count=1, flags=re.IGNORECASE)

            response = await self._client.post(
                "/services/search/jobs",
                data={"search": f"search {clean_spl}", "output_mode": "json"},
            )
            response.raise_for_status()
            sid = response.json()["sid"]
            span.set_attribute("splunk.sid", sid)

            poll_sleep = 0.5
            status_resp = None
            while time.time() - start < self._job_timeout:
                status_resp = await self._client.get(
                    f"/services/search/jobs/{sid}",
                    params={"output_mode": "json"},
                )
                status_resp.raise_for_status()
                state = status_resp.json()["entry"][0]["content"]["dispatchState"]
                if state == "DONE":
                    span.set_attribute("splunk.dispatch_state", "DONE")
                    break
                if state == "FAILED":
                    span.set_attribute("splunk.dispatch_state", "FAILED")
                    span.set_status(StatusCode.ERROR, "Splunk search job failed")
                    raise RuntimeError("Splunk search job failed")
                poll_sleep = min(poll_sleep * 2, 5.0)
                await _async_sleep(poll_sleep)
            else:
                span.set_status(StatusCode.ERROR, f"Splunk search job {sid} timed out")
                raise RuntimeError(f"Splunk search job {sid} timed out after {self._job_timeout}s")

            results_resp = await self._client.get(
                f"/services/search/jobs/{sid}/results",
                params={"output_mode": "json", "count": 10000},
            )
            results_resp.raise_for_status()
            results = results_resp.json().get("results", [])
            span.set_attribute("splunk.result_count", len(results))

            # Check if results were truncated
            if status_resp is not None:
                job_status = status_resp.json()["entry"][0]["content"]
                total_count = job_status.get("resultCount", len(results))
                if total_count > len(results):
                    span.set_attribute("splunk.truncated", True)
                    span.set_attribute("splunk.total_result_count", total_count)
                    logging.getLogger("setuq.splunk").warning(
                        "Splunk results truncated: returned %d of %d total", len(results), total_count
                    )

            span.set_attribute("splunk.duration_ms", int((time.time() - start) * 1000))
            return results

    async def discover_schema(self) -> dict:
        """Discover available indexes and their metadata from Splunk."""
        try:
            response = await self._client.get(
                "/services/data/indexes",
                params={"output_mode": "json", "count": 0},
            )
            response.raise_for_status()
            entries = response.json().get("entry", [])
            indexes = {}
            for entry in entries:
                name = entry["name"]
                if name.startswith("_"):
                    continue
                indexes[name] = {"sourcetypes": {}}
            return {"indexes": indexes}
        except httpx.ConnectError:
            raise ConnectionError(
                f"Cannot reach Splunk at {self.base_url}"
            )

    async def close(self):
        await self._client.aclose()


async def _async_sleep(seconds: float):
    await asyncio.sleep(seconds)
