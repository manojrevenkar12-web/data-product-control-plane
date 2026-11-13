import copy
from datetime import datetime

from dpcp.controlplane import ControlPlane
from dpcp.incidents import Incidents
from dpcp.pipeline import Pipeline
from dpcp.policy import PolicyEngine
from dpcp.registry import Registry

from .conftest import needs_opa

NOW = datetime(2026, 2, 2, 10)


def plane(wh, settings, products, gates=True):
    settings.raw["gates"]["enabled"] = gates
    reg = Registry()
    pipe = Pipeline(wh, Incidents(wh, reg))
    cp = ControlPlane(pipe, reg, PolicyEngine(settings))
    for d in products.values():
        cp.propose(d.model_dump(mode="json"), NOW)
    return cp


def drop_spend(doc, version):
    doc = copy.deepcopy(doc)
    doc["contract"]["columns"] = [c for c in doc["contract"]["columns"] if c["name"] != "monthly_spend"]
    doc["contract"]["version"] = version
    return doc


@needs_opa
def test_breaking_change_read_by_a_consumer_is_rejected_with_the_fix(wh, settings, products):
    cp = plane(wh, settings, products)
    doc = products["survey.consumer_panel"].model_dump(mode="json")
    res = cp.propose(drop_spend(doc, "2.0.0"), NOW)
    assert res["status"] == "rejected"
    v = next(x for x in res["violations"] if x["rule"] == "consumers")
    assert "market.online_spend_index" in v["msg"] and v["fix"]
    assert cp.registry.products["survey.consumer_panel"].contract.version == "1.0.0"


@needs_opa
def test_breaking_change_nobody_reads_needs_a_major_and_gets_a_new_table(wh, settings, products):
    cp = plane(wh, settings, products)
    doc = products["survey.consumer_panel"].model_dump(mode="json")
    doc = copy.deepcopy(doc)
    doc["contract"]["columns"] = [c for c in doc["contract"]["columns"] if c["name"] != "comment"]
    doc["contract"]["version"] = "1.1.0"
    assert {v["rule"] for v in cp.propose(doc, NOW)["violations"]} == {"major"}
    doc["contract"]["version"] = "2.0.0"
    assert cp.propose(doc, NOW)["status"] == "accepted"
    tables = {
        r[0] for r in wh.run(f"select table_name from {wh.db}.information_schema.tables where table_schema = 'CORE'")
    }
    assert {"SURVEY__CONSUMER_PANEL_V1", "SURVEY__CONSUMER_PANEL_V2"} <= tables


@needs_opa
def test_monitoring_only_merges_the_breaking_change_in_place(wh, settings, products):
    cp = plane(wh, settings, products, gates=False)
    doc = products["survey.consumer_panel"].model_dump(mode="json")
    res = cp.propose(drop_spend(doc, "1.1.0"), NOW)
    assert res["status"] == "accepted_with_findings"
    cols = {
        r[0]
        for r in wh.run(
            f"select column_name from {wh.db}.information_schema.columns where table_name = 'SURVEY__CONSUMER_PANEL_V1'"
        )
    }
    assert "MONTHLY_SPEND" not in cols


@needs_opa
def test_onboarding_mistakes_name_the_field_to_fix(wh, settings, products):
    cp = plane(wh, settings, products)
    doc = products["market.online_spend_index"].model_dump(mode="json")
    doc = copy.deepcopy(doc)
    doc["id"] = "market.new_index"
    doc["contract"]["columns"][2]["unit"] = None
    doc["budget"]["credits_per_month"] = 5000
    res = cp.propose(doc, NOW)
    assert res["status"] == "rejected"
    assert {v["field"] for v in res["violations"]} == {"contract.columns.avg_monthly_spend", "budget.credits_per_month"}
    bad = {"id": "Not An Id"}
    assert cp.propose(bad, NOW)["violations"][0]["rule"] == "schema"
