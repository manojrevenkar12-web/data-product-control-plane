"""Contract checks: what changed between two versions, and which consumers a change would break."""

from __future__ import annotations

from dataclasses import dataclass

from dpcp.descriptor import Contract

BREAKING = {"removed_column", "type_changed", "unit_changed", "key_changed"}


@dataclass(frozen=True)
class Change:
    kind: str
    column: str
    detail: str = ""

    @property
    def breaking(self) -> bool:
        return self.kind in BREAKING


def diff(old: Contract, new: Contract) -> list[Change]:
    out: list[Change] = []
    for col in old.columns:
        now = new.column(col.name)
        if now is None:
            out.append(Change("removed_column", col.name))
            continue
        if now.type != col.type:
            out.append(Change("type_changed", col.name, f"{col.type} -> {now.type}"))
        if (now.unit or "") != (col.unit or ""):
            out.append(Change("unit_changed", col.name, f"{col.unit} -> {now.unit}"))
    for col in new.columns:
        if old.column(col.name) is None:
            out.append(Change("added_column", col.name))
    if list(old.key) != list(new.key):
        out.append(Change("key_changed", ",".join(new.key), f"{old.key} -> {new.key}"))
    return out


def semver(v: str) -> list[int]:
    return [int(x) for x in v.split(".")]


def change_input(product_id: str, old: Contract, new: Contract, consumers: dict[str, set[str]]) -> dict:
    """The input the change policy decides on: the diff, and for each breaking change the consumers reading it."""
    changes = diff(old, new)
    affected = []
    for ch in changes:
        if not ch.breaking:
            continue
        readers = sorted(c for c, cols in consumers.items() if ch.column in cols or ch.kind == "key_changed")
        affected.append({"kind": ch.kind, "column": ch.column, "detail": ch.detail, "consumers": readers})
    return {
        "id": product_id,
        "from": semver(old.version),
        "to": semver(new.version),
        "changes": [{"kind": c.kind, "column": c.column} for c in changes],
        "breaking": affected,
    }
