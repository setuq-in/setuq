from __future__ import annotations


def sample_for_llm(rows: list[dict], k: int = 60) -> tuple[list[dict], dict]:
    """Return a stratified (head+mid+tail) sample of `rows` capped at k entries.

    Also returns a sketch dict with total_rows, sampled count, and field names
    observed in the first 200 rows — pass the sketch as extra context to agents.
    """
    n = len(rows)
    if n == 0:
        return [], {"total_rows": 0, "sampled": 0, "fields": []}

    if n <= k:
        sample = rows
    else:
        third = max(1, k // 3)
        head = rows[:third]
        tail = rows[-third:]
        remaining_k = k - len(head) - len(tail)
        mid_pool = rows[third : n - third]
        if remaining_k > 0 and mid_pool:
            step = max(1, len(mid_pool) // remaining_k)
            mid = mid_pool[::step][:remaining_k]
        else:
            mid = []
        sample = head + mid + tail

    fields = sorted({f for r in rows[:200] for f in r.keys()})
    sketch: dict = {"total_rows": n, "sampled": len(sample), "fields": fields}
    return sample, sketch
