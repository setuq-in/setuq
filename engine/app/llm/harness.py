from __future__ import annotations
import asyncio
import contextvars
import random
from dataclasses import dataclass

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class _RunBudget:
    max_tokens: int
    max_cost_usd: float
    used_tokens: int = 0
    used_cost_usd: float = 0.0

    def check_and_add(self, usage: LLMUsage | None) -> None:
        if usage is None:
            return
        self.used_tokens += usage.input_tokens + usage.output_tokens
        self.used_cost_usd += usage.cost_usd
        if self.used_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"Token budget exceeded: {self.used_tokens} > {self.max_tokens}"
            )
        if self.used_cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"Cost budget exceeded: ${self.used_cost_usd:.4f} > ${self.max_cost_usd:.4f}"
            )


_run_budget: contextvars.ContextVar[_RunBudget | None] = contextvars.ContextVar(
    "llm_run_budget", default=None
)


def init_run_budget(max_tokens: int, max_cost_usd: float) -> contextvars.Token:
    """Call at start of a pipeline run. Returns token for reset."""
    return _run_budget.set(_RunBudget(max_tokens=max_tokens, max_cost_usd=max_cost_usd))


def reset_run_budget(token: contextvars.Token) -> None:
    _run_budget.reset(token)


def get_run_budget_usage() -> tuple[int, float]:
    """Current run's (total_tokens, total_cost_usd). (0, 0.0) if no budget active."""
    b = _run_budget.get()
    if b is None:
        return (0, 0.0)
    return (b.used_tokens, b.used_cost_usd)


def _is_retryable(exc: BaseException) -> bool:
    import httpx
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    try:
        import openai
        if isinstance(exc, openai.RateLimitError):
            return True
    except ImportError:
        pass
    try:
        import anthropic
        if isinstance(exc, anthropic.RateLimitError):
            return True
    except ImportError:
        pass
    return False


def _is_auth_or_quota(exc: BaseException) -> bool:
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 402, 403)
    try:
        import anthropic
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return True
    except ImportError:
        pass
    try:
        import openai
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return True
    except ImportError:
        pass
    return False


class CircuitOpen(Exception):
    """Raised when the auth/quota circuit breaker is open."""


class _AuthCircuit:
    """Opens after `threshold` consecutive auth/quota errors.
    After `open_seconds` elapses allows one half-open probe; closes on success."""

    def __init__(self, threshold: int = 5, open_seconds: float = 60.0):
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    @property
    def open(self) -> bool:
        return self.opened_at is not None

    def check(self) -> None:
        if self.opened_at is None:
            return
        import time as _time
        if _time.monotonic() - self.opened_at >= self.open_seconds:
            # Half-open: allow one probe through by clearing opened_at
            self.opened_at = None
            return
        raise CircuitOpen(
            f"LLM circuit open after {self.consecutive_failures} consecutive auth/quota errors"
        )

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_auth_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            import time as _time
            self.opened_at = _time.monotonic()


class HarnessedProvider(LLMProvider):
    """Wraps any LLMProvider with retry, timeout, and budget enforcement."""

    def __init__(
        self,
        base: LLMProvider,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        auth_failure_threshold: int = 5,
    ):
        self._base = base
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._circuit = _AuthCircuit(threshold=auth_failure_threshold)

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        user_prompt: str,
    ) -> LLMResponse:
        self._circuit.check()
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._base.generate(system_prompt, history, user_prompt),
                    timeout=self._timeout,
                )
                budget = _run_budget.get()
                if budget is not None:
                    budget.check_and_add(response.usage)
                self._circuit.record_success()
                return response
            except BudgetExceeded:
                raise  # never retry budget errors
            except asyncio.TimeoutError as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    break
                await asyncio.sleep(2 ** (attempt - 1) * (0.5 + random.random()))
            except Exception as exc:
                last_exc = exc
                if _is_auth_or_quota(exc):
                    self._circuit.record_auth_failure()
                    raise
                if not _is_retryable(exc) or attempt == self._max_retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1) * (0.5 + random.random()))
        raise RuntimeError(
            f"LLM generate failed after {self._max_retries} attempts"
        ) from last_exc
