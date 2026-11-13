"""One serving path for every output port. API, MCP and A2A call this; none of them touches SQL or policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dpcp.controlplane import ControlPlane
from dpcp.registry import Registry
from dpcp.warehouse import Warehouse


class Denied(PermissionError):
    pass


class NotFound(LookupError):
    pass


@dataclass(frozen=True)
class Principal:
    name: str
    kind: str  # human | service | agent
    domain: str


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ProductService:
    def __init__(self, wh: Warehouse, registry: Registry, control: ControlPlane):
        self.wh = wh
        self.registry = registry
        self.control = control
        self.serve = wh.schema("serve_schema")
        self.platform = wh.schema("platform_schema")
        self.page = wh.settings["ports"]["api_page_size"]
        self.keys: dict[str, Principal] = {}

    def add_key(self, key: str, principal: Principal) -> None:
        self.keys[key_hash(key)] = principal

    def principal(self, key: str | None) -> Principal:
        p = self.keys.get(key_hash(key or ""))
        if p is None:
            raise Denied("unknown API key")
        return p

    def catalogue(self, who: Principal) -> list[dict]:
        out = []
        for d in sorted(self.registry.products.values(), key=lambda x: x.id):
            if who.kind == "agent" and not self.control.agent_ready(d):
                continue
            out.append(
                {
                    "id": d.id,
                    "domain": d.domain,
                    "description": d.description,
                    "owner": d.owner.team,
                    "classification": d.classification,
                    "ports": d.ports,
                    "version": d.contract.version,
                }
            )
        return out

    def describe(self, who: Principal, product_id: str) -> dict:
        d = self.registry.products.get(product_id)
        if d is None or (who.kind == "agent" and not self.control.agent_ready(d)):
            raise NotFound(product_id)
        latest = self.wh.run(
            f"select max(DATA_DATE) from {self.platform}.BATCHES where PRODUCT = %s and STATUS like 'published%%'",
            (product_id,),
        )[0][0]
        return {
            "id": d.id,
            "description": d.description,
            "owner": d.owner.model_dump(),
            "version": d.contract.version,
            "key": d.contract.key,
            "sla_freshness_hours": d.sla.freshness_hours,
            "latest_data_date": str(latest) if latest else None,
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "unit": c.unit,
                    "description": c.description,
                    "masked_for_you": c.pii and not self._unmasked(who, product_id),
                }
                for c in d.contract.columns
            ],
        }

    def _unmasked(self, who: Principal, product_id: str) -> bool:
        g = next((g for g in self.registry.grants if g.principal == who.name and g.product == product_id), None)
        return bool(g and g.unmasked)

    def read(
        self,
        who: Principal,
        product_id: str,
        port: str,
        columns: list[str] | None = None,
        filters: dict | None = None,
        limit: int | None = None,
    ) -> dict:
        d = self.registry.products.get(product_id)
        if d is None:
            raise NotFound(product_id)
        grant = self.registry.grant_for(who.name, product_id, port)
        if grant is None:
            raise Denied(f"{who.name} has no {port} grant on {product_id}; request access first")
        names = [c.name for c in d.contract.columns]
        cols = columns or names
        unknown = [c for c in [*cols, *(filters or {})] if c not in names]
        if unknown:
            raise NotFound(f"unknown columns: {', '.join(unknown)}")
        view = f"{self.serve}.{d.table}{'__FULL' if grant.unmasked else ''}"
        where = " and ".join(f"{k.upper()} = %s" for k in (filters or {}))
        sql = (
            f"select {', '.join(c.upper() for c in cols)} from {view}"
            + (f" where {where}" if where else "")
            + f" order by {', '.join(k.upper() for k in d.contract.key if k in cols) or cols[0].upper()}"
            + f" limit {min(limit or self.page, self.page)}"
        )
        self.wh.tag(team=who.domain, product=product_id, job=f"port:{port}")
        rows = self.wh.rows(sql, tuple((filters or {}).values()) or None)
        rows = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
        return {"product": product_id, "version": d.contract.version, "masked": not grant.unmasked, "rows": rows}
