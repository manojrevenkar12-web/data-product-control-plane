"""Run the world against both designs, compare with the truth, write reports/<world>.json.

    python3 scripts/evaluate.py              # dev world (config/world.toml), ~8 minutes on 2 cores
    python3 scripts/evaluate.py --heldout    # held-out world, once (writes config/heldout.lock)

This file and src/dpcp/world/ are the only code that reads the truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dpcp import settings as settings_mod  # noqa: E402
from dpcp import terraform  # noqa: E402
from dpcp.catalog import OpenMetadata  # noqa: E402
from dpcp.cost import UNATTRIBUTED  # noqa: E402


def pct(a: float, b: float) -> float:
    return round(100 * a / b, 1) if b else 0.0


def measure(world_cfg: str, design: str) -> dict:
    from dpcp.world.run import run

    r = run(Path(world_cfg), design)
    truth, wh = r.world.truth, r.wh
    plat = wh.schema("platform_schema")
    batches = wh.rows(f"select * from {plat}.BATCHES order by DECIDED_AT, BATCH_ID")
    by_sha = {b["batch_id"].split("-", 1)[1]: b for b in batches}
    out: dict = {
        "design": design,
        "products": len(r.registry.products),
        "deliveries": len(truth["deliveries"]),
        "batches": len(batches),
        "statements": wh.statements,
    }

    # -- bad data -------------------------------------------------------------------------------------------
    end = datetime.combine(r.world.end, datetime.min.time())
    pub = defaultdict(list)
    for b in batches:
        if b["status"].startswith("published"):
            pub[(b["product"], b["data_date"])].append(b["decided_at"])
    bad = [d for d in truth["deliveries"] if d["defects"]]
    clean = [d for d in truth["deliveries"] if not d["defects"]]
    per_type = {}
    reached = live_hours = 0
    row_scoped = {"null_keys", "out_of_range"}  # the rows the valid-row predicate removes
    partial = 0
    for d in bad:
        b = by_sha.get(d["sha256"][:10])
        if b is None:
            continue
        found = b["status"] != "published"
        bad_rows_live = b["status"] in ("published", "published_with_findings") or (
            b["status"] == "published_partial" and not set(d["defects"]) <= row_scoped
        )
        partial += b["status"] == "published_partial"
        for t in d["defects"]:
            s = per_type.setdefault(t, {"planted": 0, "caught": 0, "reached_consumers": 0})
            s["planted"] += 1
            s["caught"] += found
            s["reached_consumers"] += bad_rows_live
        if bad_rows_live:
            reached += 1
            later = [t for t in pub[(d["product"], d["data_date"])] if t > b["decided_at"]]
            live_hours += ((min(later) if later else end) - b["decided_at"]).total_seconds() / 3600
    blocked_clean = sum(1 for d in clean if (b := by_sha.get(d["sha256"][:10])) and b["status"] == "quarantined")
    out["bad_data"] = {
        "bad_deliveries": len(bad),
        "reached_consumers": reached,
        "hours_live": round(live_hours),
        "valid_rows_published_rest_held": partial,
        "clean_deliveries": len(clean),
        "clean_blocked": blocked_clean,
        "by_defect": per_type,
    }

    # -- personal data in free text -------------------------------------------------------------------------
    pii = [d for d in truth["deliveries"] if d["pii_values"]]
    leaked = [
        d
        for d in pii
        if (b := by_sha.get(d["sha256"][:10]))
        and b["status"].startswith("published")
        and b["status"] != "published_partial"
    ]
    out["personal_data"] = {
        "deliveries_with_undeclared_pii": len(pii),
        "published_to_other_domains": len(leaked),
        "values_published": sum(len(d["pii_values"]) for d in leaked),
    }

    # -- contract changes -----------------------------------------------------------------------------------
    ch = truth["changes"]
    harmful = [c for c in ch if c["breaking"] and c["consumers"]]
    reached_ch = [c for c in harmful if c.get("rounds") == 1]
    safe = [c for c in ch if not c["breaking"]]
    out["changes"] = {
        "proposed": len(ch),
        "breaking_with_consumers": len(harmful),
        "breaking_reached_consumers": len(reached_ch),
        "consumer_breakages": sum(len(c["consumers"]) for c in reached_ch),
        "breaking_without_consumers": sum(1 for c in ch if c["breaking"] and not c["consumers"]),
        "additive_accepted_first_time": sum(1 for c in safe if c.get("rounds") == 1),
        "additive": len(safe),
        "shipped": sum(1 for c in ch if c.get("live_at")),
        "mean_rounds": round(statistics.mean([c["rounds"] for c in ch if c.get("rounds")] or [0]), 2),
        "median_days_to_live": round(
            statistics.median(
                [(c["live_at"] - c["proposed_at"]).total_seconds() / 86400 for c in ch if c.get("live_at")] or [0]
            ),
            1,
        ),
    }

    # -- onboarding -----------------------------------------------------------------------------------------
    ob = truth["onboardings"]
    with_m = [o for o in ob if o["mistake"]]
    out["onboarding"] = {
        "proposed": len(ob),
        "with_mistake": len(with_m),
        "mistake_caught": sum(1 for o in with_m if o.get("rounds", 0) > 1 or o.get("outcome") == "abandoned"),
        "mistake_went_live": sum(
            1 for o in with_m if o.get("rounds") == 1 and o.get("outcome", "").startswith("accepted")
        ),
        "clean_live_first_time": sum(1 for o in ob if not o["mistake"] and o.get("rounds") == 1),
        "clean": len(ob) - len(with_m),
        "fixed_by_following_the_message": sum(
            1 for o in with_m if o.get("rounds", 0) > 1 and o.get("outcome", "").startswith("accepted")
        ),
    }

    # -- freshness incidents and routing --------------------------------------------------------------------
    incidents = wh.rows(f"select * from {plat}.INCIDENTS order by OPENED_AT, PRODUCT")
    fresh = [i for i in incidents if i["kind"] == "freshness"]
    right = 0
    for i in fresh:
        d = r.registry.products[i["product"]]
        ev = json.loads(i["evidence"])
        need = datetime.fromisoformat(ev["latest_data_date"]).date() + timedelta(days=1)
        roots = {up for up in d.inputs if r.world.published.get(up, {}).get(need, datetime.max) > i["opened_at"]} or {
            d.id
        }
        right += i["root_product"] in roots
    breach_h = sum(((i["closed_at"] or end) - i["opened_at"]).total_seconds() / 3600 for i in fresh)
    out["freshness"] = {
        "incidents": len(fresh),
        "paged": sum(1 for i in fresh if i["paged"]),
        "routed_to_root_cause_team": right,
        "breach_hours": round(breach_h),
        "quality_incidents": sum(1 for i in incidents if i["kind"] == "quality"),
    }

    # -- spend ----------------------------------------------------------------------------------------------
    s = settings_mod.load()
    price = s["cost"]["credit_price_usd"]
    eps = truth["spend_episodes"]
    delays = [(e["told_at"] - e["start"]).total_seconds() / 3600 for e in eps if e.get("told_at")]
    out["spend"] = {
        "episodes": len(eps),
        "found_by_alert": sum(1 for e in eps if e.get("told_by") == "alert"),
        "found_by_invoice": sum(1 for e in eps if e.get("told_by") == "invoice"),
        "credits_burnt": round(sum(e.get("credits", 0.0) for e in eps), 1),
        "usd_burnt": round(price * sum(e.get("credits", 0.0) for e in eps)),
        "median_hours_to_team": round(statistics.median(delays), 1) if delays else None,
        "alerts": len(r.alerts),
    }
    out["attribution"] = attribution(r, truth)

    # -- ports, events, catalogue, load-once ----------------------------------------------------------------
    out["ports"] = ports(r)
    events = [e for evs in r.producer.topics.values() for _k, e in evs]
    published = sum(1 for b in batches if b["status"].startswith("published"))
    out["events"] = {
        "published_batches": published,
        "events": len(events),
        "unique_event_ids": len({e["id"] for e in events}),
    }
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        calls.append(req.url.path)
        if "/teams/name/" in req.url.path:
            return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[1]})
        if req.url.path.endswith("/tables"):
            return httpx.Response(200, json={"id": body["name"], "fullyQualifiedName": f"sf.DB.S.{body['name']}"})
        return httpx.Response(200, json={})

    om = OpenMetadata("http://om", "t", "snowflake", "DATA_PLATFORM", "SERVE", transport=httpx.MockTransport(handler))
    out["catalogue"] = om.sync(list(r.registry.products.values()))
    rendered = terraform.render(list(r.registry.products.values()), s.raw)
    out["terraform"] = {
        "products": len(rendered["products"]),
        "teams": len(rendered["teams"]),
        "roles": sum(len(p["roles"]) for p in rendered["products"].values()),
        "kafka_topics": sum(1 for p in rendered["products"].values() if p["kafka_topic"]),
    }
    out["load_once"] = {"sent_twice": r.duplicates_sent, "skipped": r.duplicates_skipped}
    out["access"] = access(wh, r)
    out["policy_evaluations"] = r.control.policy.calls
    if design == "platform":
        out["console"] = console(r, batches, incidents)
    r.wh.close()
    return out


def attribution(r, truth) -> dict:
    """Both methods against the truth, per team and day. Truth idle: the owner's on a team warehouse, else pro rata."""
    owners = {f"WH_{t.replace('-', '_').upper()}": t for t, _ in r.world.teams}
    true = defaultdict(float)
    used = defaultdict(lambda: defaultdict(float))
    for hour, wh, team, credits in truth["usage"]:
        if team != "__idle__":
            used[(hour, wh)][team] += credits
    for hour, wh, team, credits in truth["usage"]:
        day = hour.date()
        if team != "__idle__":
            true[(day, team)] += credits
        elif wh in owners:
            true[(day, owners[wh])] += credits
        else:
            tot = sum(used[(hour, wh)].values())
            for t, c in used[(hour, wh)].items():
                true[(day, t)] += credits * c / tot
    total = sum(true.values())
    res = {}
    for method in ("platform", "tag_only"):
        got = defaultdict(float)
        for row in r.wh.rows(
            f"select DAY, TEAM, sum(CREDITS) as C from {r.wh.schema('platform_schema')}.COST_ATTRIBUTION "
            "where METHOD like %s group by DAY, TEAM",
            (f"{method}:%",),
        ):
            got[(row["day"], row["team"])] += row["c"]
        un = sum(v for (d, t), v in got.items() if t == UNATTRIBUTED)
        keys = {k for k in set(true) | set(got) if k[1] != UNATTRIBUTED}
        err = sum(abs(true.get(k, 0.0) - got.get(k, 0.0)) for k in keys)
        res[method] = {"unattributed_pct": pct(un, total), "team_day_error_pct": pct(err, 2 * total)}
    res["total_credits"] = round(total)
    return res


