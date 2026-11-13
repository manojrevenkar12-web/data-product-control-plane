# Who may read which product through which port. Personal data is masked unless the reader is in the
# owning domain; masking itself is applied by the serving views, the policy decides who gets which view.
package dpcp

import rego.v1

deny_access contains v if {
	some r in input.access
	not r.port in r.product.ports
	v := {"id": r.id, "rule": "port", "msg": sprintf("%s does not offer the %s port", [r.product.id, r.port])}
}

deny_access contains v if {
	some r in input.access
	r.principal.kind == "agent"
	levels[r.product.classification] > levels[data.limits.agent_max_classification]
	v := {"id": r.id, "rule": "agent", "msg": sprintf("agents may not read %s data", [r.product.classification])}
}

deny_access contains v if {
	some r in input.access
	r.product.classification == "restricted"
	r.principal.domain != r.product.domain
	v := {"id": r.id, "rule": "restricted", "msg": "restricted products are read inside their domain only"}
}

deny_access contains v if {
	some r in input.access
	r.product.classification == "confidential"
	r.principal.domain != r.product.domain
	count(r.purpose) < data.limits.min_purpose_chars
	v := {"id": r.id, "rule": "purpose", "msg": "confidential data across domains needs a stated purpose"}
}

unmasked contains r.id if {
	some r in input.access
	r.principal.domain == r.product.domain
}
