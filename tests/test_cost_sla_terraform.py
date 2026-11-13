import json
from datetime import date, datetime, timedelta

from dpcp import terraform
from dpcp.cost import UNATTRIBUTED, CostJob
from dpcp.incidents import Incidents
from dpcp.pipeline import Pipeline
from dpcp.registry import Registry
from dpcp.settings import ROOT
from dpcp.sla import SlaMonitor


def usage(wh, rows, metering):
    db = wh.settings["snowflake"]["usage_database"]
    wh.run(f"create database if not exists {db}")
    wh.run(f"create schema if not exists {db}.ACCOUNT_USAGE")
    wh.run(
        f"create table {db}.ACCOUNT_USAGE.QUERY_HISTORY (QUERY_ID varchar, ROLE_NAME varchar, USER_NAME varchar, "
        "WAREHOUSE_NAME varchar, QUERY_TAG varchar, START_TIME timestamp_ntz)"
    )
    wh.run(
        f"create table {db}.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY (QUERY_ID varchar, WAREHOUSE_NAME varchar, "
        "QUERY_TAG varchar, START_TIME timestamp_ntz, CREDITS_ATTRIBUTED_COMPUTE float)"
    )
    wh.run(
        f"create table {db}.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY (WAREHOUSE_NAME varchar, "
        "START_TIME timestamp_ntz, CREDITS_USED_COMPUTE float)"
    )
    for i, (wname, role, user, tag, credits) in enumerate(rows):
        t = datetime(2026, 3, 2, 10, 5)
        wh.run(
            f"insert into {db}.ACCOUNT_USAGE.QUERY_HISTORY values (%s, %s, %s, %s, %s, %s)",
            (f"q{i}", role, user, wname, tag, t),
        )
        wh.run(
            f"insert into {db}.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY values (%s, %s, %s, %s, %s)",
            (f"q{i}", wname, tag, t, credits),
        )
    for wname, credits in metering:
        wh.run(
            f"insert into {db}.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY values (%s, %s, %s)",
            (wname, datetime(2026, 3, 2, 10), credits),
        )


def test_attribution_order_and_idle(wh):
    usage(
        wh,
        [
            ("WH_ADHOC", "X", "u1", json.dumps({"team": "a"}), 1.0),  # tag
            ("WH_ADHOC", "TEAM_B", "u9", "", 1.0),  # role
            ("WH_ADHOC", "PUBLIC", "u2", "", 2.0),  # user directory
            ("WH_ADHOC", "PUBLIC", "nobody", "", 1.0),  # nothing known
            ("WH_C", "PUBLIC", "nobody", "", 1.0),
        ],  # the team's own warehouse
        [("WH_ADHOC", 6.0), ("WH_C", 1.5)],
    )
    job = CostJob(wh, "platform")
    job.set_owners({"TEAM_B": "b"}, {"WH_C": "c"}, {"u2": "a"})
    got = {}
    for r in job.attribute(date(2026, 3, 2)):
        got[(r["team"], r["method"])] = round(got.get((r["team"], r["method"]), 0) + r["credits"], 3)
    assert got[("a", "tag")] == 1.0 and got[("b", "role")] == 1.0 and got[("a", "user")] == 2.0
    assert got[("c", "warehouse")] == 1.0 and got[("c", "idle")] == 0.5
    assert got[(UNATTRIBUTED, "none")] == 1.0
    # shared warehouse idle (6 - 5 = 1) goes pro rata to a (3 of 4) and b (1 of 4)
    assert got[("a", "idle")] == 0.75 and got[("b", "idle")] == 0.25
    tag_only = CostJob(wh, "tag_only")
    un = sum(r["credits"] for r in tag_only.attribute(date(2026, 3, 3)))
    assert un == 0  # nothing that day


def test_anomaly_needs_consecutive_hours_above_the_team_baseline(wh):
    job = CostJob(wh, "platform")
    start = datetime(2026, 3, 1)
    for h in range(24 * 8):
        job.hourly["a"][start + timedelta(hours=h)] = 0.2
    spike = datetime(2026, 3, 9, 3)
    job.hourly["a"][spike] = 5.0
    assert job.anomalies(date(2026, 3, 9)) == []  # one hour is not a runaway
    job.hourly["a"][spike + timedelta(hours=1)] = 5.0
    (alert,) = job.anomalies(date(2026, 3, 9))
    assert alert["team"] == "a" and alert["since"] == spike


def test_freshness_incident_goes_to_the_upstream_team(wh, products):
    reg = Registry()
    for d in products.values():
        reg.put(d)
    inc = Incidents(wh, reg)
    sla = SlaMonitor(Pipeline(wh, inc), reg)
    sla.observe_publish("survey.consumer_panel", date(2026, 3, 1))
    sla.observe_publish("market.online_spend_index", date(2026, 3, 1))
    sla.observe_publish("commerce.shop_orders", date(2026, 3, 3))
    opened = sla.run(datetime(2026, 3, 3, 8))  # panel SLA 26 h, index 30 h: both stale
    by = {i["product"]: i for i in opened}
    assert by["survey.consumer_panel"]["team"] == "survey-data" and by["survey.consumer_panel"]["paged"]
    idx = by["market.online_spend_index"]
    assert idx["root_product"] == "survey.consumer_panel" and idx["team"] == "survey-data" and not idx["paged"]
    sla.observe_publish("survey.consumer_panel", date(2026, 3, 2))
    sla.run(datetime(2026, 3, 3, 9))
    assert ("survey.consumer_panel", "freshness") not in inc.open_now


def test_terraform_inputs_follow_the_descriptors(products, settings):
    doc = terraform.render(list(products.values()), settings.raw)
    panel = doc["products"]["survey.consumer_panel"]
    assert panel["pii_columns"] == ["EMAIL"] and panel["roles"]["reader_full"].endswith("_READER_FULL")
    assert doc["products"]["market.online_spend_index"]["kafka_topic"] == "dataproduct.market.online_spend_index.v1"
    assert doc["teams"]["survey-data"]["credits_per_month"] == 60
    module = (ROOT / "infra/modules/snowflake_data_product/variables.tf").read_text()
    for key in panel:
        assert key in module, key


def test_only_the_harness_and_evaluation_read_the_truth():
    src = ROOT / "src" / "dpcp"
    for path in src.rglob("*.py"):
        if "world" in path.parts:
            continue
        text = path.read_text()
        assert "dpcp.world" not in text and "truth" not in text, path
    sim = (src / "world" / "sim.py").read_text()
    assert "platform.toml" not in sim and "dpcp.settings" not in sim  # the world does not read the platform's settings
