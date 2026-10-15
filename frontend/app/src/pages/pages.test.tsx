import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixture } from "../test/fixture";
import { Evidence } from "./Evidence";
import { Model } from "./Model";
import { Overview } from "./Overview";
import { Security } from "./Security";
import { SECURITY_AREAS } from "../lib/securityAreas";

describe("Overview", () => {
  it("shows headline figures from the measured data", () => {
    render(<Overview data={fixture} />);
    const figs = screen.getByTestId("headline-figures");
    expect(figs.textContent).toContain("0.987");
    expect(figs.textContent).toContain("926");
  });

  it("renders without data rather than inventing figures", () => {
    render(<Overview data={null} />);
    expect(screen.queryByTestId("headline-figures")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
});

describe("Model", () => {
  it("shows the measured values at reported precision", () => {
    render(<Model data={fixture} />);
    const table = screen.getByTestId("bank-table");
    const federated = within(table).getByText("Federated").closest("tr")!;
    expect(federated.textContent).toContain("0.987");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 5 + 2);
    expect(screen.getByTestId("honesty-note").textContent).toContain("99.0%");
    expect(screen.getByTestId("robustness").textContent).toContain("0.588");
    expect(screen.getByTestId("privacy-table").textContent).toContain("118.29");
  });

  it("switches dataset by click and by arrow key", () => {
    render(<Model data={fixture} />);
    fireEvent.click(screen.getAllByRole("tab")[1]);
    expect(screen.getAllByRole("tab")[1]).toHaveAttribute("aria-selected", "true");
    const federated = within(screen.getByTestId("bank-table")).getByText("Federated").closest("tr")!;
    expect(federated.textContent).toContain("0.655");
    fireEvent.keyDown(screen.getAllByRole("tab")[1], { key: "ArrowRight" });
    expect(screen.getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true");
  });
});

describe("Security", () => {
  it("lists all eight controls with every recorded limitation", () => {
    render(<Security />);
    const rows = within(screen.getByTestId("controls")).getAllByRole("row");
    expect(rows).toHaveLength(1 + 8);
    const withLimits = SECURITY_AREAS.filter((a) => a.limitation).length;
    expect(screen.getAllByText("None recorded")).toHaveLength(8 - withLimits);
    expect(screen.getByText(/Real HSM hardware not available/)).toBeInTheDocument();
  });
});

describe("Evidence", () => {
  it("passes only the checks that were measured as passing", () => {
    render(<Evidence data={fixture} />);
    const checks = screen.getByTestId("checks");
    expect(screen.getByTestId("badge-tests").textContent).toContain("926 passed");
    const lint = within(checks).getByText("Lint").closest("tr")!;
    const types = within(checks).getByText("Type check").closest("tr")!;
    expect(lint.textContent).toContain("Pass");
    expect(types.textContent).toContain("Fail"); // mypy_strict is false in the fixture
    expect(screen.getByText(/Not running during this export/)).toBeInTheDocument();
  });

  it("marks the test suite as failing when tests failed", () => {
    render(<Evidence data={{ ...fixture, tests: { ...fixture.tests!, failed: 2, exit_code: 1 } }} />);
    const row = within(screen.getByTestId("checks")).getByText("Test suite").closest("tr")!;
    expect(row.textContent).toContain("Fail");
  });
});
