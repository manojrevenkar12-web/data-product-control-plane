"""OpenMetadata: domains, tables with column tags, data products and column-level lineage, from the descriptors.

Everything is a PUT (create or update), so a sync can run on every merge. The payloads follow OpenMetadata's
create requests (API v1); entity references need ids, so tables are written before data products and lineage.
"""

from __future__ import annotations

import httpx

from dpcp.descriptor import Descriptor

OM_TYPES = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "number": "DOUBLE",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOLEAN",
}
PII_TAG = {"tagFQN": "PII.Sensitive", "source": "Classification", "labelType": "Manual", "state": "Confirmed"}
TIER = {"public": "Tier.Tier3", "internal": "Tier.Tier2", "confidential": "Tier.Tier1", "restricted": "Tier.Tier1"}


def table_request(d: Descriptor, schema_fqn: str) -> dict:
    return {
        "name": d.table,
        "databaseSchema": schema_fqn,
        "description": d.description,
        "columns": [
            {
                "name": c.name.upper(),
                "dataType": OM_TYPES[c.type],
                "description": " ".join(x for x in (c.description, f"Unit: {c.unit}." if c.unit else "") if x),
                "tags": [PII_TAG] if c.pii else [],
            }
            for c in d.contract.columns
        ],
        "tableConstraints": [{"constraintType": "PRIMARY_KEY", "columns": [k.upper() for k in d.contract.key]}],
        "tags": [
            {"tagFQN": TIER[d.classification], "source": "Classification", "labelType": "Manual", "state": "Confirmed"}
        ],
        "extension": {
            "contractVersion": d.contract.version,
            "freshnessSlaHours": d.sla.freshness_hours,
            "outputPorts": ",".join(d.ports),
        },
    }


def lineage_requests(d: Descriptor, ids: dict[str, str], fqns: dict[str, str]) -> list[dict]:
    out = []
    for up, cols in sorted(d.inputs.items()):
        if up not in ids:
            continue
        out.append(
            {
                "edge": {
                    "fromEntity": {"id": ids[up], "type": "table"},
                    "toEntity": {"id": ids[d.id], "type": "table"},
                    "lineageDetails": {
                        "columnsLineage": [
                            {"fromColumns": [f"{fqns[up]}.{c.upper()}"], "toColumn": None} for c in cols
                        ],
                        "source": "Manual",
                        "description": "declared in the descriptor's inputs",
                    },
                }
            }
        )
    return out


class OpenMetadata:
    def __init__(
        self,
        base_url: str,
        token: str,
        service: str,
        database: str,
        schema: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            transport=transport,
            timeout=30,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.schema_fqn = f"{service}.{database}.{schema}"

    def _put(self, path: str, body: dict) -> dict:
        r = self.http.put(path, json=body)
        r.raise_for_status()
        return r.json()

    def team_ref(self, team: str) -> dict:
        r = self.http.get(f"/teams/name/{team}")
        r.raise_for_status()
        return {"id": r.json()["id"], "type": "team"}

    def sync(self, products: list[Descriptor]) -> dict:
        ids, fqns, owners = {}, {}, {}
        for domain in sorted({d.domain for d in products}):
            self._put(
                "/domains",
                {
                    "name": domain,
                    "domainType": "Source-aligned",
                    "description": f"Data products of the {domain} domain",
                },
            )
        for d in sorted(products, key=lambda x: x.id):
            if d.owner.team not in owners:
                owners[d.owner.team] = self.team_ref(d.owner.team)
            body = table_request(d, self.schema_fqn) | {"owners": [owners[d.owner.team]]}
            t = self._put("/tables", body)
            ids[d.id], fqns[d.id] = t["id"], t["fullyQualifiedName"]
        for d in sorted(products, key=lambda x: x.id):
            self._put(
                "/dataProducts",
                {
                    "name": d.id.replace(".", "_"),
                    "displayName": d.id,
                    "domain": d.domain,
                    "description": d.description,
                    "owners": [owners[d.owner.team]],
                    "assets": [{"id": ids[d.id], "type": "table"}],
                },
            )
        edges = [e for d in products for e in lineage_requests(d, ids, fqns)]
        for e in edges:
            self._put("/lineage", e)
        return {"tables": len(ids), "data_products": len(products), "lineage_edges": len(edges)}
