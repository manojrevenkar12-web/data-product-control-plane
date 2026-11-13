# Decisions

## 1. Rules are Rego, evaluated by OPA, and nothing else decides

Product, change and access rules live in `policies/`. Python computes facts (a contract diff, who reads which
column, the request) and hands them to OPA in one batch; it never repeats a rule. Domain teams, the platform team and
an auditor read the same file, `opa test` covers it, and a rule change is a reviewed diff.
*Rejected:* rules in Python (easy to write, impossible to show to a governance board); a policy SaaS (another
system to run for what one binary does).

## 2. A violation names the field and the fix

Self-service fails at the error message. Every `deny` carries `field` and `fix`, and the world's teams fix exactly
the field the message names, nothing more. The evaluation counts how many planted onboarding mistakes were fixed by
the message alone; a mistake that needs a human to explain it shows up there.

## 3. Checks run in Snowflake, between staging and publishing

One staging table per delivery, one aggregate query for all column checks, publish as `DELETE` + `INSERT` of the
day in one transaction with the outbox row. The checks scale with the warehouse and cost credits that are
attributed like everything else. *Rejected:* checks in Python on the client (moves data out of Snowflake);
checks after publishing (the monitoring-only design, which the evaluation measures).

## 4. Holding data back has a price, so row-scoped findings hold rows, not days

The first version held any delivery with a finding. The evaluation showed what that costs: consumers waited for the
corrected delivery, and freshness SLA breaches rose against monitoring only. Row-scoped findings (a missing required
value, a value out of range) now publish the valid rows and keep the invalid ones in staging when they are at most
`row_quarantine_max_share` of the delivery. Batch-scoped findings (duplicate keys, a scale shift, personal data in
an undeclared column) still hold everything: there is no row to drop that makes them safe.

## 5. A breaking change is a new table, not an `ALTER`

`CORE.<product>_V<major>`. Readers of v1 keep working; the policy refuses a breaking change in place while anyone
reads the column. The monitoring-only design applies it in place, and the evaluation counts the consumers it broke.

## 6. One serving path for REST, MCP and A2A

`ProductService` picks the masked or full view from the grant, validates columns and filters, and runs the query.
The ports only translate. An agent can never see more than a person with the same grant, and the evaluation checks
that the three ports return identical rows.

## 7. Cost: tag, role, user, warehouse, in that order; idle to whoever kept the warehouse up

Query tags alone miss notebooks, BI tools and anyone who forgets. Roles are created per product and team by
Terraform, so they have owners; users map to teams through the identity provider's groups; a team's own warehouse
is the last resort. Idle time is real spend: it goes to the owner of a team warehouse, or pro rata on a shared one.
Anomalies are judged per team against its own last week, the next morning, because ACCOUNT_USAGE lags by hours.

## 8. Incidents are routed by lineage

A derived product goes stale when its input does. Paging its owner sends a team to fix something it cannot fix.
The SLA monitor runs upstream-first and links a downstream incident to the open upstream one; only the root pages.

## 9. Found by the simulation: `null` is not `false` in Rego

The first policies wrote `not p.owner.team` and `c.unit` as presence tests. Descriptors arrive from pydantic with
`null` for unset fields, and in Rego `null` is a defined value, so a column with `unit: null` passed the
agent-readiness rule. The world's onboarding mistakes went live in the platform design, which is how it showed up.
The rules now test values (`non_empty`), and `policies/policy_test.rego` has the case.

## 10. The world draws one random stream per event

The first harness used one random generator. A platform that reacted differently (a re-delivery, an alert) shifted
every later draw, so the two designs saw different defects and could not be compared. `World._r(*key)` gives every
delivery, change, request and usage hour its own stream.

## 11. Found by the simulation: a gate can lock itself

A product onboarded mid-run whose very first delivery arrived in thousands. With no history the scale check had
nothing to compare against, so the day was published; from then on every normal delivery looked like a 1000x drop
against that one day, was held, re-delivered, held again: 2,652 clean deliveries held in the first full run. The scale
check now needs `scale_min_history_days` of published history before it judges, and
`tests/test_pipeline.py::test_no_scale_check_without_enough_history` keeps it that way. The first shifted day of a
new product is the price, and it is one of the misses the README reports.
