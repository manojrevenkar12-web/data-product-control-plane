import { AnimatePresence, motion as m } from "framer-motion";
import { useMemo, useState } from "react";
import { motion } from "../../app/theme";
import type { Product } from "../../shared/types";
import { Empty } from "../../shared/ui";
import "./products.css";

const PORTS = ["api", "mcp", "a2a", "kafka"] as const;

function Hallmark({ label, title, tone }: { label: string; title: string; tone: "pass" | "held" | "off" }) {
  return <span className={`hallmark ${tone}`} title={title} aria-label={title}>{label}</span>;
}

/** Every product with its marks: contract version, ports, agent readiness and how often the gate held it. */
export function Products({ products }: { products: Product[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const [domain, setDomain] = useState("all");
  const domains = useMemo(() => ["all", ...new Set(products.map((p) => p.domain))], [products]);
  const shown = products.filter((p) => domain === "all" || p.domain === domain);
  if (!products.length) return <Empty>No data products yet. Add a descriptor under products/ and open a pull request.</Empty>;
  return (
    <div className="products">
      <div className="filters" role="group" aria-label="Domain">
        {domains.map((d) => (
          <button key={d} className={d === domain ? "chip on" : "chip"} aria-pressed={d === domain}
            onClick={() => setDomain(d)}>{d}</button>
        ))}
      </div>
      <ul className="product-list panel">
        {shown.map((p, i) => (
          <li key={p.id}>
            <button className="product-row" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>
              <span className="product-name">{p.id}<small>{p.team} · {p.classification}</small></span>
              <m.span className="marks" initial={{ scale: 1.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                transition={{ delay: i * motion.stampStagger, duration: motion.stampDuration, ease: motion.ease }}>
                <Hallmark label={`v${p.version.split(".").slice(0, 2).join(".")}`} title={`contract ${p.version}`} tone="pass" />
                {PORTS.map((port) => (
                  <Hallmark key={port} label={port} title={p.ports.includes(port) ? `${port} port` : `no ${port} port`}
                    tone={p.ports.includes(port) ? "pass" : "off"} />
                ))}
                <Hallmark label={String(p.quarantined)} title={`${p.quarantined} deliveries held at the gate`}
                  tone={p.quarantined ? "held" : "off"} />
              </m.span>
            </button>
            <AnimatePresence initial={false}>
              {open === p.id && (
                <m.div className="product-detail" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }} transition={{ duration: motion.expandDuration, ease: motion.ease }}>
                  <dl>
                    <dt>Newest published data</dt><dd>{p.latest ?? "nothing published yet"}</dd>
                    <dt>Built from</dt><dd>{p.inputs.length ? p.inputs.join(", ") : "a source system"}</dd>
                    <dt>Agents</dt><dd>{p.agent_ready ? "listed over MCP and A2A" : "not offered to agents"}</dd>
                  </dl>
                </m.div>
              )}
            </AnimatePresence>
          </li>
        ))}
      </ul>
    </div>
  );
}
