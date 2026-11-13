import asyncio
import json
from datetime import date, datetime

import httpx
from fastapi.testclient import TestClient

from dpcp.catalog import OpenMetadata
from dpcp.controlplane import ControlPlane
from dpcp.incidents import Incidents
from dpcp.pipeline import Delivery, Pipeline
from dpcp.policy import PolicyEngine
from dpcp.ports import mcp_server
from dpcp.ports.api import create_app
from dpcp.ports.events import MemoryProducer, OutboxPublisher
from dpcp.ports.service import Principal, ProductService
from dpcp.registry import Registry

from .conftest import needs_opa


def world(wh, settings, products):
    reg = Registry()
    pipe = Pipeline(wh, Incidents(wh, reg))
    cp = ControlPlane(pipe, reg, PolicyEngine(settings))
    for d in products.values():
        assert cp.propose(d.model_dump(mode="json"), datetime(2026, 1, 1))["status"] == "accepted"
    idx = products["market.online_spend_index"]
    rows = [
        {"country": c, "wave": "2026-W03", "avg_monthly_spend": 100.0 + i, "respondents": 50}
        for i, c in enumerate(["AT", "CH", "DE"])
    ]
    pipe.ingest(
        idx, Delivery(idx.id, "s3://x/1", "h1", date(2026, 1, 5), datetime(2026, 1, 6), rows), datetime(2026, 1, 6)
    )
    svc = ProductService(wh, reg, cp)
    svc.add_key("agent-key", Principal("research-assistant", "agent", "market"))
    svc.add_key("analyst-key", Principal("bi-team", "human", "finance"))
    reqs = [
        {
            "id": f"r-{port}",
            "principal": {"name": "research-assistant", "kind": "agent", "domain": "market"},
            "product": idx.id,
            "port": port,
            "purpose": "",
            "columns": ["country", "avg_monthly_spend"],
        }
        for port in ("api", "mcp", "a2a")
    ]
    reqs.append(
        {
            "id": "r-conf",
            "principal": {"name": "research-assistant", "kind": "agent", "domain": "market"},
            "product": "survey.consumer_panel",
            "port": "api",
            "purpose": "",
            "columns": [],
        }
    )
    return svc, cp, cp.request_access(reqs), idx


@needs_opa
def test_same_rows_through_api_mcp_and_a2a(wh, settings, products):
    svc, _, decisions, idx = world(wh, settings, products)
    assert not decisions["r-conf"]["allow"] and decisions["r-api"]["allow"]
    client = TestClient(create_app(svc))
    h = {"Authorization": "Bearer agent-key"}
    via_api = client.get(f"/products/{idx.id}/rows", headers=h).json()["rows"]
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"product": idx.id}}]}
        },
    }
    via_a2a = client.post("/a2a", json=rpc, headers=h).json()["result"]["parts"][0]["data"]["rows"]
    server = mcp_server.build(svc, svc.principal("agent-key"))
    res = asyncio.run(server.call_tool("read_product", {"product_id": idx.id}))
    via_mcp = json.loads(res.content[0].text)["rows"]
    assert via_api == via_a2a == via_mcp and len(via_api) == 3
    card = client.get("/.well-known/agent-card.json").json()
    assert [s["name"] for s in card["skills"]] == [idx.id]  # the confidential panel is not offered to agents
    assert client.get("/products/survey.consumer_panel/rows", headers=h).status_code == 403
    assert client.get("/products", headers={"Authorization": "Bearer nope"}).status_code == 401


@needs_opa
def test_outbox_sends_in_order_and_retries(wh, settings, products):
    svc, cp, _, idx = world(wh, settings, products)
    flaky = MemoryProducer(fail_every=1)
    pub = OutboxPublisher(wh, flaky, {idx.id: 1})
    assert pub.publish(datetime(2026, 1, 6)) == 0
    ok = MemoryProducer()
    assert OutboxPublisher(wh, ok, {idx.id: 1}).publish(datetime(2026, 1, 6)) == 1
    ((key, event),) = ok.topics["dataproduct.market.online_spend_index.v1"]
    assert key == idx.id and event["data"]["data_date"] == "2026-01-05" and event["specversion"] == "1.0"
    assert OutboxPublisher(wh, ok, {idx.id: 1}).publish(datetime(2026, 1, 7)) == 0


def test_openmetadata_sync(products):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.url.path.startswith("/api/v1/teams/name/"):
            return httpx.Response(200, json={"id": "team-" + req.url.path.rsplit("/", 1)[1]})
        if req.url.path == "/api/v1/tables":
            return httpx.Response(
                200, json={"id": "t-" + body["name"], "fullyQualifiedName": f"sf.DATA_PLATFORM.SERVE.{body['name']}"}
            )
        return httpx.Response(200, json={})

    om = OpenMetadata("http://om", "t", "sf", "DATA_PLATFORM", "SERVE", transport=httpx.MockTransport(handler))
    assert om.sync(list(products.values())) == {"tables": 3, "data_products": 3, "lineage_edges": 1}
    panel = next(b for m, p, b in calls if p == "/api/v1/tables" and b["name"].startswith("SURVEY"))
    assert [c["tags"] for c in panel["columns"] if c["name"] == "EMAIL"] == [
        [{"tagFQN": "PII.Sensitive", "source": "Classification", "labelType": "Manual", "state": "Confirmed"}]
    ]
    edge = next(b for m, p, b in calls if p == "/api/v1/lineage")["edge"]
    assert edge["fromEntity"]["id"].startswith("t-SURVEY") and edge["toEntity"]["id"].startswith("t-MARKET")
