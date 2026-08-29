from collections import defaultdict
from contextlib import suppress

# Optional prometheus bridge will be set by main if prometheus-client is available
_PROM_COUNTER = None


def _set_prom_counter(counter):
    global _PROM_COUNTER
    _PROM_COUNTER = counter


class Metrics:
    """Simple in-memory metrics collector for counters.

    If a Prometheus counter is registered via `_set_prom_counter`, `inc`
    will also increment the prometheus counter with label `name`.
    """

    def __init__(self) -> None:
        self.counters: defaultdict[str, int] = defaultdict(int)

    def inc(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount
        if _PROM_COUNTER is not None:
            # best-effort; do not fail application if prometheus fails
            with suppress(Exception):
                _PROM_COUNTER.labels(name=key).inc(amount)

    def get(self, key: str) -> int:
        return self.counters.get(key, 0)


def set_prometheus_counter(counter):
    _set_prom_counter(counter)
