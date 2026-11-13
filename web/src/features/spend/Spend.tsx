import { methodLabel, methods } from "../../app/theme";
import type { Alert, Cost } from "../../shared/types";
import { Empty } from "../../shared/ui";
import "./spend.css";

/** Credits per team, by how the platform knew whose they were. */
export function Spend({ cost, alerts }: { cost: Cost[]; alerts: Alert[] }) {
  if (!cost.length) return <Empty>No usage attributed yet. The cost job runs each morning over the previous day.</Empty>;
  const total = (c: Cost) => methods.reduce((s, k) => s + c[k], 0);
  const max = Math.max(...cost.map(total));
  const rows = [...cost].sort((a, b) => total(b) - total(a));
  return (
    <div className="spend">
      <ul className="legend" aria-label="How credits were attributed">
        {methods.map((k) => <li key={k}><i className={`swatch ${k}`} />{methodLabel[k]}</li>)}
      </ul>
      <ul className="bars panel">
        {rows.map((c) => (
          <li key={c.team}>
            <span className="team">{c.team === "(unattributed)" ? "nobody known" : c.team}</span>
            <span className="bar" role="img" aria-label={`${c.team}: ${total(c).toFixed(0)} credits`}>
              {methods.map((k) => c[k] > 0 && (
                <i key={k} className={`swatch ${k}`} style={{ width: `${(100 * c[k]) / max}%` }} title={`${methodLabel[k]}: ${c[k]} credits`} />
              ))}
            </span>
            <span className="credits">{total(c).toFixed(0)}</span>
          </li>
        ))}
      </ul>
      {alerts.length > 0 && (
        <ul className="alerts">
          {alerts.map((a) => (
            <li key={`${a.team}-${a.since}`}>
              <strong>{a.team}</strong> spent {a.credits_per_hour} credits an hour from {a.since.replace("T", " ").slice(0, 16)};
              the team was told at {a.told_at.replace("T", " ").slice(0, 16)}.
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
