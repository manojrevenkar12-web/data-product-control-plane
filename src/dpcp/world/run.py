"""The harness: one simulated world against one platform design, hour by hour.

`platform`: gates in the pipeline and on pull requests, cost attribution with next-morning anomaly alerts.
`baseline`: the same checks after publishing (alerts only), changes merged as proposed, and spend found only when
the monthly invoice is read (the platform design reads it too, as a backstop). Both run the same world (same seed),
so the same defects arrive in both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from dpcp import settings as settings_mod
from dpcp import terraform
from dpcp.controlplane import ControlPlane
from dpcp.cost import CostJob
from dpcp.incidents import Incidents
from dpcp.pipeline import Delivery, Pipeline
from dpcp.policy import PolicyEngine
from dpcp.ports.events import MemoryProducer, OutboxPublisher
from dpcp.ports.service import Principal, ProductService
from dpcp.registry import Registry
from dpcp.sla import SlaMonitor
from dpcp.warehouse import Warehouse, bootstrap
from dpcp.world.sim import AGENTS, World, at, load_config

USAGE_DDL = {
    "QUERY_HISTORY": "QUERY_ID varchar, ROLE_NAME varchar, USER_NAME varchar, WAREHOUSE_NAME varchar, "
    "QUERY_TAG varchar, "
    "START_TIME timestamp_ntz",
    "QUERY_ATTRIBUTION_HISTORY": "QUERY_ID varchar, WAREHOUSE_NAME varchar, QUERY_TAG varchar, "
    "START_TIME timestamp_ntz, CREDITS_ATTRIBUTED_COMPUTE float",
    "WAREHOUSE_METERING_HISTORY": "WAREHOUSE_NAME varchar, START_TIME timestamp_ntz, CREDITS_USED_COMPUTE float",
}


@dataclass
class Run:
    design: str
    world: World
    wh: Warehouse
    registry: Registry
    pipeline: Pipeline
    control: ControlPlane
    service: ProductService
    sla: SlaMonitor
    cost: dict[str, CostJob]
    producer: MemoryProducer
    alerts: list = field(default_factory=list)
    duplicates_sent: int = 0
    duplicates_skipped: int = 0
    rejected_initial: list = field(default_factory=list)


def _load_usage(wh: Warehouse, usage_db: str, tables: dict[str, list[tuple]]) -> None:
    import pandas as pd

    cols = {k: [c.split()[0] for c in v.split(", ")] for k, v in USAGE_DDL.items()}
    for name, rows in tables.items():
        if rows:
            frame = pd.DataFrame(rows, columns=cols[name])
            import snowflake.connector.pandas_tools as pt

            pt.write_pandas(wh.conn, frame, name, database=usage_db, schema="ACCOUNT_USAGE")


def run(world_cfg: Path, design: str, log=lambda *_: None) -> Run:
    settings = settings_mod.load().with_gates(design == "platform")
    world = World(load_config(world_cfg))
    wh = Warehouse(settings)
    bootstrap(wh)
    usage_db = settings["snowflake"]["usage_database"]
    wh.run(f"create database if not exists {usage_db}")
    wh.run(f"create schema if not exists {usage_db}.ACCOUNT_USAGE")
    for name, ddl in USAGE_DDL.items():
        wh.run(f"create table if not exists {usage_db}.ACCOUNT_USAGE.{name} ({ddl})")

    registry = Registry()
    incidents = Incidents(wh, registry)
    pipeline = Pipeline(wh, incidents)
    control = ControlPlane(pipeline, registry, PolicyEngine(settings))
    service = ProductService(wh, registry, control)
    for name, kind in AGENTS:
        service.add_key(f"key-{name}", Principal(name, kind, "platform"))
    for team, domain in world.teams:
        service.add_key(f"key-{team}", Principal(team, "human", domain))
    r = Run(
        design,
        world,
        wh,
        registry,
        pipeline,
        control,
        service,
        SlaMonitor(pipeline, registry),
        {"platform": CostJob(wh, "platform"), "tag_only": CostJob(wh, "tag_only")},
        MemoryProducer(),
    )
    t0 = at(world.start, 0)
    for doc in world.initial_products():
        res = control.propose(doc, t0)
        if res["status"] == "rejected":
            r.rejected_initial.append((doc["id"], res["violations"]))
    rendered = terraform.render(list(registry.products.values()), settings.raw)
    roles = {role: p["team"] for p in rendered["products"].values() for role in p["roles"].values()}
    roles |= {f"TEAM_{t.replace('-', '_').upper()}": t for t in rendered["teams"]}
    warehouses = {t["warehouse"]: team for team, t in rendered["teams"].items()}
    for job in r.cost.values():
        job.set_owners(roles, warehouses, world.directory())
    outbox = OutboxPublisher(wh, r.producer, {})
    run_hour = settings["cost"]["run_hour"]
    seen_events = 0

    day = world.start
    while day < world.end:
        world.schedule_day(day)
        for h in range(24):
            now = at(day, h)
            for kind, payload in world.due(now):
                _handle(r, kind, payload, now)
            if pipeline.published_since_outbox:
                outbox.majors = {pid: d.contract.major for pid, d in registry.products.items()}
                outbox.publish(now)
                pipeline.published_since_outbox = 0
            events = r.producer.log
            if len(events) > seen_events:
                for _topic, ev in events[seen_events:]:
                    world.published_event(
                        ev["data"]["product"], datetime.fromisoformat(ev["data"]["data_date"]).date(), now
                    )
                seen_events = len(events)
            for inc in r.sla.run(now):
                world.tell_incident(inc, now)
            if h == run_hour and day > world.start:
                _cost(r, day - timedelta(days=1), now, usage_db)
            if day.day == 1 and h == 9:  # both designs read the monthly invoice
                world.finops_review(now)
        day += timedelta(days=1)
        if day.day == 1:
            log(f"{design}: {day} statements={wh.statements}")
    return r


def _handle(r: Run, kind: str, payload: dict, now: datetime) -> None:
    world, control = r.world, r.control
    if kind in ("deliver", "redeliver"):
        pid = payload["product"]
        if pid not in r.registry.products:
            return
        raw = world.delivery(pid, payload["data_date"], now, fix=kind == "redeliver")
        _ingest(r, raw, now)
        if world.duplicate_redelivery_due(pid, payload["data_date"]):
            r.duplicates_sent += 1
            if _ingest(r, raw, now)["status"] == "duplicate":
                r.duplicates_skipped += 1
    elif kind in ("change", "resubmit"):
        pid = payload["product"]
        doc = world.change(pid, now) if kind == "change" else world.pending_changes[pid]["doc"]
        res = control.propose(doc, now)
        world.review_result(pid, res["status"], res["violations"], now)
    elif kind == "onboard":
        res = control.propose(payload["doc"], now)
        world.onboarding_result(payload, res["status"], res["violations"], now)
    elif kind == "access":
        reqs = world.access_requests(now)
        decisions = control.request_access(reqs)
        for req in reqs:
            world.access_result(req, decisions[req["id"]]["allow"])


def _ingest(r: Run, raw: dict, now: datetime) -> dict:
    d = r.registry.products[raw["product"]]
    res = r.pipeline.ingest(d, Delivery(**raw), now)
    if res["status"].startswith("published"):
        r.sla.observe_publish(d.id, raw["data_date"])
    if res["findings"]:
        inc = r.pipeline.incidents.open_now.get((d.id, "quality"))
        if inc:
            r.world.tell_incident(inc, now)
    return res


def _cost(r: Run, day, now: datetime, usage_db: str) -> None:
    _load_usage(r.wh, usage_db, r.world.usage(day))
    for job in r.cost.values():
        job.attribute(day)
    if r.design == "platform":
        for alert in r.cost["platform"].anomalies(day):
            alert["told_at"] = now
            r.alerts.append(alert)
            r.world.cost_alert(alert["team"], now)
