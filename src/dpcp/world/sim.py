"""A business-data publisher's domain teams over a few months, with the defects planted at known rates.

The world produces what the platform would see (deliveries, contract changes, onboarding requests, access
requests, ACCOUNT_USAGE rows) and keeps what really happened in `self.truth`. It reacts to what the platform tells
its teams (incidents, review results, cost alerts, published events), as teams do.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import random
import tomllib
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

TEAMS = [
    ("survey-data", "survey"),
    ("market-insights", "market"),
    ("company-data", "company"),
    ("ecommerce-tracking", "tracking"),
    ("consumer-insights", "consumer"),
    ("mobility-outlook", "mobility"),
    ("health-data", "health"),
    ("finance-revenue", "finance"),
    ("content-delivery", "content"),
    ("ad-sales", "adsales"),
    ("digital-markets", "digital"),
    ("research-ops", "research"),
]
SUBJECTS = [
    "revenue",
    "users",
    "prices",
    "shipments",
    "panel",
    "forecast",
    "traffic",
    "orders",
    "brands",
    "rankings",
    "penetration",
    "spend",
]
UNITS = [("EUR", 1000.0), ("count", 500.0), ("percent", 40.0), ("ratio", 1.5), ("USD", 2000.0)]
COUNTRIES = ["DE", "AT", "CH", "FR", "IT", "ES", "NL", "PL", "GB", "US"]
AGENTS = [
    ("research-assistant", "agent"),
    ("sales-copilot", "agent"),
    ("bi-dashboards", "service"),
    ("statista-connect", "service"),
]
SIZES = ["XSMALL", "SMALL", "MEDIUM", "LARGE"]
CREDITS_PER_HOUR = {"XSMALL": 1, "SMALL": 2, "MEDIUM": 4, "LARGE": 8}


def load_config(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def at(day: date, hour: float) -> datetime:
    return datetime.combine(day, time()) + timedelta(hours=hour)


class World:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rates = cfg["rates"]
        self.rng = random.Random(cfg["seed"])
        self.start = date.fromisoformat(cfg["start"])
        self.end = self.start + timedelta(days=cfg["days"])
        self.truth: dict = defaultdict(list)
        self._seq = 0
        self._queue: list = []
        self.teams = TEAMS[: cfg["teams"]]
        self.products: dict[str, dict] = {}  # product id -> descriptor document as the team has it live
        self.base: dict[str, dict[str, float]] = {}
        self.pending_changes: dict[str, dict] = {}
        self.published: dict[str, dict] = defaultdict(dict)  # product -> data date -> first published
        self.waiting_derived: dict[tuple[str, date], datetime] = {}
        self.grants: dict[str, dict] = {}
        self.redelivery: dict[tuple[str, date], int] = defaultdict(int)
        self.spend: list[dict] = []  # runaway and left-large episodes
        self._build()
        self._plan_spend_episodes()
        self._plan_onboardings()

    def _r(self, *key) -> random.Random:
        """A random stream per event, so a platform's reactions never shift what happens elsewhere: every design
        sees the same deliveries, defects, changes and requests."""
        return random.Random(f"{self.cfg['seed']}|" + "|".join(str(k) for k in key))

    # ---- building ----------------------------------------------------------------------------------------------
    def _schedule(self, when: datetime, kind: str, payload: dict) -> None:
        self._seq += 1
        heapq.heappush(self._queue, (when, self._seq, kind, payload))

    def _product_doc(
        self,
        team: str,
        domain: str,
        name: str,
        *,
        pii: bool,
        note: bool,
        derived: dict | None,
        ports: list[str],
        mistake: str | None = None,
    ) -> dict:
        r = self.rng
        cols = [
            {"name": "entity_id", "type": "string", "required": True, "description": "Entity id"},
            {"name": "period", "type": "string", "required": True, "description": "Reporting day"},
            {"name": "country", "type": "string", "required": True, "description": "ISO 3166-1 alpha-2"},
        ]
        for i in range(r.choice([2, 3])):
            unit, _ = r.choice(UNITS)
            cols.append(
                {
                    "name": f"{SUBJECTS[(len(name) + i) % len(SUBJECTS)]}_{i}",
                    "type": "number",
                    "required": i == 0,
                    "unit": unit,
                    "min": 0,
                    "description": f"{name} measure {i} in {unit}",
                }
            )
        if pii:
            cols.append({"name": "email", "type": "string", "pii": True, "description": "Contact address"})
        if note:
            cols.append({"name": "note", "type": "string", "description": "Free-text remark from the source"})
        classification = "confidential" if pii else r.choice(["internal", "internal", "public"])
        doc = {
            "id": f"{domain}.{name}",
            "domain": domain,
            "description": f"One row per entity, country and day of the {domain} {name} series, as delivered.",
            "owner": {"team": team, "email": f"{team}@example.com"},
            "classification": classification,
            "source": {"kind": r.choice(["files", "files", "kafka"]), "location": f"s3://dp-landing/{domain}/{name}/"},
            "sla": {"freshness_hours": 30, "schedule": "daily"},
            "budget": {"credits_per_month": float(r.choice([20, 40, 60, 90])), "warehouse_size": "XSMALL"},
            "ports": [p for p in ports if not (pii and p in ("mcp", "a2a"))],
            "inputs": derived or {},
            "contract": {"version": "1.0.0", "key": ["entity_id", "period"], "columns": cols},
        }
        if mistake:
            self._plant_mistake(doc, mistake)
        return doc

    def _plant_mistake(self, doc: dict, mistake: str) -> None:
        cols = doc["contract"]["columns"]
        if mistake == "no_email":
            doc["owner"]["email"] = ""
        elif mistake == "pii_internal":
            cols.append({"name": "contact_email", "type": "string", "pii": True, "description": "Contact"})
            doc["classification"] = "internal"
            doc["ports"] = ["api"]
        elif mistake == "no_unit":
            doc["ports"] = sorted(set(doc["ports"]) | {"mcp"})
            doc["classification"] = "internal"
            cols[3].pop("unit", None)
        elif mistake == "over_budget":
            doc["budget"]["credits_per_month"] = 950.0
        elif mistake == "key_not_required":
            cols[1]["required"] = False
        elif mistake == "no_sla":
            doc["sla"]["freshness_hours"] = None

    def _build(self) -> None:
        r = self.rng
        lo, hi = self.cfg["products_per_team"]
        all_ids: list[str] = []
        for team, domain in self.teams:
            for k in range(r.randint(lo, hi)):
                name = f"{r.choice(SUBJECTS)}_{chr(97 + k)}"
                pii, note = r.random() < 0.25, r.random() < 0.5
                derived = None
                ups = [p for p in all_ids if not p.startswith(domain + ".")]
                if ups and r.random() < 0.3:
                    derived = {}
                    for up in r.sample(ups, k=min(len(ups), r.choice([1, 2]))):
                        metrics = [c["name"] for c in self.products[up]["contract"]["columns"] if c["type"] == "number"]
                        derived[up] = ["country", r.choice(metrics)]
                ports = ["api"] + (["mcp", "a2a"] if r.random() < 0.5 else []) + (["kafka"] if r.random() < 0.4 else [])
                doc = self._product_doc(team, domain, name, pii=pii, note=note, derived=derived, ports=ports)
                self.products[doc["id"]] = doc
                all_ids.append(doc["id"])
        for pid in self.products:
            self.base[pid] = {}

    def _plan_spend_episodes(self) -> None:
        r, days = self.rng, self.cfg["days"]
        n_runaway = round(self.rates["runaway_per_team_year"] * len(self.teams) * days / 365)
        n_large = round(self.rates["warehouse_left_large_per_year"] * days / 365 * len(self.teams) / 4)
        for kind, n in (("runaway", n_runaway), ("left_large", n_large)):
            for _ in range(n):
                team, _domain = r.choice(self.teams)
                day = self.start + timedelta(days=r.randint(min(21, days // 3), max(min(21, days // 3), days - 8)))
                start = at(day, r.randint(0, 23))
                tagged = kind == "left_large" or r.random() < 0.5
                ep = {
                    "id": f"{kind}-{len(self.spend)}",
                    "kind": kind,
                    "team": team,
                    "start": start,
                    "stop": None,
                    "credits_per_hour": round(r.uniform(3, 8), 2),
                    "tagged": tagged,
                    "warehouse": self._team_wh(team) if kind == "left_large" else "WH_ADHOC",
                    "role_mapped": kind == "left_large" or r.random() < 0.5,
                    "user": f"{team}.analyst{r.randint(1, 4)}",
                }
                self.spend.append(ep)
        self.truth["spend_episodes"] = self.spend

    def _plan_onboardings(self) -> None:
        r = self.rng
        mistakes = ["no_email", "pii_internal", "no_unit", "over_budget", "key_not_required", "no_sla"]
        for i in range(self.rates["onboardings"]):
            team, domain = r.choice(self.teams)
            mistake = r.choice(mistakes) if r.random() < self.rates["onboarding_mistake_share"] else None
            ports = ["api", "mcp"] if mistake != "pii_internal" else ["api"]
            doc = self._product_doc(
                team, domain, f"new_{i}", pii=False, note=False, derived=None, ports=ports, mistake=mistake
            )
            when = at(
                self.start + timedelta(days=r.randint(min(7, self.cfg["days"] // 3), max(7, self.cfg["days"] - 14))), 10
            )
            self.truth["onboardings"].append({"id": doc["id"], "mistake": mistake, "proposed_at": when})
            self._schedule(when, "onboard", {"doc": doc, "mistake": mistake, "round": 1})

    def directory(self) -> dict[str, str]:
        """The identity provider's groups (SCIM): user -> team. Contractors are often missing from it."""
        r = random.Random(self.cfg["seed"] + 1)
        return {f"{team}.analyst{k}": team for team, _ in self.teams for k in range(1, 5) if r.random() > 0.15}

    @staticmethod
    def _team_wh(team: str) -> str:
        return f"WH_{team.replace('-', '_').upper()}"

    # ---- what the platform sees --------------------------------------------------------------------------------
    def initial_products(self) -> list[dict]:
        return [copy.deepcopy(d) for d in self.products.values()]

    def schedule_day(self, day: date) -> None:
        """Plan the deliveries of `day`'s data and the week's changes and access requests."""
        for pid, doc in self.products.items():
            r = self._r("schedule", pid, day)
            if doc["inputs"]:
                self.waiting_derived[(pid, day)] = at(day + timedelta(days=1), 0)
                continue
            if r.random() < self.rates["missing_delivery"]:
                self.truth["missing"].append({"product": pid, "data_date": day})
                continue
            hour = 24 + r.uniform(0.3, 1.3)
            if r.random() < self.rates["late_delivery"]:
                hour += r.uniform(3, 20)
                self.truth["late"].append({"product": pid, "data_date": day, "hours": hour - 25})
            self._schedule(at(day, hour), "deliver", {"product": pid, "data_date": day})
        if day.weekday() == 0:
            for pid in list(self.products):
                if (
                    pid not in self.pending_changes
                    and self._r("change?", pid, day).random() < self.rates["change_per_week"]
                ):
                    self._schedule(at(day, 10), "change", {"product": pid})
            self._schedule(at(day, 11), "access", {})

    def due(self, now: datetime) -> list[tuple[str, dict]]:
        out = []
        while self._queue and self._queue[0][0] <= now:
            _, _, kind, payload = heapq.heappop(self._queue)
            out.append((kind, payload))
        for (pid, day), since in list(self.waiting_derived.items()):
            if now >= since and all(day in self.published[up] for up in self.products[pid]["inputs"]):
                del self.waiting_derived[(pid, day)]
                out.append(("deliver", {"product": pid, "data_date": day}))
        return out

    def delivery(self, pid: str, day: date, now: datetime, fix: bool = False) -> dict:
        r = self._r("delivery", pid, day, self.redelivery[(pid, day)])
        doc = self.products[pid]
        cols = doc["contract"]["columns"]
        n = r.randint(*self.cfg["rows_per_delivery"])
        defects = []
        if not fix:
            for d in ("null_keys", "duplicate_keys", "out_of_range", "unit_change", "pii_in_free_text"):
                if r.random() < self.rates[d]:
                    if d == "pii_in_free_text" and not any(c["name"] == "note" for c in cols):
                        continue
                    if d == "unit_change" and day < self.start + timedelta(days=14):
                        continue
                    defects.append(d)
        rows, planted_pii = [], []
        metrics = [c for c in cols if c["type"] == "number"]
        shifted = r.choice(metrics)["name"] if metrics else None
        for i in range(n):
            row = {}
            for c in cols:
                name = c["name"]
                if name == "entity_id":
                    row[name] = f"e{i:04d}"
                elif name == "period":
                    row[name] = day.isoformat()
                elif name == "country":
                    row[name] = COUNTRIES[i % len(COUNTRIES)]
                elif c.get("pii"):
                    row[name] = f"person{i}.{day:%m%d}@mail.example"
                elif name == "note":
                    row[name] = r.choice(["checked", "n/a", "estimate", "source revised", ""])
                elif c["type"] == "number":
                    base = self.base[pid].setdefault(name, self._r("base", pid, name).uniform(50, 5000))
                    row[name] = round(base * r.uniform(0.8, 1.2), 3)
                elif c["type"] == "integer":
                    row[name] = r.randint(0, 1000)
                else:
                    row[name] = f"v{r.randint(0, 9)}"
            rows.append(row)
        if "null_keys" in defects:
            for row in r.sample(rows, k=max(1, n // 20)):
                row["entity_id"] = None
        if "duplicate_keys" in defects:
            rows += copy.deepcopy(r.sample(rows, k=max(1, n // 15)))
        if "out_of_range" in defects and metrics:
            for row in r.sample(rows, k=max(1, n // 10)):
                row[metrics[0]["name"]] = -abs(row[metrics[0]["name"]] or 1.0)
        if "unit_change" in defects and shifted:
            for row in rows:
                row[shifted] = row[shifted] * 1000 if row[shifted] is not None else None
        if "pii_in_free_text" in defects:
            for j, row in enumerate(r.sample(rows, k=max(2, n // 4))):
                value = (
                    f"call back +49 151 {r.randint(1000000, 9999999)}"
                    if j % 2
                    else f"ask {r.choice(['anna', 'jonas', 'mia', 'leon'])}.{r.randint(10, 99)}@mail.example"
                )
                row["note"] = value
                planted_pii.append(value)
        k = self.redelivery[(pid, day)]
        self.redelivery[(pid, day)] += 1
        body = json.dumps(rows, sort_keys=True, default=str)
        sha = hashlib.sha256(f"{pid}/{day}/{k}/{body}".encode()).hexdigest()
        kind = doc["source"]["kind"]
        uri = (
            f"{doc['source']['location']}{day:%Y/%m/%d}/part-{k}.json"
            if kind == "files"
            else f"kafka://{pid}.events/0/{day:%Y%m%d}-{k}"
        )
        self.truth["deliveries"].append(
            {
                "product": pid,
                "data_date": day,
                "attempt": k,
                "sha256": sha,
                "arrived_at": now,
                "defects": defects,
                "pii_values": planted_pii,
                "fix": fix,
            }
        )
        return {"product": pid, "uri": uri, "sha256": sha, "data_date": day, "arrived_at": now, "rows": rows}

    def duplicate_redelivery_due(self, pid: str, day: date) -> bool:
        return self._r("dup", pid, day, self.redelivery[(pid, day)]).random() < 0.01

    # ---- reactions -------------------------------------------------------------------------------------------
    def tell_incident(self, inc: dict, now: datetime) -> None:
        """The team named on a paged incident fixes its cause: re-delivers the day, or delivers the missing one."""
        if not inc.get("paged"):
            return
        pid = inc["root_product"]
        ev = inc.get("evidence") or {}
        if inc["kind"] == "quality":
            day = date.fromisoformat(ev["data_date"])
        else:
            latest = ev.get("latest_data_date")
            day = date.fromisoformat(latest) + timedelta(days=1) if latest else self.start
        self.truth["told"].append({"team": inc["team"], "product": pid, "kind": inc["kind"], "at": now})
        if inc["kind"] == "freshness" and self.products[pid]["inputs"]:
            return  # a derived product waits for its inputs; its team cannot make them arrive
        fix_hours = self._r("fix", pid, day, inc["kind"]).uniform(2, 30)
        self._schedule(now + timedelta(hours=fix_hours), "redeliver", {"product": pid, "data_date": day})

    def published_event(self, product: str, data_date: date, when: datetime) -> None:
        self.published[product].setdefault(data_date, when)

    def change(self, pid: str, now: datetime) -> dict | None:
        """The owning team edits its contract. Returns the proposed descriptor document."""
        r = self._r("change", pid, now)
        doc = copy.deepcopy(self.products[pid])
        cols = doc["contract"]["columns"]
        metrics = [c for c in cols if c["type"] == "number"]
        breaking = r.random() < self.rates["breaking_share"] and len(metrics) >= 2
        major, minor, patch = (int(x) for x in doc["contract"]["version"].split("."))
        if breaking:
            target = r.choice(metrics)
            kind = r.choice(["removed_column", "renamed_column", "unit_changed", "type_changed"])
            if kind == "removed_column":
                cols.remove(target)
            elif kind == "renamed_column":
                target["name"] = target["name"] + "_v2"
            elif kind == "unit_changed":
                target["unit"] = "k" + (target.get("unit") or "")
            else:
                target["type"] = "string"
                target.pop("min", None)
            bump_major = r.random() < 0.3
            doc["contract"]["version"] = f"{major + 1}.0.0" if bump_major else f"{major}.{minor + 1}.0"
            affected = sorted(
                c for c, colset in self.readers(pid).items() if target["name"].removesuffix("_v2") in colset
            )
            truth = {
                "product": pid,
                "breaking": True,
                "kind": kind,
                "column": target["name"].removesuffix("_v2"),
                "consumers": affected,
                "proposed_at": now,
                "major": bump_major,
            }
        else:
            cols.append(
                {
                    "name": f"extra_{len(cols)}",
                    "type": "number",
                    "unit": "count",
                    "min": 0,
                    "description": "Added measure",
                }
            )
            doc["contract"]["version"] = f"{major}.{minor + 1}.0"
            truth = {
                "product": pid,
                "breaking": False,
                "kind": "added_column",
                "column": cols[-1]["name"],
                "consumers": [],
                "proposed_at": now,
                "major": False,
            }
        self.truth["changes"].append(truth)
        self.pending_changes[pid] = {"doc": doc, "truth": truth, "round": 1}
        return doc

    def readers(self, pid: str) -> dict[str, set]:
        out = {}
        for other, d in self.products.items():
            if pid in d["inputs"]:
                out[other] = set(d["inputs"][pid])
        for g in self.grants.values():
            if g["product"] == pid:
                out[f"{g['kind']}:{g['principal']}"] = set(g["columns"])
        return out

    def review_result(self, pid: str, status: str, violations: list[dict], now: datetime) -> dict | None:
        """CI answered the change's pull request. Accepted: it is live. Rejected: follow the fix next working day."""
        pending = self.pending_changes.get(pid)
        if pending is None:
            return None
        if status != "rejected":
            self.products[pid] = pending["doc"]
            pending["truth"]["outcome"] = status
            pending["truth"]["rounds"] = pending["round"]
            pending["truth"]["live_at"] = now
            del self.pending_changes[pid]
            return None
        rules = {v["rule"] for v in violations}
        doc = copy.deepcopy(self.products[pid])
        old = {c["name"]: c for c in doc["contract"]["columns"]}
        new_doc = pending["doc"]
        major, minor, _ = (int(x) for x in doc["contract"]["version"].split("."))
        if "consumers" in rules:
            # follow the fix: keep every column consumers read, add the new shape next to it
            cols = doc["contract"]["columns"]
            for c in new_doc["contract"]["columns"]:
                if c["name"] not in old:
                    cols.append(c)
                elif c != old[c["name"]] and c["name"] in {n for s in self.readers(pid).values() for n in s}:
                    cols.append({**c, "name": c["name"] + "_new"})
            doc["contract"]["version"] = f"{major}.{minor + 1}.0"
        elif rules & {"major", "version"}:
            doc = new_doc
            doc["contract"]["version"] = f"{major + 1}.0.0"
        else:
            doc = new_doc
        pending["doc"] = doc
        pending["round"] += 1
        self._schedule(now + timedelta(days=1), "resubmit", {"product": pid})
        return doc

    def onboarding_result(self, payload: dict, status: str, violations: list[dict], now: datetime) -> dict | None:
        doc = payload["doc"]
        rec = next(o for o in self.truth["onboardings"] if o["id"] == doc["id"])
        rec.setdefault("rounds", 0)
        rec["rounds"] += 1
        if status != "rejected":
            rec["outcome"], rec["live_at"] = status, now
            self.products[doc["id"]] = doc
            self.base[doc["id"]] = {}
            return None
        fields = {v.get("field", "") for v in violations}
        fixed = copy.deepcopy(doc)
        repaired = World._repair(fixed, fields)
        if not repaired or rec["rounds"] >= 3:
            rec["outcome"] = "abandoned"
            return None
        self._schedule(
            now + timedelta(days=1),
            "onboard",
            {"doc": fixed, "mistake": payload["mistake"], "round": payload["round"] + 1},
        )
        return fixed

    @staticmethod
    def _repair(doc: dict, fields: set[str]) -> bool:
        """A team fixes exactly what the review names; nothing else."""
        changed = False
        for f in fields:
            if f == "owner.email":
                doc["owner"]["email"] = f"{doc['owner']['team']}@example.com"
            elif f == "budget.credits_per_month":
                doc["budget"]["credits_per_month"] = 150.0
            elif f == "sla.freshness_hours":
                doc["sla"]["freshness_hours"] = 30
            elif f == "contract.key":
                for c in doc["contract"]["columns"]:
                    if c["name"] in doc["contract"]["key"]:
                        c["required"] = True
            elif f.startswith("contract.columns."):
                name = f.split(".", 2)[2]
                for c in doc["contract"]["columns"]:
                    if c["name"] == name and c.get("pii"):
                        doc["classification"] = "confidential"
                    elif c["name"] == name:
                        c.setdefault("unit", "count")
                        c["unit"] = c["unit"] or "count"
                        c["description"] = c.get("description") or "Measure"
            else:
                continue
            changed = True
        return changed

    def access_requests(self, now: datetime) -> list[dict]:
        r = self._r("access", now)
        out = []
        ids = sorted(self.products)
        for i in range(self.rates["access_requests_per_week"]):
            pid = r.choice(ids)
            doc = self.products[pid]
            if r.random() < 0.5:
                name, kind = r.choice(AGENTS)
                domain = "platform"
            else:
                team, domain = r.choice(self.teams)
                name, kind = team, "human"
            port = r.choice(["api", "api", "mcp", "a2a", "kafka"])
            cols = [c["name"] for c in doc["contract"]["columns"]]
            out.append(
                {
                    "id": f"{now:%Y%m%d}-{i}",
                    "principal": {"name": name, "kind": kind, "domain": domain},
                    "product": pid,
                    "port": port,
                    "purpose": "" if r.random() < 0.4 else "quarterly market model refresh for the outlook",
                    "columns": sorted(r.sample(cols, k=min(len(cols), r.randint(3, 6)))),
                }
            )
        return out

    def access_result(self, req: dict, allowed: bool) -> None:
        if allowed:
            self.grants[req["id"]] = {
                "principal": req["principal"]["name"],
                "kind": req["principal"]["kind"],
                "product": req["product"],
                "columns": req["columns"],
            }

    # ---- Snowflake usage (ACCOUNT_USAGE) -----------------------------------------------------------------------
    def usage(self, day: date) -> dict[str, list[tuple]]:
        """QUERY_HISTORY, QUERY_ATTRIBUTION_HISTORY and WAREHOUSE_METERING_HISTORY rows for one day, plus truth."""
        qh, qa, metering = [], [], []
        true_rows = []
        weekday = day.weekday() < 5
        for h in range(24):
            hour = at(day, h)
            r = self._r("usage", hour)
            by_wh: dict[str, float] = defaultdict(float)
            groups = []
            for team, _domain in self.teams:
                tw = self._team_wh(team)
                if h < 6:
                    groups.append(
                        (tw, team, r.uniform(0.15, 0.4), "tag", f"DP_{team.upper()}_WRITER", "DP_PLATFORM_SVC")
                    )
                if weekday and 8 <= h < 19:
                    for wh, scale in (("WH_ADHOC", 0.6), ("WH_BI", 0.3)):
                        c = r.uniform(0.0, scale)
                        u = r.random()
                        how = (
                            "tag"
                            if u < 1 - self.rates["untagged_query_share"] - 0.4
                            else "role"
                            if u < 1 - self.rates["untagged_query_share"]
                            else "none"
                        )
                        groups.append(
                            (
                                wh,
                                team,
                                c,
                                how,
                                f"TEAM_{team.replace('-', '_').upper()}",
                                f"{team}.analyst{r.randint(1, 4)}" if r.random() < 0.8 else "BI_SERVICE",
                            )
                        )
            for ep in self.spend:
                stop = ep["stop"] or datetime.max
                if ep["start"] <= hour < stop:
                    how = "tag" if ep["tagged"] else "role" if ep["role_mapped"] else "none"
                    groups.append(
                        (
                            ep["warehouse"],
                            ep["team"],
                            ep["credits_per_hour"],
                            how,
                            f"TEAM_{ep['team'].replace('-', '_').upper()}",
                            ep["user"],
                        )
                    )
                    ep.setdefault("credits", 0.0)
                    ep["credits"] = ep["credits"] + ep["credits_per_hour"]
            for gi, (wh, team, credits, how, role, user) in enumerate(groups):
                if credits <= 0:
                    continue
                g = self._r("query", hour, gi, team)
                self._seq += 1
                qid = f"q{self._seq}"
                tag = json.dumps({"team": team}) if how == "tag" else ""
                role_name = role if how in ("tag", "role") else g.choice(["SYSADMIN", "PUBLIC", "ANALYST_ADHOC"])
                start = hour + timedelta(minutes=g.randint(0, 59))
                qh.append((qid, role_name, user, wh, tag, start))
                qa.append((qid, wh, tag, start, round(credits, 6)))
                by_wh[wh] += credits
                true_rows.append((hour, wh, team, credits))
            for wh, used in sorted(by_wh.items()):
                idle = used * self._r("idle", hour, wh).uniform(0.1, 0.25)
                metering.append((wh, hour, round(used + idle, 6)))
                true_rows.append((hour, wh, "__idle__", idle))
        self.truth["usage"].extend(true_rows)
        return {"QUERY_HISTORY": qh, "QUERY_ATTRIBUTION_HISTORY": qa, "WAREHOUSE_METERING_HISTORY": metering}

    def cost_alert(self, team: str, now: datetime) -> None:
        for ep in self.spend:
            if ep["team"] == team and ep["start"] <= now and ep["stop"] is None:
                ep["stop"] = now + timedelta(hours=self._r("react", ep["id"]).uniform(1, 4))
                ep["told_at"], ep["told_by"] = now, "alert"

    def finops_review(self, now: datetime) -> None:
        """The invoice is read on the 1st of the month; every episode still running is found and stopped then."""
        for ep in self.spend:
            if ep["start"] <= now and ep["stop"] is None:
                ep["stop"] = now + timedelta(hours=self._r("react", ep["id"]).uniform(1, 4))
                ep["told_at"], ep["told_by"] = now, "invoice"
