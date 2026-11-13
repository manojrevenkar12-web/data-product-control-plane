# Policies every data product descriptor must meet, at onboarding and on every change.
# A violation carries the field it is about and the fix, so a team can correct it without asking anyone.
package dpcp

import rego.v1

levels := {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

agent_ports := {"mcp", "a2a"}

numeric := {"integer", "number"}

deny_product contains v if {
	some p in input.products
	not non_empty(p.owner.team)
	v := {"id": p.id, "rule": "owner", "field": "owner.team", "msg": "no owning team", "fix": "set owner.team to the team that answers incidents"}
}

deny_product contains v if {
	some p in input.products
	not contains(p.owner.email, "@")
	v := {"id": p.id, "rule": "owner", "field": "owner.email", "msg": "no team e-mail for incidents", "fix": "set owner.email to the team's shared address"}
}

deny_product contains v if {
	some p in input.products
	not levels[p.classification]
	v := {"id": p.id, "rule": "classification", "field": "classification", "msg": sprintf("unknown classification %q", [p.classification]), "fix": "use public, internal, confidential or restricted"}
}

deny_product contains v if {
	some p in input.products
	p.sla.freshness_hours == null
	v := {"id": p.id, "rule": "sla", "field": "sla.freshness_hours", "msg": "no freshness SLA", "fix": "set sla.freshness_hours; consumers are alerted against it"}
}

deny_product contains v if {
	some p in input.products
	p.budget.credits_per_month == null
	v := {"id": p.id, "rule": "budget", "field": "budget.credits_per_month", "msg": "no monthly credit budget", "fix": "set budget.credits_per_month; it becomes the warehouse's resource monitor"}
}

deny_product contains v if {
	some p in input.products
	cap := data.limits.max_credits_per_month[p.budget.warehouse_size]
	p.budget.credits_per_month > cap
	v := {"id": p.id, "rule": "budget", "field": "budget.credits_per_month", "msg": sprintf("%v credits a month is above the %v cap for %s", [p.budget.credits_per_month, cap, p.budget.warehouse_size]), "fix": "lower the budget or ask the platform team for a larger cap"}
}

deny_product contains v if {
	some p in input.products
	some c in p.contract.columns
	c.pii
	levels[p.classification] < levels.confidential
	v := {"id": p.id, "rule": "pii", "field": sprintf("contract.columns.%s", [c.name]), "msg": sprintf("column %s is personal data but the product is %s", [c.name, p.classification]), "fix": "classify the product confidential (personal data is masked for other domains) or drop the column"}
}

deny_product contains v if {
	some p in input.products
	some port in p.ports
	agent_ports[port]
	levels[p.classification] > levels[data.limits.agent_max_classification]
	v := {"id": p.id, "rule": "agent_port", "field": "ports", "msg": sprintf("%s port on a %s product", [port, p.classification]), "fix": sprintf("agents may read products up to %s; remove %s or publish a derived product without the sensitive data", [data.limits.agent_max_classification, port])}
}

deny_product contains v if {
	some p in input.products
	some port in p.ports
	agent_ports[port]
	some c in p.contract.columns
	numeric[c.type]
	not agent_readable(c)
	v := {"id": p.id, "rule": "agent_ready", "field": sprintf("contract.columns.%s", [c.name]), "msg": sprintf("numeric column %s has no unit or description", [c.name]), "fix": "agents misread numbers without units: add unit and description"}
}

deny_product contains v if {
	some p in input.products
	some port in p.ports
	agent_ports[port]
	count(p.description) < data.limits.min_description_chars
	v := {"id": p.id, "rule": "agent_ready", "field": "description", "msg": "description too short for agents to choose this product", "fix": sprintf("describe what one row is and what it is for, at least %v characters", [data.limits.min_description_chars])}
}

deny_product contains v if {
	some p in input.products
	some k in p.contract.key
	not key_column_required(p, k)
	v := {"id": p.id, "rule": "key", "field": "contract.key", "msg": sprintf("key column %s is missing or not required", [k]), "fix": "declare every key column with required: true"}
}

deny_product contains v if {
	some p in input.products
	"kafka" in p.ports
	count(p.contract.key) == 0
	v := {"id": p.id, "rule": "kafka", "field": "contract.key", "msg": "kafka port without a key", "fix": "events are keyed by the contract key; declare one"}
}

# Descriptors arrive with nulls for unset fields, and null is not false in Rego: test values, not presence.
non_empty(s) if {
	is_string(s)
	count(s) > 0
}

agent_readable(c) if {
	non_empty(c.unit)
	non_empty(c.description)
}

key_column_required(p, k) if {
	some c in p.contract.columns
	c.name == k
	c.required
}
