"""Freshness SLAs: the newest published data must be younger than the product's promise."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from dpcp.pipeline import Pipeline
from dpcp.registry import Registry


class SlaMonitor:
    def __init__(self, pipeline: Pipeline, registry: Registry):
        self.pipeline = pipeline
        self.registry = registry
        self.latest: dict[str, object] = {}

    def observe_publish(self, product_id: str, data_date) -> None:
        cur = self.latest.get(product_id)
        if cur is None or data_date > cur:
            self.latest[product_id] = data_date

    def age_hours(self, product_id: str, now: datetime) -> float | None:
        latest = self.latest.get(product_id)
        if latest is None:
            return None
        as_of = datetime.combine(latest + timedelta(days=1), time())
        return (now - as_of).total_seconds() / 3600

    def depth(self, pid: str, seen: frozenset = frozenset()) -> int:
        ups = [u for u in self.registry.upstream(pid) if u not in seen]
        return 1 + max((self.depth(u, seen | {pid}) for u in ups), default=-1)

    def run(self, now: datetime) -> list[dict]:
        """Upstream products first, so a stale downstream product is linked to its cause, not paged on its own."""
        opened = []
        order = sorted(self.registry.products.items(), key=lambda kv: (self.depth(kv[0]), kv[0]))
        for pid, d in order:
            age = self.age_hours(pid, now)
            if age is None or d.sla.freshness_hours is None:
                continue
            if age > d.sla.freshness_hours:
                evidence = {
                    "age_hours": round(age, 1),
                    "sla_hours": d.sla.freshness_hours,
                    "latest_data_date": str(self.latest[pid]),
                    "last_quality_incident": (pid, "quality") in self.pipeline.incidents.open_now,
                }
                if (pid, "freshness") not in self.pipeline.incidents.open_now:
                    opened.append(self.pipeline.incidents.open(d, "freshness", now, evidence))
            else:
                self.pipeline.incidents.close(pid, "freshness", now)
        return opened
