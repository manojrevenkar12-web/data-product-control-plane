# Putting a data product on the platform

This is for a domain team. You should not need the platform team for any of it; if you do, that is a bug in the
platform or in this page, and we want to hear about it.

## 1. Start from a reference product

| Your source | Copy |
|---|---|
| Files your system drops on S3 | `products/survey/consumer_panel.yaml` |
| Events your service writes to Kafka | `products/commerce/shop_orders.yaml` |
| Built from other products | `products/market/online_spend_index.yaml` (note `inputs`) |

## 2. Fill in the descriptor

* `id` is `<domain>.<name>` in lower snake case. It never changes.
* `owner.team` and `owner.email`: incidents go there.
* `classification`: `public`, `internal`, `confidential` or `restricted`. A column marked `pii: true` needs
  `confidential` or above; it is masked for every reader outside your domain.
* `sla.freshness_hours`: how old your newest data may be before your consumers are told.
* `budget.credits_per_month`: becomes your warehouse's resource monitor; your spend is reported against it every morning.
* `ports`: `api`, `kafka`, and `mcp`/`a2a` if agents may read it. Agent ports need a description of at least 40
  characters and a unit and description on every numeric column, and are refused on confidential data.
* `contract`: the key, and every column with its type, `required`, `min`/`max`, `unit`.

## 3. Open the pull request

CI runs `dpcp check products/ --base main`. Each problem comes with the field and the fix, for example:

```
market.new_index: contract.columns.avg_monthly_spend: numeric column avg_monthly_spend has no unit or description
    fix: agents misread numbers without units: add unit and description
```

On merge, the platform creates the tables and views, Terraform creates the roles, grants, warehouse, resource
monitor and Kafka topic, and the catalogue gets the table, its PII tags, the data product and its lineage.

## 4. Deliver

Drop files under your `source.location` or produce to your topic. Each delivery is loaded once (by path and by
content hash), checked, and published only if it passes. If it does not, you get an incident with the findings and
your consumers keep the last good data until you send a corrected delivery for the same day. Rows that miss a
required value or fall out of range are held on their own when they are at most 10% of the delivery; the rest is
published.

## 5. Change the contract

Adding a column is a minor version. Removing, renaming, retyping or changing the unit of a column is breaking: it
needs a major version, and it is refused while anyone reads that column. The message names who. Either keep the
column and add the new one next to it, or publish the new shape as the next major version (a new table next to the
old one) and move your consumers first.
