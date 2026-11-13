import { motion as m } from "framer-motion";
import { motion } from "../../app/theme";
import type { Row } from "../../shared/types";
import "./ledger.css";

/** The two designs on the same simulated months, line by line. */
export function Ledger({ rows }: { rows: Row[] }) {
  return (
    <div className="ledger panel" role="table" aria-label="Platform against monitoring only">
      <div className="ledger-head" role="row">
        <span role="columnheader">Same world, same defects</span>
        <span role="columnheader">Gates in the pipeline</span>
        <span role="columnheader">Monitoring only</span>
      </div>
      {rows.map((r, i) => (
        <m.div key={r.label} className="ledger-row" role="row" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          transition={{ delay: i * motion.stampStagger, duration: motion.stampDuration, ease: motion.ease }}>
          <span role="cell" className="ledger-label">{r.label}<small>{r.note}</small></span>
          <span role="cell" className="ledger-value platform">{r.platform}</span>
          <span role="cell" className="ledger-value">{r.baseline}</span>
        </m.div>
      ))}
    </div>
  );
}
