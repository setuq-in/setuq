# Setuq benchmark baseline

## Provider / scale matrix

| Provider | Backend | KW pass | p95 ms | $/query |
|----------|---------|---------|--------|---------|
| anthropic | memory | skipped | — | — |

### Skipped / not-run cells

Cells marked `skipped` above either lack credentials, are unreachable, or errored on every query. Reason per cell (a real error here is NOT a benign 'not configured' -- inspect it):

- `anthropic` / `memory`: AuthenticationError: Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, 'request_id': 'req_011Ccyh48wQCZJmFGuM4ZzMz'}

### Known limitations

- The `redis` session backend is not yet wired into the bench harness (`build_bench_orchestrator` uses in-memory `SessionManager`); `--backends redis` rows are reported as `skipped`, not measured.
