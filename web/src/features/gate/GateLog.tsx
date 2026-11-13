import type { Held, Rejected } from "../../shared/types";
import { Empty } from "../../shared/ui";
import "./gate.css";

const CHECK: Record<string, string> = {
  required_null: "missing required values", duplicate_key: "duplicate keys", out_of_range: "values out of range",
  undeclared_pii: "personal data in a column not declared as such", scale_shift: "a column changed scale",
  empty: "an empty delivery",
};

/** What the gate stopped: deliveries held in staging, and contract changes sent back with the fix. */
export function GateLog({ held, rejected }: { held: Held[]; rejected: Rejected[] }) {
  return (
    <div className="gate">
      <div>
        <h3>Deliveries held</h3>
        {held.length ? (
          <ol className="gate-list panel">
            {[...held].reverse().slice(0, 12).map((h) => (
              <li key={`${h.product}-${h.at}`}>
                <span className="gate-title"><strong>{h.product}</strong>, data of {h.data_date}</span>
                <span>{h.findings.map((f) => `${CHECK[f.check] ?? f.check}${f.column ? ` (${f.column.replaceAll(",", ", ")})` : ""}`).join("; ")}</span>
              </li>
            ))}
          </ol>
        ) : <Empty>No delivery was held. Every one passed its contract.</Empty>}
      </div>
      <div>
        <h3>Changes sent back</h3>
        {rejected.length ? (
          <ol className="gate-list panel">
            {[...rejected].reverse().slice(0, 8).map((r) => (
              <li key={`${r.product}-${r.at}`}>
                <span className="gate-title"><strong>{r.product}</strong>{r.to ? ` to version ${r.to}` : ""}</span>
                {r.violations.map((v) => (<span key={v.rule}>{v.msg}. <em>Fix: {v.fix}</em></span>))}
              </li>
            ))}
          </ol>
        ) : <Empty>No change was sent back.</Empty>}
      </div>
    </div>
  );
}
