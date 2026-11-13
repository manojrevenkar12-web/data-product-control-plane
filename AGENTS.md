# Working on data-product-control-plane

## Fast feedback

```bash
pytest -q                                  # pipeline, control plane, cost, SLA, ports, catalogue (~10 s)
opa test policies -v                       # the Rego, with its own unit tests
ruff check src tests scripts
dpcp check products/                       # the reference descriptors pass the policies
python3 scripts/evaluate.py                # both designs on the dev world, ~10 min on 2 cores
python3 scripts/readme.py && git diff --exit-code README.md web/public/data/console.json
cd web && npm test && npm run build
```

## Invariants

1. **Truth stays in the world.** `src/dpcp/world/` and `scripts/evaluate.py` are the only code that knows what was
   planted. Nothing else under `src/dpcp` imports `dpcp.world` (a test checks it).
2. **The world does not read the platform's settings.** `config/world.toml` drives it; `config/platform.toml` is the
   platform's. Changing a threshold must not change what happens in the world.
3. **Same future for every design.** The world draws from one random stream per event (`World._r`), so both designs
   see the same deliveries, defects, changes and requests whatever the platform does.
4. **The policies decide.** Rules live in `policies/*.rego` and are evaluated by OPA. Python prepares facts (diffs,
   consumers, requests) and never re-implements a rule. Every violation names a field and a fix.
5. **Nothing reaches a consumer before its checks.** With gates on, a delivery is published only after the checks in
   `Pipeline.check`; row-scoped findings may publish the valid rows, anything else is held in staging.
6. **One serving path.** API, MCP and A2A call `ProductService`; none of them builds SQL or decides access.
7. **An event is sent once it is stored.** Publishing and its outbox row commit together; a row is marked sent only
   on a delivery report.
8. **Settings live in config**, thresholds included. **Reports are generated**: `reports/*.json` by
   `scripts/evaluate.py`; the README results and `web/public/data/console.json` by `scripts/readme.py`.

## The held-out rule

`python3 scripts/evaluate.py --heldout` runs the held-out world once and writes `config/heldout.lock`. After that,
changes are measured on the dev world only; the held-out numbers stay as they first came out.