def ports(r) -> dict:
    from fastapi.testclient import TestClient

    from dpcp.ports import mcp_server
    from dpcp.ports.api import create_app

    client = TestClient(create_app(r.service))
    same = checked = denied_agents = 0
    for g in r.registry.grants:
        if g.port not in ("api", "mcp", "a2a") or g.kind != "agent":
            continue
        d = r.registry.products[g.product]
        if not r.control.agent_ready(d):
            continue
        for port in ("api", "mcp", "a2a"):
            if r.registry.grant_for(g.principal, g.product, port) is None:
                r.registry.grants.append(
                    type(g)(f"parity-{port}", g.principal, g.kind, g.domain, g.product, port, g.columns, g.unmasked)
                )
        h = {"Authorization": f"Bearer key-{g.principal}"}
        a = client.get(f"/products/{g.product}/rows", headers=h).json()["rows"]
        rpc = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": "m",
                    "parts": [{"kind": "data", "data": {"product": g.product}}],
                }
            },
        }
        b = client.post("/a2a", json=rpc, headers=h).json()["result"]["parts"][0]["data"]["rows"]
        server = mcp_server.build(r.service, r.service.principal(f"key-{g.principal}"))
        c = json.loads(
            asyncio.run(server.call_tool("read_product", {"product_id": g.product, "limit": 200})).content[0].text
        )["rows"]
        checked += 1
        same += a == b == c
    for d in r.registry.products.values():
        if not r.control.agent_ready(d):
            res = client.get(f"/products/{d.id}", headers={"Authorization": "Bearer key-research-assistant"})
            denied_agents += res.status_code == 404
    hidden = sum(1 for d in r.registry.products.values() if not r.control.agent_ready(d))
    return {
        "agent_reads_checked": checked,
        "identical_api_mcp_a2a": same,
        "non_agent_products_hidden_from_agents": f"{denied_agents}/{hidden}",
    }


