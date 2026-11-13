"""dpcp: what a domain team and the platform's CI run.

dpcp check products/            policies and contract diff for every descriptor (exit 1 on a violation)
dpcp check products/ --base main   ... diffed against the descriptors on another git ref
dpcp render products/           write infra/envs/prod/products.auto.tfvars.json for Terraform
dpcp serve                      the REST and A2A ports on :8000 (emulated Snowflake unless configured)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from dpcp import settings as settings_mod
from dpcp import terraform
from dpcp.contract import change_input
from dpcp.descriptor import Descriptor, load_all
from dpcp.policy import PolicyEngine
from dpcp.settings import ROOT


def _base_descriptors(ref: str, root: Path) -> dict[str, Descriptor]:
    rel = root.resolve().relative_to(ROOT)
    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, str(rel)], capture_output=True, text=True, cwd=ROOT, check=False
    ).stdout.split()
    out = {}
    for name in names:
        if name.endswith(".yaml"):
            text = subprocess.run(["git", "show", f"{ref}:{name}"], capture_output=True, text=True, cwd=ROOT).stdout
            d = Descriptor.model_validate(yaml.safe_load(text))
            out[d.id] = d
    return out


def check(root: Path, base: str | None) -> int:
    s = settings_mod.load()
    products = load_all(root)
    engine = PolicyEngine(s)
    violations = engine.product_violations([d.policy_input() for d in products])
    if base:
        old = _base_descriptors(base, root)
        consumers: dict[str, dict[str, set]] = {}
        for d in products:
            for up, cols in d.inputs.items():
                consumers.setdefault(up, {})[d.id] = set(cols)
        changes = [
            change_input(d.id, old[d.id].contract, d.contract, consumers.get(d.id, {}))
            for d in products
            if d.id in old and old[d.id].contract != d.contract
        ]
        for pid, v in engine.change_violations(changes).items():
            violations.setdefault(pid, []).extend(v)
    for pid in sorted(violations):
        for v in violations[pid]:
            print(f"{pid}: {v['field'] if 'field' in v else ''}: {v['msg']}\n    fix: {v.get('fix', '')}")
    print(f"{len(products)} products, {sum(len(v) for v in violations.values())} violations")
    return 1 if violations else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dpcp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("root", type=Path)
    c.add_argument("--base")
    r = sub.add_parser("render")
    r.add_argument("root", type=Path)
    r.add_argument("--out", type=Path, default=ROOT / "infra/envs/prod/products.auto.tfvars.json")
    sub.add_parser("serve")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        return check(args.root, args.base)
    if args.cmd == "render":
        doc = terraform.write(load_all(args.root), settings_mod.load().raw, args.out)
        print(json.dumps({"products": len(doc["products"]), "teams": len(doc["teams"]), "out": str(args.out)}))
        return 0
    if args.cmd == "serve":
        import uvicorn

        from dpcp.controlplane import ControlPlane
        from dpcp.incidents import Incidents
        from dpcp.pipeline import Pipeline
        from dpcp.ports.api import create_app
        from dpcp.ports.service import ProductService
        from dpcp.registry import Registry
        from dpcp.warehouse import Warehouse, bootstrap

        s = settings_mod.load()
        wh = Warehouse(s)
        bootstrap(wh)
        reg = Registry()
        cp = ControlPlane(Pipeline(wh, Incidents(wh, reg)), reg, PolicyEngine(s))
        for d in load_all(ROOT / "products"):
            cp.propose(d.model_dump(mode="json"), __import__("datetime").datetime.now())
        uvicorn.run(create_app(ProductService(wh, reg, cp)), host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
