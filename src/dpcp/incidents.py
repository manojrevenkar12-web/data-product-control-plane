"""Incidents: one per product and kind while it lasts, routed by lineage to the team that can fix the cause."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from dpcp.descriptor import Descriptor
from dpcp.registry import Registry
from dpcp.warehouse import Warehouse


class Incidents:
    def __init__(self, wh: Warehouse, registry: Registry):
        self.wh = wh
        self.registry = registry
        self.table = f"{wh.schema('platform_schema')}.INCIDENTS"
        self.open_now: dict[tuple[str, str], dict] = {}

    def root_of(self, product_id: str, seen: frozenset = frozenset()) -> str:
        """The furthest upstream product with an open incident; the product itself if none."""
        for up in self.registry.upstream(product_id):
            if up in seen:
                continue
            if any((up, k) in self.open_now for k in ("quality", "freshness")):
                return self.root_of(up, seen | {product_id})
        return product_id

    def open(self, d: Descriptor, kind: str, now: datetime, evidence: dict) -> dict:
        key = (d.id, kind)
        if key in self.open_now:
            return self.open_now[key]
        root = self.root_of(d.id) if kind == "freshness" else d.id
        root_d = self.registry.products.get(root, d)
        inc = {
            "incident_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{d.id}/{kind}/{now.isoformat()}")),
            "product": d.id,
            "kind": kind,
            "root_product": root,
            "team": root_d.owner.team,
            "paged": root == d.id,
            "opened_at": now,
            "evidence": evidence,
        }
        self.open_now[key] = inc
        self.wh.run(
            f"insert into {self.table} values (%s, %s, %s, %s, %s, %s, %s, null, %s)",
            (inc["incident_id"], d.id, kind, root, inc["team"], inc["paged"], now, json.dumps(evidence)),
        )
        return inc

    def close(self, product_id: str, kind: str, now: datetime, **_: str) -> None:
        inc = self.open_now.pop((product_id, kind), None)
        if inc:
            self.wh.run(f"update {self.table} set CLOSED_AT = %s where INCIDENT_ID = %s", (now, inc["incident_id"]))

    def all(self) -> list[dict]:
        return self.wh.rows(f"select * from {self.table} order by OPENED_AT, PRODUCT")
