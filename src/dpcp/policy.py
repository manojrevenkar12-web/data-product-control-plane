"""Policies as code: the Rego in policies/ decides, evaluated by OPA in one batch per call.

Python prepares the facts (descriptors, contract diffs, consumers, access requests) and OPA returns the
violations. Nothing in Python second-guesses a decision.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from dpcp.settings import ROOT, Settings

POLICY_DIR = ROOT / "policies"


class PolicyError(RuntimeError):
    pass


def opa_binary() -> str:
    found = os.environ.get("DPCP_OPA") or shutil.which("opa")
    if not found:
        raise PolicyError("OPA is not installed: https://www.openpolicyagent.org/docs/#running-opa")
    return found


class PolicyEngine:
    def __init__(self, settings: Settings, policy_dir: Path = POLICY_DIR):
        self.policy_dir = policy_dir
        self._data = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"limits": settings["policy"]}, self._data)
        self._data.close()
        self.calls = 0

    def evaluate(self, products=(), changes=(), access=()) -> dict:
        doc = {"products": list(products), "changes": list(changes), "access": list(access)}
        cmd = [
            opa_binary(),
            "eval",
            "--format",
            "json",
            "--stdin-input",
            "-d",
            str(self.policy_dir),
            "-d",
            self._data.name,
            "data.dpcp",
        ]
        proc = subprocess.run(cmd, input=json.dumps(doc), capture_output=True, text=True, check=False)
        if proc.returncode:
            raise PolicyError(proc.stderr.strip() or proc.stdout.strip())
        self.calls += 1
        value = json.loads(proc.stdout)["result"][0]["expressions"][0]["value"]
        return {k: value.get(k, []) for k in ("deny_product", "deny_change", "deny_access", "unmasked")}

    def product_violations(self, products: list[dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for v in self.evaluate(products=products)["deny_product"]:
            out[v["id"]].append(v)
        return {k: sorted(v, key=lambda x: (x["rule"], x["field"])) for k, v in out.items()}

    def change_violations(self, changes: list[dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for v in self.evaluate(changes=changes)["deny_change"]:
            out[v["id"]].append(v)
        return dict(out)

    def access_decisions(self, requests: list[dict]) -> dict[str, dict]:
        res = self.evaluate(access=requests)
        denied: dict[str, list[str]] = defaultdict(list)
        for v in res["deny_access"]:
            denied[v["id"]].append(v["msg"])
        unmasked = set(res["unmasked"])
        return {
            r["id"]: {
                "allow": r["id"] not in denied,
                "reasons": sorted(denied.get(r["id"], [])),
                "unmasked": r["id"] in unmasked,
            }
            for r in requests
        }
