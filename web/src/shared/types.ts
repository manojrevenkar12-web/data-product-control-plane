export interface Product {
  id: string; domain: string; team: string; classification: string; ports: string[]; version: string;
  inputs: string[]; latest: string | null; quarantined: number; agent_ready: boolean;
}
export interface Finding { check: string; column: string | null }
export interface Held { at: string; product: string; data_date: string; findings: Finding[] }
export interface Rejected { at: string; product: string; to: string; violations: { rule: string; msg: string; fix: string }[] }
export interface Cost { team: string; tag: number; role: number; user: number; warehouse: number; idle: number; none: number }
export interface Alert { team: string; since: string; told_at: string; credits_per_hour: number }
export interface Row { label: string; platform: string; baseline: string; note: string }
export interface Console {
  world: string; generated_from: string; ledger: Row[]; products: Product[]; gate_log: Held[];
  rejected_changes: Rejected[]; cost: Cost[]; alerts: Alert[];
}
