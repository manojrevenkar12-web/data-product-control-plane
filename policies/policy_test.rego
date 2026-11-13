package dpcp_test

import rego.v1

import data.dpcp

limits := {"agent_max_classification": "internal", "min_description_chars": 10, "min_purpose_chars": 5, "max_credits_per_month": {"XSMALL": 200}}

good := {
	"id": "survey.panel", "domain": "survey", "description": "one row per respondent and wave",
	"owner": {"team": "survey", "email": "survey@example.com"}, "classification": "internal",
	"sla": {"freshness_hours": 26}, "budget": {"credits_per_month": 50, "warehouse_size": "XSMALL"},
	"ports": ["api", "mcp", "kafka"],
	"contract": {"key": ["id"], "columns": [
		{"name": "id", "type": "string", "required": true, "pii": false},
		{"name": "value", "type": "number", "required": false, "pii": false, "unit": "EUR", "description": "spend"},
	]},
}

test_good_product_passes if {
	count(dpcp.deny_product) == 0 with input as {"products": [good]} with data.limits as limits
}

test_pii_needs_confidential if {
	cols := array.concat(good.contract.columns, [{"name": "email", "type": "string", "pii": true}])
	p := object.union(good, {"contract": {"key": ["id"], "columns": cols}, "ports": ["api"]})
	rules := {v.rule | some v in dpcp.deny_product} with input as {"products": [p]} with data.limits as limits
	rules == {"pii"}
}

test_agent_port_needs_units if {
	cols := [good.contract.columns[0], {"name": "value", "type": "number", "pii": false}]
	p := object.union(good, {"contract": {"key": ["id"], "columns": cols}})
	rules := {v.rule | some v in dpcp.deny_product} with input as {"products": [p]} with data.limits as limits
	rules == {"agent_ready"}
}

test_breaking_change_with_consumers_denied if {
	ch := {"id": "survey.panel", "from": [1, 2, 0], "to": [2, 0, 0], "changes": [], "breaking": [{"kind": "removed_column", "column": "value", "consumers": ["finance.revenue"]}]}
	rules := {v.rule | some v in dpcp.deny_change} with input as {"changes": [ch]} with data.limits as limits
	rules == {"consumers"}
}

test_breaking_change_without_consumers_needs_major if {
	ch := {"id": "survey.panel", "from": [1, 2, 0], "to": [1, 3, 0], "changes": [], "breaking": [{"kind": "removed_column", "column": "value", "consumers": []}]}
	rules := {v.rule | some v in dpcp.deny_change} with input as {"changes": [ch]} with data.limits as limits
	rules == {"major"}
}

test_version_must_go_up if {
	ch := {"id": "survey.panel", "from": [1, 2, 0], "to": [1, 2, 0], "changes": [{"kind": "added_column", "column": "x"}], "breaking": []}
	rules := {v.rule | some v in dpcp.deny_change} with input as {"changes": [ch]} with data.limits as limits
	rules == {"version"}
}

test_agent_cannot_read_confidential if {
	r := {"id": "r1", "principal": {"kind": "agent", "domain": "x"}, "product": {"id": "p", "domain": "y", "classification": "confidential", "ports": ["mcp"]}, "port": "mcp", "purpose": "long enough purpose"}
	rules := {v.rule | some v in dpcp.deny_access} with input as {"access": [r]} with data.limits as limits
	rules == {"agent"}
}

test_same_domain_unmasked if {
	r := {"id": "r1", "principal": {"kind": "human", "domain": "y"}, "product": {"id": "p", "domain": "y", "classification": "confidential", "ports": ["api"]}, "port": "api", "purpose": ""}
	count(dpcp.deny_access) == 0 with input as {"access": [r]} with data.limits as limits
	dpcp.unmasked == {"r1"} with input as {"access": [r]} with data.limits as limits
}

test_null_unit_and_empty_team_are_violations if {
	cols := [good.contract.columns[0], {"name": "value", "type": "number", "pii": false, "unit": null, "description": "spend"}]
	p := object.union(good, {"contract": {"key": ["id"], "columns": cols}, "owner": {"team": "", "email": "a@b.c"}})
	rules := {v.rule | some v in dpcp.deny_product} with input as {"products": [p]} with data.limits as limits
	rules == {"agent_ready", "owner"}
}
