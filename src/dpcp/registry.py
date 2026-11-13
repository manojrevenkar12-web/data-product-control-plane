"""What is live: the accepted descriptor of every product, and who reads which of its columns."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from dpcp.descriptor import Descriptor


@dataclass
class Grant:
    request_id: str
    principal: str  # team or service or agent name
    kind: str  # human | service | agent
    domain: str
    product: str
    port: str
    columns: list[str]
    unmasked: bool


@dataclass
class Registry:
    products: dict[str, Descriptor] = field(default_factory=dict)
    grants: list[Grant] = field(default_factory=list)

    def put(self, d: Descriptor) -> None:
        self.products[d.id] = d

    def consumers_of(self, product_id: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for d in self.products.values():
            if product_id in d.inputs:
                out[d.id] |= set(d.inputs[product_id])
        for g in self.grants:
            if g.product == product_id:
                out[f"{g.kind}:{g.principal}"] |= set(g.columns)
        return dict(out)

    def upstream(self, product_id: str) -> list[str]:
        d = self.products.get(product_id)
        return sorted(d.inputs) if d else []

    def grant_for(self, principal: str, product_id: str, port: str) -> Grant | None:
        return next(
            (g for g in self.grants if g.principal == principal and g.product == product_id and g.port == port), None
        )
