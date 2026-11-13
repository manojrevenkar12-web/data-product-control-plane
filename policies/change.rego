# Policies on a change to a published contract: versions only go up, and a breaking change never reaches
# a consumer that reads what it breaks.
package dpcp

import rego.v1

deny_change contains v if {
	some ch in input.changes
	not ch.to > ch.from
	v := {"id": ch.id, "rule": "version", "field": "contract.version", "msg": "the contract changed but its version did not go up", "fix": "bump the version: patch for docs, minor for added columns, major for breaking changes"}
}

deny_change contains v if {
	some ch in input.changes
	count(ch.breaking) > 0
	ch.to[0] == ch.from[0]
	v := {"id": ch.id, "rule": "major", "field": "contract.version", "msg": sprintf("breaking change (%s) in a minor version", [concat(", ", {b.kind | some b in ch.breaking})]), "fix": "bump the major version"}
}

deny_change contains v if {
	some ch in input.changes
	some b in ch.breaking
	count(b.consumers) > 0
	v := {"id": ch.id, "rule": "consumers", "field": sprintf("contract.columns.%s", [b.column]), "msg": sprintf("%s on %s breaks %s", [b.kind, b.column, concat(", ", b.consumers)]), "fix": "keep the column and add the new one next to it, or publish the new shape as a separate major version and move consumers first"}
}
