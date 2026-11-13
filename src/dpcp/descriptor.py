"""The data product descriptor: the one YAML file a domain team writes.

It holds the product's identity, owner, classification, source, SLA, budget, output ports and its data
contract (a subset of the Open Data Contract Standard). Everything the platform builds is derived from it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

ColumnType = Literal["string", "integer", "number", "date", "timestamp", "boolean"]
Port = Literal["api", "mcp", "a2a", "kafka"]
SQL_TYPES = {
    "string": "VARCHAR",
    "integer": "NUMBER(38,0)",
    "number": "FLOAT",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "boolean": "BOOLEAN",
}
IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


class Column(BaseModel):
    name: str
    type: ColumnType
    required: bool = False
    pii: bool = False
    unit: str | None = None
    description: str = ""
    min: float | None = None
    max: float | None = None

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError(f"column name {v!r}: use lower_snake_case")
        return v


class Contract(BaseModel):
    version: str
    key: list[str]
    columns: list[Column]

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class Owner(BaseModel):
    team: str = ""
    email: str = ""


class Source(BaseModel):
    kind: Literal["files", "kafka"]
    location: str  # s3://... prefix or a Kafka topic


class Sla(BaseModel):
    freshness_hours: float | None = None
    schedule: Literal["hourly", "daily", "weekly"] = "daily"


class Budget(BaseModel):
    credits_per_month: float | None = None
    warehouse_size: Literal["XSMALL", "SMALL", "MEDIUM", "LARGE"] = "XSMALL"


class Descriptor(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    domain: str
    description: str = ""
    owner: Owner = Owner()
    classification: str = "internal"
    source: Source
    sla: Sla = Sla()
    budget: Budget = Budget()
    ports: list[Port] = ["api"]
    inputs: dict[str, list[str]] = {}  # upstream product id -> the columns this product reads from it
    contract: Contract

    @property
    def name(self) -> str:
        return self.id.split(".")[1]

    @property
    def table(self) -> str:
        """Snowflake table of the current major version. A new major version is a new table next to the old one."""
        return f"{self.id.replace('.', '__').upper()}_V{self.contract.major}"

    def policy_input(self) -> dict:
        return self.model_dump(mode="json")


def load(path: Path) -> Descriptor:
    return Descriptor.model_validate(yaml.safe_load(path.read_text()))


def load_all(root: Path) -> list[Descriptor]:
    return [load(p) for p in sorted(root.rglob("*.yaml"))]
