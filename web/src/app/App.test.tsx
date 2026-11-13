import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Console } from "../shared/types";

const sample: Console = {
  world: "dev", generated_from: "reports/dev.json",
  ledger: [{ label: "Bad deliveries that reached consumers", platform: "0", baseline: "55", note: "of 55 planted" }],
  products: [{ id: "survey.panel", domain: "survey", team: "survey-data", classification: "confidential",
    ports: ["api"], version: "1.0.0", inputs: [], latest: "2026-04-01", quarantined: 2, agent_ready: false }],
  gate_log: [{ at: "2026-03-01T01:00:00", product: "survey.panel", data_date: "2026-02-28", findings: [{ check: "scale_shift", column: "spend" }] }],
  rejected_changes: [], cost: [{ team: "survey-data", tag: 10, role: 2, user: 1, warehouse: 0, idle: 1, none: 0 }], alerts: [],
};

function renderApp() {
  return render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("console", () => {
  it("shows a skeleton, then the ledger and products", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(sample)));
    renderApp();
    expect(screen.getByLabelText("Loading")).toBeTruthy();
    expect(await screen.findByText("Bad deliveries that reached consumers")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /survey.panel/ }));
    expect(await screen.findByText("not offered to agents")).toBeTruthy();
    expect(screen.getByText("No change was sent back.")).toBeTruthy();
  });

  it("offers a retry when the data does not load", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status: 404 }));
    renderApp();
    expect(await screen.findByRole("alert")).toBeTruthy();
    f.mockResolvedValueOnce(new Response(JSON.stringify(sample)));
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(screen.getAllByText("survey.panel").length).toBeGreaterThan(0));
  });

  it("says what to do when there are no products", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ...sample, products: [], cost: [] })));
    renderApp();
    expect(await screen.findByText(/No data products yet/)).toBeTruthy();
    expect(screen.getByText(/No usage attributed yet/)).toBeTruthy();
  });
});
