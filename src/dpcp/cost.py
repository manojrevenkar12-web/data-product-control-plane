"""Cost per team and product from Snowflake's own usage views, and spend anomalies found the next morning.

Attribution, in order: the query tag the platform sets on every job; else the role (Terraform creates one role
set per product and team, so a role has an owner); else the user's team from the identity provider (SCIM groups);
else the warehouse, when it is a team's own. Idle warehouse
time goes to the warehouse's owner, or pro rata to the teams that used a shared warehouse that hour.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

from dpcp.warehouse import Warehouse

UNATTRIBUTED = "(unattributed)"

ATTRIBUTION_SQL = """
with q as (
  select date_trunc('hour', a.START_TIME) as HR, a.WAREHOUSE_NAME as WH, a.CREDITS_ATTRIBUTED_COMPUTE as CREDITS,
         try_parse_json(a.QUERY_TAG):team::string as TAG_TEAM, r.TEAM as ROLE_TEAM, u.TEAM as USER_TEAM,
         w.TEAM as WH_TEAM
  from {usage}.QUERY_ATTRIBUTION_HISTORY a
  join {usage}.QUERY_HISTORY h on h.QUERY_ID = a.QUERY_ID
  left join {platform}.ROLE_OWNERS r on r.ROLE_NAME = h.ROLE_NAME
  left join {platform}.USER_OWNERS u on u.USER_NAME = h.USER_NAME
  left join {platform}.WAREHOUSE_OWNERS w on w.WAREHOUSE_NAME = a.WAREHOUSE_NAME
  where a.START_TIME >= %s and a.START_TIME < %s
), labelled as (
  select HR, WH, CREDITS,
         {team_expr} as TEAM,
         {method_expr} as METHOD
  from q
), used as (
  select HR, WH, TEAM, METHOD, sum(CREDITS) as CREDITS from labelled group by HR, WH, TEAM, METHOD
), metered as (
  select date_trunc('hour', START_TIME) as HR, WAREHOUSE_NAME as WH, sum(CREDITS_USED_COMPUTE) as METERED
  from {usage}.WAREHOUSE_METERING_HISTORY where START_TIME >= %s and START_TIME < %s
  group by date_trunc('hour', START_TIME), WAREHOUSE_NAME
), idle as (
  select m.HR, m.WH, greatest(m.METERED - coalesce(sum(u.CREDITS), 0), 0) as IDLE
  from metered m left join used u on u.HR = m.HR and u.WH = m.WH group by m.HR, m.WH, m.METERED
)
select HR, WH, TEAM, METHOD, CREDITS from used
union all
select i.HR, i.WH, {idle_team} as TEAM, 'idle' as METHOD,
       i.IDLE * {idle_share} as CREDITS
from idle i
left join {platform}.WAREHOUSE_OWNERS w on w.WAREHOUSE_NAME = i.WH
left join (select HR, WH, TEAM, CREDITS, sum(CREDITS) over (partition by HR, WH) as TOTAL
           from used where TEAM <> '{un}') s on s.HR = i.HR and s.WH = i.WH and w.TEAM is null
where i.IDLE > 0
"""

PLATFORM = {
    "team_expr": "coalesce(TAG_TEAM, ROLE_TEAM, USER_TEAM, WH_TEAM, '{un}')",
    "method_expr": "case when TAG_TEAM is not null then 'tag' when ROLE_TEAM is not null then 'role' "
    "when USER_TEAM is not null then 'user' when WH_TEAM is not null then 'warehouse' else 'none' end",
    "idle_team": "coalesce(w.TEAM, s.TEAM, '{un}')",
    "idle_share": "case when w.TEAM is not null or s.TEAM is null then 1 else s.CREDITS / s.TOTAL end",
}
TAG_ONLY = {  # the baseline: what a query-tag convention alone gives you
    "team_expr": "coalesce(TAG_TEAM, '{un}')",
    "method_expr": "case when TAG_TEAM is not null then 'tag' else 'none' end",
    "idle_team": "'{un}'",
    "idle_share": "case when s.TEAM is null then 1 else 0 end",
}


class CostJob:
    def __init__(self, wh: Warehouse, method: str = "platform"):
        self.wh = wh
        self.method = method
        c = wh.settings["cost"]
        self.ratio, self.floor = c["anomaly_ratio"], c["anomaly_floor_credits"]
        self.lookback, self.consecutive = c["anomaly_lookback_hours"], c["anomaly_consecutive_hours"]
        self.platform = wh.schema("platform_schema")
        self.usage = f"{wh.settings['snowflake']['usage_database']}.ACCOUNT_USAGE"
        parts = PLATFORM if method == "platform" else TAG_ONLY
        self.sql = ATTRIBUTION_SQL.format(
            usage=self.usage,
            platform=self.platform,
            un=UNATTRIBUTED,
            **{k: v.format(un=UNATTRIBUTED) for k, v in parts.items()},
        )
        self.hourly: dict[str, dict[datetime, float]] = defaultdict(lambda: defaultdict(float))
        self.alerted: dict[str, datetime] = {}

    def set_owners(self, roles: dict[str, str], warehouses: dict[str, str], users: dict[str, str]) -> None:
        for table, mapping in (("ROLE_OWNERS", roles), ("WAREHOUSE_OWNERS", warehouses), ("USER_OWNERS", users)):
            self.wh.run(f"delete from {self.platform}.{table}")
            if mapping:
                values = ", ".join(["(%s, %s)"] * len(mapping))
                self.wh.run(
                    f"insert into {self.platform}.{table} values {values}",
                    tuple(x for kv in sorted(mapping.items()) for x in kv),
                )

    def attribute(self, day: date) -> list[dict]:
        start, end = (
            datetime.combine(day, datetime.min.time()),
            datetime.combine(day + timedelta(days=1), datetime.min.time()),
        )
        rows = self.wh.rows(self.sql, (start, end, start, end))
        out = [
            {
                "day": day,
                "hour": r["hr"],
                "team": r["team"],
                "warehouse": r["wh"],
                "credits": float(r["credits"]),
                "method": r["method"],
            }
            for r in rows
            if r["credits"]
        ]
        if out:
            values = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(out))
            params = tuple(
                x
                for o in out
                for x in (day, o["hour"], o["team"], o["warehouse"], o["credits"], f"{self.method}:{o['method']}")
            )
            self.wh.run(f"insert into {self.platform}.COST_ATTRIBUTION values {values}", params)
        for o in out:
            self.hourly[o["team"]][o["hour"]] += o["credits"]
        return out

    def anomalies(self, day: date) -> list[dict]:
        """Team-hours of `day` far above the team's own recent hours, for `consecutive` hours in a row."""
        found = []
        start = datetime.combine(day, datetime.min.time())
        for team, series in sorted(self.hourly.items()):
            if team == UNATTRIBUTED:
                continue
            run = 0
            for h in range(24):
                hour = start + timedelta(hours=h)
                past = [series.get(hour - timedelta(hours=k), 0.0) for k in range(1, self.lookback + 1)]
                base = statistics.median(past)
                value = series.get(hour, 0.0)
                run = run + 1 if value >= self.floor and value >= self.ratio * max(base, 1e-9) else 0
                if run >= self.consecutive and team not in self.alerted:
                    self.alerted[team] = hour
                    found.append(
                        {
                            "team": team,
                            "since": hour - timedelta(hours=self.consecutive - 1),
                            "credits_per_hour": round(value, 2),
                            "baseline": round(base, 3),
                        }
                    )
            if run == 0:
                self.alerted.pop(team, None)
        return found
