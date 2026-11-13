import { GateLog } from "../features/gate/GateLog";
import { Ledger } from "../features/ledger/Ledger";
import { Products } from "../features/products/Products";
import { Spend } from "../features/spend/Spend";
import { useConsole } from "../shared/api";
import { ErrorState, Section, Skeleton } from "../shared/ui";

export function App() {
  const { data, isPending, error, refetch } = useConsole();
  return (
    <main className="page">
      <header className="masthead">
        <h1>Every delivery is checked before anyone reads it</h1>
        <p>
          A data platform on Snowflake where one descriptor per product decides its tables, roles, budget, contract
          checks and output ports. Below: a simulated company's months on it, against the same months with
          monitoring only.
        </p>
      </header>
      {isPending ? <Skeleton rows={6} /> : error ? <ErrorState message={error.message} onRetry={() => refetch()} /> : (
        <>
          <Ledger rows={data.ledger} />
          <Section id="products" title="Products and their marks"
            lede="Contract version, the ports each product offers, and how many deliveries the gate held. Open a row for its inputs and agent access.">
            <Products products={data.products} />
          </Section>
          <Section id="gate" title="Held at the gate"
            lede="The newest deliveries kept in staging, and contract changes sent back with the fix the policy names.">
            <GateLog held={data.gate_log} rejected={data.rejected_changes} />
          </Section>
          <Section id="spend" title="Who spent the credits"
            lede="Snowflake's own usage views, attributed by query tag, then role, then the user's team, then the warehouse owner. Idle time goes to whoever kept the warehouse running.">
            <Spend cost={data.cost} alerts={data.alerts} />
          </Section>
          <p className="muted">Generated from {data.generated_from}.</p>
        </>
      )}
    </main>
  );
}
