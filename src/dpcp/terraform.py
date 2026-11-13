"""Render the Terraform inputs from the descriptors. The module in infra/modules/ does the rest."""

from __future__ import annotations

import json
from pathlib import Path

from dpcp.descriptor import Descriptor


def role_names(d: Descriptor) -> dict[str, str]:
    base = f"DP_{d.id.replace('.', '__').upper()}"
    return {"reader": f"{base}_READER", "reader_full": f"{base}_READER_FULL", "writer": f"{base}_WRITER"}


def team_warehouse(team: str) -> str:
    return f"WH_{team.replace('-', '_').upper()}"


def render(products: list[Descriptor], settings_raw: dict) -> dict:
    sf = settings_raw["snowflake"]
    out: dict = {"database": sf["database"], "products": {}, "teams": {}}
    for d in sorted(products, key=lambda x: x.id):
        out["products"][d.id] = {
            "table": d.table,
            "domain": d.domain,
            "team": d.owner.team,
            "classification": d.classification,
            "pii_columns": [c.name.upper() for c in d.contract.columns if c.pii],
            "roles": role_names(d),
            "source_kind": d.source.kind,
            "source": d.source.location,
            "ports": d.ports,
            "kafka_topic": f"dataproduct.{d.id}.v{d.contract.major}" if "kafka" in d.ports else None,
            "inputs": sorted(d.inputs),
        }
        team = out["teams"].setdefault(
            d.owner.team,
            {
                "warehouse": team_warehouse(d.owner.team),
                "size": "XSMALL",
                "credits_per_month": 0.0,
                "email": d.owner.email,
            },
        )
        team["credits_per_month"] += d.budget.credits_per_month or 0.0
        sizes = ["XSMALL", "SMALL", "MEDIUM", "LARGE"]
        team["size"] = max(team["size"], d.budget.warehouse_size, key=sizes.index)
    return out


def write(products: list[Descriptor], settings_raw: dict, path: Path) -> dict:
    doc = render(products, settings_raw)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc
