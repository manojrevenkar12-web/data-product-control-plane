"""Write the README results and the dashboard data from reports/. Nothing here is typed by hand."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def ledger(rep: dict) -> list[dict]:
    p, b = rep["platform"], rep["baseline"]
    bd, pd_, ch, ob, sp, at, fr = (
        k for k in ("bad_data", "personal_data", "changes", "onboarding", "spend", "attribution", "freshness")
    )
    words = {
        "duplicate_keys": "duplicate keys",
        "null_keys": "missing keys",
        "out_of_range": "values out of range",
        "pii_in_free_text": "personal data in free text",
        "unit_change": "values in thousands",
    }
    types = ", ".join(words.get(t, t) for t in sorted(p[bd]["by_defect"]))
    return [
        {
            "label": "Bad deliveries whose rows reached consumers",
            "platform": n(p[bd]["reached_consumers"]),
            "baseline": n(b[bd]["reached_consumers"]),
            "note": f"of {p[bd]['bad_deliveries']} planted ({types})",
        },
        {
            "label": "Hours bad data was readable",
            "platform": n(p[bd]["hours_live"]),
            "baseline": n(b[bd]["hours_live"]),
            "note": f"until a corrected delivery replaced it; with gates, all of it is the "
            f"{p[bd]['reached_consumers']} "
            "misses, which no check flagged and so nobody corrected",
        },
        {
            "label": "Personal data published in a column not declared as personal",
            "platform": n(p[pd_]["values_published"]),
            "baseline": n(b[pd_]["values_published"]),
            "note": f"values; {p[pd_]['deliveries_with_undeclared_pii']} deliveries carried e-mail addresses or phone "
            "numbers in free text",
        },
        {
            "label": "Breaking contract changes that reached a consumer",
            "platform": n(p[ch]["breaking_reached_consumers"]),
            "baseline": n(b[ch]["breaking_reached_consumers"]),
            "note": f"of {p[ch]['breaking_with_consumers']} that removed, renamed, retyped or re-united a column "
            "someone reads",
        },
        {
            "label": "Onboarding mistakes that went live",
            "platform": n(p[ob]["mistake_went_live"]),
            "baseline": n(b[ob]["mistake_went_live"]),
            "note": f"of {p[ob]['with_mistake']}; {p[ob]['fixed_by_following_the_message']} fixed by the team from the "
            "message alone",
        },
        {
            "label": "Credits burnt by runaway queries and oversized warehouses",
            "platform": n(round(p[sp]["credits_burnt"])),
            "baseline": n(round(b[sp]["credits_burnt"])),
            "note": f"{p[sp]['episodes']} episodes; the team was told after a median {p[sp]['median_hours_to_team']} h "
            f"against {b[sp]['median_hours_to_team']} h",
        },
        {
            "label": "Spend nobody could attribute to a team",
            "platform": f"{p[at]['platform']['unattributed_pct']}%",
            "baseline": f"{p[at]['tag_only']['unattributed_pct']}%",
            "note": "same usage, two methods: tag, role, user and warehouse against query tags alone",
        },
        {
            "label": "Hours products were staler than their SLA",
            "platform": n(p[fr]["breach_hours"]),
            "baseline": n(b[fr]["breach_hours"]),
            "note": "the price of holding bad data back: consumers wait for the corrected delivery",
        },
    ]


def table(dev: dict, held: dict | None) -> str:
    rows = ledger(dev)
    hrows = ledger(held) if held else None
    head = "| | Gates (dev) | Monitoring only (dev) |" + (
        " Gates (held-out) | Monitoring only (held-out) |" if held else ""
    )
    sep = "|---|---:|---:|" + ("---:|---:|" if held else "")
    out = [head, sep]
    for i, r in enumerate(rows):
        line = f"| {r['label']} <br><sub>{r['note']}</sub> | **{r['platform']}** | {r['baseline']} |"
        if hrows:
            line += f" **{hrows[i]['platform']}** | {hrows[i]['baseline']} |"
        out.append(line)
    return "\n".join(out)


def facts(rep: dict) -> str:
    p = rep["platform"]
    pt, ev, cat, tf, lo = p["ports"], p["events"], p["catalogue"], p["terraform"], p["load_once"]
    fr = p["freshness"]
    return "\n".join(
        [
            f"- **{p['products']} products** from {tf['teams']} teams, {p['deliveries']:,} deliveries, "
            f"{p['batches']:,} batches decided, {p['policy_evaluations']} OPA evaluations.",
            f"- **Clean deliveries held by mistake:** {p['bad_data']['clean_blocked']} of "
            f"{p['bad_data']['clean_deliveries']:,}.",
            f"- **Row-level quarantine:** {p['bad_data']['valid_rows_published_rest_held']} deliveries published their "
            "valid rows and held the rest, instead of holding the whole day.",
            f"- **Same rows through API, MCP and A2A:** {pt['identical_api_mcp_a2a']} of {pt['agent_reads_checked']} "
            "agent reads; products not offered to agents hidden from them: "
            f"{pt['non_agent_products_hidden_from_agents']}.",
            f"- **Kafka:** {ev['events']:,} events for {ev['published_batches']:,} published batches, "
            f"{ev['unique_event_ids']:,} distinct ids (sent from the outbox, marked only on delivery).",
            f"- **Files sent twice:** {lo['sent_twice']}, loaded once each ({lo['skipped']} skipped).",
            f"- **Freshness incidents:** {fr['incidents']}, of which {fr['paged']} paged a team; "
            f"{fr['routed_to_root_cause_team']} went to the team that owned the cause (downstream products are linked "
            "to their upstream incident instead of paging their own team).",
            f"- **Cost attribution error per team and day:** {p['attribution']['platform']['team_day_error_pct']}% "
            f"against {p['attribution']['tag_only']['team_day_error_pct']}% with query tags alone.",
            f"- **OpenMetadata:** {cat['tables']} tables, {cat['data_products']} data products, "
            f"{cat['lineage_edges']} lineage edges; **Terraform:** {tf['roles']} roles, {tf['teams']} warehouses with "
            f"resource monitors, {tf['kafka_topics']} Kafka topics.",
        ]
    )


def main() -> None:
    dev = json.loads((ROOT / "reports/dev.json").read_text())
    held_path = ROOT / "reports/heldout.json"
    held = json.loads(held_path.read_text()) if held_path.exists() else None
    readme = ROOT / "README.md"
    text = readme.read_text()
    block = f"<!-- results:start -->\n{table(dev, held)}\n\n{facts(dev)}\n<!-- results:end -->"
    text = re.sub(r"<!-- results:start -->.*<!-- results:end -->", lambda _: block, text, flags=re.S)
    readme.write_text(text)
    console = {
        "world": "dev",
        "generated_from": "reports/dev.json",
        "ledger": ledger(dev),
        **dev["platform"]["console"],
    }
    out = ROOT / "web/public/data/console.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(console, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
