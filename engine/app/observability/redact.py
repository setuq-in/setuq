import hashlib
import re

_SENSITIVE_KEYS = re.compile(r"(password|token|api_key|authorization|secret)", re.IGNORECASE)


def hash_query(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def scrub_dict(d: dict) -> dict:
    return {
        k: "[REDACTED]" if _SENSITIVE_KEYS.search(k) else v
        for k, v in d.items()
    }
