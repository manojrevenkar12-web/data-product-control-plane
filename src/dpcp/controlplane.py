"""The control plane: what happens when a team opens a pull request on its descriptor, or asks for access.

`propose` is the same code path for a new product and for a change: parse, policies, contract diff against
what is live, consumers from lineage, and on acceptance the warehouse objects. With gates off (the monitoring-only
design) the policies still run, but their findings are recorded instead of blocking.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from pydantic import ValidationError

from dpcp.contract import change_input
from dpcp.descriptor import Descriptor
from dpcp.pipeline import Pipeline
from dpcp.policy import PolicyEngine
from dpcp.registry import Grant, Registry
from dpcp.settings import classification_rank


def parse(doc: dict) -> tuple[Descriptor | None, list[dict]]:
    try:
        return Descriptor.model_validate(doc), []
    except ValidationError as e:
        return None, [
            {
                "rule": "schema",
                "field": ".".join(str(x) for x in err["loc"]),
                "msg": err["msg"],
                "fix": "see products/ for the reference descriptors",
            }
            for err in e.errors()
        ]


class ControlPlane:
    def __init__(self, pipeline: Pipeline, registry: Registry, policy: PolicyEngine):
        self.pipeline = pipeline
        self.registry = registry
        self.policy = policy
        self.wh = pipeline.wh
        self.gates = pipeline.gates

    def review(self, doc: dict) -> dict:
        """What CI says on the pull request. Nothing is changed."""
        d, errors = parse(doc)
        if d is None:
            return {"descriptor": None, "violations": errors, "change": None}
        violations = self.policy.product_violations([d.policy_input()]).get(d.id, [])
        live = self.registry.products.get(d.id)
        change = None
        if live is not None:
            change = change_input(d.id, live.contract, d.contract, self.registry.consumers_of(d.id))
            violations += self.policy.change_violations([change]).get(d.id, [])
        return {"descriptor": d, "violations": violations, "change": change}

    def propose(self, doc: dict, now: datetime) -> dict:
        res = self.review(doc)
        d, violations, change = res["descriptor"], res["violations"], res["change"]
        accepted = d is not None and (not violations or not self.gates)
        live = self.registry.products.get(d.id) if d else None
        if accepted:
            if (
                live is not None
                and change
                and change["breaking"]
                and not self.gates
                and d.contract.major == live.contract.major
            ):
                self.pipeline.apply_in_place(live, d)
            self.pipeline.ensure_product(d)
            self.registry.put(d)
        status = "accepted" if accepted and not violations else "accepted_with_findings" if accepted else "rejected"
        self.wh.run(
            f"insert into {self.pipeline.platform}.CHANGES values (%s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                d.id if d else doc.get("id", "?"),
                live.contract.version if live else None,
                d.contract.version if d else None,
                now,
                status,
                json.dumps(violations),
            ),
        )
        return {"status": status, "violations": violations, "change": change, "descriptor": d}

    def request_access(self, requests: list[dict]) -> dict[str, dict]:
        """requests: id, principal {name, kind, domain}, product, port, purpose, columns."""
        enriched = []
        for r in requests:
            d = self.registry.products[r["product"]]
            enriched.append(
                {**r, "product": {"id": d.id, "domain": d.domain, "classification": d.classification, "ports": d.ports}}
            )
        decisions = self.policy.access_decisions(enriched)
        for r in requests:
            dec = decisions[r["id"]]
            if dec["allow"]:
                p = r["principal"]
                self.registry.grants.append(
                    Grant(
                        r["id"],
                        p["name"],
                        p["kind"],
                        p["domain"],
                        r["product"],
                        r["port"],
                        list(r.get("columns") or []),
                        dec["unmasked"],
                    )
                )
        return decisions

    def agent_ready(self, d: Descriptor) -> bool:
        limit = self.wh.settings["policy"]["agent_max_classification"]
        return classification_rank(d.classification) <= classification_rank(limit) and bool(
            {"mcp", "a2a"} & set(d.ports)
        )
