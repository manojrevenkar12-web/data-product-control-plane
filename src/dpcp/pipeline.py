"""The standard ingestion framework: load once, check in the warehouse, then publish (write-audit-publish).

With gates on, a delivery that fails any check is quarantined in the staging schema and consumers keep the last
good data. With gates off (the monitoring-only design) the same checks run but only raise an incident after
the data is already published.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from dpcp.descriptor import SQL_TYPES, Descriptor
from dpcp.incidents import Incidents
from dpcp.warehouse import Warehouse

NUMERIC = {"integer", "number"}
ROW_CHECKS = {"required_null", "out_of_range"}


@dataclass
class Delivery:
    product: str
    uri: str  # s3://bucket/prefix/file or kafka://topic/partition/first-last
    sha256: str
    data_date: date
    arrived_at: datetime
    rows: list[dict] = field(default_factory=list)


def _sql_str(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class Pipeline:
    def __init__(self, wh: Warehouse, incidents: Incidents):
        self.wh = wh
        self.incidents = incidents
        self.gates = wh.settings.gates_enabled
        g = wh.settings["gates"]
        self.patterns = dict(g["patterns"])
        self.pii_min_share = g["pii_min_share"]
        self.scale_ratio = g["scale_ratio"]
        self.scale_days = g["scale_history_days"]
        self.scale_min_days = g["scale_min_history_days"]
        self.row_share = g["row_quarantine_max_share"]
        self.platform = wh.schema("platform_schema")
        self.stage = wh.schema("staging_schema")
        self.core = wh.schema("core_schema")
        self.serve = wh.schema("serve_schema")
        self._loaded: set[str] | None = None  # LOAD_HISTORY keys; this pipeline is the table's only writer
        self.published_since_outbox = 0

    # ---- product objects -------------------------------------------------------------------------------
    def ensure_product(self, d: Descriptor) -> None:
        cols = ", ".join(f"{c.name.upper()} {SQL_TYPES[c.type]}" for c in d.contract.columns)
        self.wh.run(f"create table if not exists {self.core}.{d.table} ({cols}, _BATCH_ID varchar, _DATA_DATE date)")
        existing = {
            r[0]
            for r in self.wh.run(
                f"select column_name from {self.wh.db}.information_schema.columns "
                "where table_schema = %s and table_name = %s",
                (self.core.split(".")[1], d.table),
            )
        }
        for c in d.contract.columns:
            if c.name.upper() not in existing:
                self.wh.run(f"alter table {self.core}.{d.table} add column {c.name.upper()} {SQL_TYPES[c.type]}")
        self.create_views(d)

    def create_views(self, d: Descriptor) -> None:
        secure = "" if self.wh.emulated else "secure "
        masked = ", ".join(
            f"sha2({c.name.upper()}) as {c.name.upper()}" if c.pii else c.name.upper() for c in d.contract.columns
        )
        full = ", ".join(c.name.upper() for c in d.contract.columns)
        tail = f"_DATA_DATE from {self.core}.{d.table}"
        self.wh.run(f"create or replace {secure}view {self.serve}.{d.table} as select {masked}, {tail}")
        self.wh.run(f"create or replace {secure}view {self.serve}.{d.table}__FULL as select {full}, {tail}")

    def apply_in_place(self, old: Descriptor, new: Descriptor) -> None:
        """Monitoring-only design: a contract change is applied to the live table whatever it breaks."""
        for c in old.contract.columns:
            now = new.contract.column(c.name)
            if now is None or now.type != c.type:
                self.wh.run(f"alter table {self.core}.{old.table} drop column {c.name.upper()}")
        tmp = new.model_copy(deep=True)
        tmp.contract.version = old.contract.version  # same table name
        self.ensure_product(tmp)

    # ---- ingestion ------------------------------------------------------------------------------------
    def already_loaded(self, delivery: Delivery) -> bool:
        if self._loaded is None:
            self._loaded = {
                k for row in self.wh.run(f"select URI, SHA256 from {self.platform}.LOAD_HISTORY") for k in row
            }
        return delivery.uri in self._loaded or delivery.sha256 in self._loaded

    def ingest(self, d: Descriptor, delivery: Delivery, now: datetime) -> dict:
        if self.already_loaded(delivery):
            return {"status": "duplicate", "batch_id": None, "findings": []}
        self.wh.tag(team=d.owner.team, product=d.id, job="ingest")
        batch_id = f"{delivery.data_date:%Y%m%d}-{delivery.sha256[:10]}"
        stage = f"{d.table}__B{re.sub(r'[^0-9A-Za-z]', '', batch_id).upper()}"
        cols = d.contract.columns
        self.wh.run(
            f"create or replace table {self.stage}.{stage} ("
            + ", ".join(f"{c.name.upper()} {SQL_TYPES[c.type]}" for c in cols)
            + ")"
        )
        frame = pd.DataFrame(
            [{c.name: r.get(c.name) for c in cols} for r in delivery.rows], columns=[c.name for c in cols]
        )
        if len(frame):
            self.wh.load_frame(frame, "staging_schema", stage)
        self.wh.run(
            f"insert into {self.platform}.LOAD_HISTORY values (%s, %s, %s, %s, %s)",
            (delivery.uri, delivery.sha256, d.id, batch_id, now),
        )
        self._loaded |= {delivery.uri, delivery.sha256}
        findings = self.check(d, f"{self.stage}.{stage}", delivery.data_date)
        valid = self.valid_rows(d)
        row_scoped = bool(findings) and all(f["check"] in ROW_CHECKS for f in findings)
        bad_rows = self.wh.run(f"select count_if(not ({valid})) from {self.stage}.{stage}")[0][0] if row_scoped else 0
        if not findings or not self.gates:
            self.publish(d, f"{self.stage}.{stage}", batch_id, delivery.data_date, now)
            self.wh.run(f"drop table if exists {self.stage}.{stage}")
            status = "published_with_findings" if findings else "published"
        elif row_scoped and len(frame) and bad_rows / len(frame) <= self.row_share:
            self.publish(d, f"{self.stage}.{stage}", batch_id, delivery.data_date, now, where=valid)
            status = "published_partial"  # the invalid rows stay in staging for the producer
        else:
            status = "quarantined"
        self.wh.run(
            f"insert into {self.platform}.BATCHES values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                batch_id,
                d.id,
                d.contract.version,
                delivery.data_date,
                delivery.uri,
                delivery.arrived_at,
                now,
                status,
                len(frame),
                json.dumps(findings),
            ),
        )
        if findings:
            self.incidents.open(
                d,
                "quality",
                now,
                {"batch_id": batch_id, "data_date": str(delivery.data_date), "status": status, "findings": findings},
            )
        elif status == "published":
            self.incidents.close(d.id, "quality", now, data_date=str(delivery.data_date))
        return {"status": status, "batch_id": batch_id, "findings": findings}

    def valid_rows(self, d: Descriptor) -> str:
        """SQL predicate for rows that pass every row-scoped check."""
        conds = []
        for c in d.contract.columns:
            u = c.name.upper()
            if c.required:
                conds.append(f"{u} is not null")
            if c.min is not None:
                conds.append(f"({u} is null or {u} >= {c.min})")
            if c.max is not None:
                conds.append(f"({u} is null or {u} <= {c.max})")
        return " and ".join(conds) or "true"

    def publish(self, d: Descriptor, staged: str, batch_id: str, day: date, now: datetime, where: str = "true") -> None:
        names = ", ".join(c.name.upper() for c in d.contract.columns)
        payload = json.dumps(
            {
                "product": d.id,
                "version": d.contract.version,
                "batch_id": batch_id,
                "data_date": str(day),
                "table": f"{self.serve}.{d.table}",
            }
        )
        self.wh.run("begin")
        self.wh.run(f"delete from {self.core}.{d.table} where _DATA_DATE = %s", (day,))
        self.wh.run(
            f"insert into {self.core}.{d.table} ({names}, _BATCH_ID, _DATA_DATE) "
            f"select {names}, %s, %s from {staged} where {where}",
            (batch_id, day),
        )
        self.wh.run(
            f"insert into {self.platform}.OUTBOX values (%s, %s, %s, %s, %s, null)",
            (str(uuid.uuid5(uuid.NAMESPACE_URL, f"{d.id}/{batch_id}")), d.id, "published", payload, now),
        )
        self.wh.run("commit")
        self.published_since_outbox += 1

    # ---- checks ---------------------------------------------------------------------------------------
    def check(self, d: Descriptor, staged: str, day: date) -> list[dict]:
        cols = d.contract.columns
        parts = ["count(*) as N"]
        for c in cols:
            u = c.name.upper()
            if c.required:
                parts.append(f"count_if({u} is null) as REQ__{u}")
            if c.min is not None or c.max is not None:
                cond = " or ".join(
                    x
                    for x in (
                        f"{u} < {c.min}" if c.min is not None else "",
                        f"{u} > {c.max}" if c.max is not None else "",
                    )
                    if x
                )
                parts.append(f"count_if({cond}) as RANGE__{u}")
            if c.type == "string" and not c.pii:
                like = " or ".join(f"regexp_like({u}, {_sql_str(p)})" for p in self.patterns.values())
                parts.append(f"count_if({like}) as PII__{u}")
            if c.type in NUMERIC and c.unit:
                parts.append(f"median({u}) as MED__{u}")
        keys = ", ".join(k.upper() for k in d.contract.key)
        parts.append(f"(select count(*) from (select {keys} from {staged} group by {keys} having count(*) > 1)) as DUP")
        stats = self.wh.rows(f"select {', '.join(parts)} from {staged}")[0]
        dup = stats.pop("dup")
        findings: list[dict] = []
        n = int(stats["n"])
        if n == 0:
            findings.append({"check": "empty", "column": None, "count": 0})
        if dup:
            findings.append({"check": "duplicate_key", "column": ",".join(d.contract.key), "count": int(dup)})
        for key, val in stats.items():
            kind, _, col = key.partition("__")
            if kind == "req" and val:
                findings.append({"check": "required_null", "column": col.lower(), "count": int(val)})
            elif kind == "range" and val:
                findings.append({"check": "out_of_range", "column": col.lower(), "count": int(val)})
            elif kind == "pii" and n and val and int(val) / n >= self.pii_min_share:
                findings.append({"check": "undeclared_pii", "column": col.lower(), "count": int(val)})
        meds = {k.partition("__")[2]: v for k, v in stats.items() if k.startswith("med__") and v is not None}
        if meds:
            hist = self.wh.rows(
                f"select count(distinct _DATA_DATE) as DAYS__, "
                f"{', '.join(f'median({c.upper()}) as {c}' for c in meds)} from {self.core}.{d.table} "
                f"where _DATA_DATE >= %s and _DATA_DATE < %s",
                (day - timedelta(days=self.scale_days), day),
            )[0]
            if hist.pop("days__") < self.scale_min_days:
                meds = {}
            for col, now_med in meds.items():
                past = hist.get(col)
                if past and now_med and (now_med / past >= self.scale_ratio or past / now_med >= self.scale_ratio):
                    findings.append(
                        {
                            "check": "scale_shift",
                            "column": col.lower(),
                            "count": n,
                            "detail": f"median {past:.4g} -> {now_med:.4g}",
                        }
                    )
        return findings

    def latest_published(self, product_id: str) -> date | None:
        r = self.wh.run(
            f"select max(DATA_DATE) from {self.platform}.BATCHES where PRODUCT = %s and STATUS like 'published%%'",
            (product_id,),
        )
        return r[0][0] if r and r[0][0] else None
