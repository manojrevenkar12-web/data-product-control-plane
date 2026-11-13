"""A2A output port: an Agent Card and the JSON-RPC `message/send` method (A2A protocol 0.3).

A calling agent sends a message whose data part names a product, optional columns, filters and a limit; the
reply is a message with the rows as a data part. Only agent-ready products are listed as skills.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Header

from dpcp.ports.service import Denied, NotFound, ProductService

PROTOCOL = "0.3.0"


def agent_card(service: ProductService, base_url: str) -> dict:
    agent = next(iter(p for p in service.keys.values() if p.kind == "agent"), None)
    skills = [
        {
            "id": f"read:{p['id']}",
            "name": p["id"],
            "description": p["description"],
            "tags": [p["domain"]],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        }
        for p in (service.catalogue(agent) if agent else [])
    ]
    return {
        "protocolVersion": PROTOCOL,
        "name": "data-products",
        "description": "Governed data products",
        "url": f"{base_url}/a2a",
        "version": "1.0.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": skills,
    }


def _error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle(service: ProductService, key: str, body: dict) -> dict:
    rpc_id = body.get("id")
    if body.get("method") != "message/send":
        return _error(rpc_id, -32601, "method not found")
    try:
        who = service.principal(key)
        parts = body["params"]["message"]["parts"]
        req = next(p["data"] for p in parts if p.get("kind") == "data")
        if who.kind == "agent":
            service.describe(who, req["product"])  # agents only reach agent-ready products
        res = service.read(
            who, req["product"], "a2a", columns=req.get("columns"), filters=req.get("filters"), limit=req.get("limit")
        )
    except (KeyError, StopIteration):
        return _error(rpc_id, -32602, "expected a data part with a product")
    except Denied as e:
        return _error(rpc_id, -32001, str(e))
    except NotFound as e:
        return _error(rpc_id, -32002, f"not found: {e}")
    msg = {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "contextId": body["params"]["message"].get("contextId", str(uuid.uuid4())),
        "parts": [{"kind": "data", "data": res}],
    }
    return {"jsonrpc": "2.0", "id": rpc_id, "result": msg}


def mount(app: FastAPI, service: ProductService) -> None:
    @app.get("/.well-known/agent-card.json")
    def card():
        return agent_card(service, "")

    @app.post("/a2a")
    def rpc(body: dict, authorization: str | None = Header(default=None)):
        return handle(service, (authorization or "").removeprefix("Bearer ").strip(), body)
