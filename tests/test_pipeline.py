from datetime import date, datetime

from dpcp.incidents import Incidents
from dpcp.pipeline import Delivery, Pipeline
from dpcp.registry import Registry


def rows(n=20, **over):
    out = []
    for i in range(n):
        r = {
            "respondent_id": f"r{i}",
            "wave": "2026-W03",
            "country": "DE",
            "email": f"p{i}@mail.de",
            "weight": 1.0,
            "monthly_spend": 100.0 + i,
            "comment": "fine",
        }
        r.update({k: v(i) if callable(v) else v for k, v in over.items()})
        out.append(r)
    return out


def setup(wh, products, gates=True):
    wh.settings.raw["gates"]["enabled"] = gates
    reg = Registry()
    for d in products.values():
        reg.put(d)
    pipe = Pipeline(wh, Incidents(wh, reg))
    d = products["survey.consumer_panel"]
    pipe.ensure_product(d)
    return pipe, d


def deliver(pipe, d, day, rws, tag="a"):
    delivery = Delivery(d.id, f"s3://x/{day}-{tag}.csv", f"{day}{tag}" * 4, day, datetime(2026, 1, 1), rws)
    return pipe.ingest(d, delivery, datetime(2026, 1, 1))


def test_clean_delivery_is_published_and_masked(wh, products):
    pipe, d = setup(wh, products)
    assert deliver(pipe, d, date(2026, 1, 5), rows())["status"] == "published"
    masked = wh.rows(f"select EMAIL from {pipe.serve}.{d.table} limit 1")[0]["email"]
    full = wh.rows(f"select EMAIL from {pipe.serve}.{d.table}__FULL order by RESPONDENT_ID limit 1")[0]["email"]
    assert "@" not in masked and full == "p0@mail.de"


def test_same_file_is_loaded_once(wh, products):
    pipe, d = setup(wh, products)
    deliver(pipe, d, date(2026, 1, 5), rows())
    assert deliver(pipe, d, date(2026, 1, 5), rows())["status"] == "duplicate"


def test_bad_deliveries_are_quarantined(wh, products):
    wh.settings.raw["gates"]["row_quarantine_max_share"] = 0.0
    pipe, d = setup(wh, products)
    for i, day in enumerate([date(2026, 1, x) for x in range(1, 9)]):  # 8 days of history
        deliver(pipe, d, day, rows(), tag=f"g{i}")
    cases = {
        "required_null": rows(respondent_id=lambda i: None if i == 3 else f"r{i}"),
        "duplicate_key": rows(respondent_id="same"),
        "out_of_range": rows(weight=-1.0),
        "undeclared_pii": rows(comment=lambda i: f"call me +49 176 1234567{i}"),
        "scale_shift": rows(monthly_spend=lambda i: (100.0 + i) * 1000),
    }
    for check, rws in cases.items():
        res = deliver(pipe, d, date(2026, 1, 9), rws, tag=check)
        assert res["status"] == "quarantined", check
        assert check in {f["check"] for f in res["findings"]}, (check, res["findings"])
    live = wh.run(f"select count(*) from {pipe.core}.{d.table} where _DATA_DATE = '2026-01-09'")[0][0]
    assert live == 0


def test_no_scale_check_without_enough_history(wh, products):
    pipe, d = setup(wh, products)
    deliver(pipe, d, date(2026, 1, 1), rows(monthly_spend=lambda i: (100.0 + i) * 1000), tag="first")
    for x in range(2, 6):  # a bad first day must not make every normal day after it look shifted
        assert deliver(pipe, d, date(2026, 1, x), rows(), tag=f"n{x}")["status"] == "published"


def test_row_scoped_findings_publish_the_valid_rows(wh, products):
    wh.settings.raw["gates"]["row_quarantine_max_share"] = 0.1
    pipe, d = setup(wh, products)
    res = deliver(pipe, d, date(2026, 1, 6), rows(20, weight=lambda i: -1.0 if i == 4 else 1.0))
    assert res["status"] == "published_partial"
    live = wh.run(f"select count(*), min(WEIGHT) from {pipe.core}.{d.table} where _DATA_DATE = '2026-01-06'")[0]
    assert live == (19, 1.0)
    assert pipe.incidents.open_now[(d.id, "quality")]["evidence"]["status"] == "published_partial"
    res = deliver(pipe, d, date(2026, 1, 7), rows(20, weight=lambda i: -1.0 if i < 4 else 1.0))
    assert res["status"] == "quarantined"  # 20% invalid is above the share: hold it all


def test_monitoring_only_publishes_and_alerts(wh, products):
    pipe, d = setup(wh, products, gates=False)
    res = deliver(pipe, d, date(2026, 1, 6), rows(weight=-1.0))
    assert res["status"] == "published_with_findings"
    assert pipe.incidents.open_now[(d.id, "quality")]["team"] == "survey-data"