def access(wh, r) -> dict:
    rows = wh.rows(f"select STATUS, count(*) as N from {wh.schema('platform_schema')}.CHANGES group by STATUS")
    reqs = len({g.request_id for g in r.registry.grants if not g.request_id.startswith("parity")})
    return {
        "grants": reqs,
        "unmasked_grants_outside_domain": sum(
            1 for g in r.registry.grants if g.unmasked and g.domain != r.registry.products[g.product].domain
        ),
        "change_decisions": {x["status"]: x["n"] for x in rows},
    }


def console(r, batches, incidents) -> dict:
    """What the dashboard shows: the platform design only."""
    prods = []
    latest = defaultdict(lambda: None)
    for b in batches:
        if b["status"].startswith("published"):
            latest[b["product"]] = max(latest[b["product"]] or b["data_date"], b["data_date"])
    q = defaultdict(int)
    for b in batches:
        if b["status"] == "quarantined":
            q[b["product"]] += 1
    for pid, d in sorted(r.registry.products.items()):
        prods.append(
            {
                "id": pid,
                "domain": d.domain,
                "team": d.owner.team,
                "classification": d.classification,
                "ports": d.ports,
                "version": d.contract.version,
                "inputs": sorted(d.inputs),
                "latest": str(latest[pid]) if latest[pid] else None,
                "quarantined": q[pid],
                "agent_ready": r.control.agent_ready(d),
            }
        )
    gate_log = []
    for b in batches:
        if b["status"] == "quarantined":
            f = json.loads(b["findings"])
            gate_log.append(
                {
                    "at": b["decided_at"].isoformat(),
                    "product": b["product"],
                    "data_date": str(b["data_date"]),
                    "findings": [{"check": x["check"], "column": x["column"]} for x in f],
                }
            )
    changes = r.wh.rows(
        f"select * from {r.wh.schema('platform_schema')}.CHANGES where STATUS = 'rejected' order by DECIDED_AT, PRODUCT"
    )
    rejected = [
        {
            "at": c["decided_at"].isoformat(),
            "product": c["product"],
            "to": c["to_version"],
            "violations": [
                {"rule": v["rule"], "msg": v["msg"], "fix": v.get("fix", "")} for v in json.loads(c["violations"])
            ][:3],
        }
        for c in changes
    ]
    spend = defaultdict(float)
    for row in r.wh.rows(
        f"select TEAM, METHOD, sum(CREDITS) as C from {r.wh.schema('platform_schema')}.COST_ATTRIBUTION "
        "where METHOD like 'platform:%%' group by TEAM, METHOD"
    ):
        spend[(row["team"], row["method"].split(":")[1])] += row["c"]
    teams = sorted({t for t, _ in spend})
    cost = [
        {
            "team": t,
            **{m: round(spend.get((t, m), 0.0), 1) for m in ("tag", "role", "user", "warehouse", "idle", "none")},
        }
        for t in teams
    ]
    return {
        "products": prods,
        "gate_log": gate_log[-40:],
        "rejected_changes": rejected[-20:],
        "cost": cost,
        "alerts": [
            {
                "team": a["team"],
                "since": a["since"].isoformat(),
                "told_at": a["told_at"].isoformat(),
                "credits_per_hour": a["credits_per_hour"],
            }
            for a in r.alerts
        ],
        "incidents": [
            {
                "product": i["product"],
                "kind": i["kind"],
                "root": i["root_product"],
                "team": i["team"],
                "paged": i["paged"],
                "opened_at": i["opened_at"].isoformat(),
                "closed_at": i["closed_at"].isoformat() if i["closed_at"] else None,
            }
            for i in incidents
        ][-60:],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", action="store_true")
    args = ap.parse_args()
    name = "heldout" if args.heldout else "world"
    cfg = ROOT / "config" / f"{name}.toml"
    lock = ROOT / "config" / "heldout.lock"
    if args.heldout and lock.exists():
        sys.exit(f"the held-out world already ran ({lock.read_text().strip()}); see AGENTS.md")
    with ProcessPoolExecutor(max_workers=2) as pool:
        plat, base = pool.map(measure, [str(cfg)] * 2, ["platform", "baseline"])
    report = {"world": name, "platform": plat, "baseline": base}
    out = ROOT / "reports" / f"{'heldout' if args.heldout else 'dev'}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    if args.heldout:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
        lock.write_text(f"ran at commit {commit}\n")
    print(json.dumps({k: v for k, v in plat.items() if k != "console"}, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
