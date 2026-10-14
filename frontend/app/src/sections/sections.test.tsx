import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixture } from "../test/fixture";
import { Federated } from "./Federated";
import { Metrics } from "./Metrics";
import { Proof } from "./Proof";
import { SECURITY_AREAS } from "../lib/securityAreas";
import { Security } from "./Security";

describe("Metrics", () => {
  it("shows exactly the measured values, at reported precision", () => {
    render(<Metrics data={fixture} />);
    const table = screen.getByTestId("bank-table");
    const global = within(table).getByText("Global FedAvg").closest("tr")!;
    expect(within(global).getAllByText("0.987")).toHaveLength(1);
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 5 + 2);
    expect(screen.getByTestId("honesty-note").textContent).toContain("99.0%");
  });

  it("switches dataset from the tab list, by click and by arrow key", () => {
    render(<Metrics data={fixture} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.click(tabs[1]);
    expect(screen.getAllByRole("tab")[1]).toHaveAttribute("aria-selected", "true");
    const global = within(screen.getByTestId("bank-table")).getByText("Global FedAvg").closest("tr")!;
    expect(global.textContent).toContain("0.655");
    fireEvent.keyDown(screen.getAllByRole("tab")[1], { key: "ArrowRight" });
    expect(screen.getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true");
  });
});

describe("Federated", () => {
  it("reveals each protection's measured effect only when switched on", () => {
    render(<Federated data={fixture} />);
    expect(screen.queryByTestId("privacy-table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /DP-SGD/ }));
    expect(screen.getByTestId("privacy-table").textContent).toContain("118.29");
    fireEvent.click(screen.getByRole("button", { name: /Krum/ }));
    expect(screen.getByTestId("evil-bank").textContent).toContain("Update rejected");
    expect(screen.queryByTestId("combo-note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Secure aggregation/ }));
    expect(screen.getByTestId("secagg-panel").textContent).toContain("6.0e-8");
    expect(screen.getByTestId("combo-note")).toBeInTheDocument();
  });
});

describe("Security", () => {
  it("renders all eight hardened areas, each limitation stated", () => {
    render(<Security />);
    const cards = within(screen.getByTestId("security-grid")).getAllByRole("button");
    expect(cards).toHaveLength(8);
    const withNote = SECURITY_AREAS.filter((a) => "limitation" in a).length;
    expect(screen.getAllByText("Note")).toHaveLength(withNote);
    expect(screen.getByText(/SoftHSM2\. Real HSM hardware not available\./)).toBeInTheDocument();
  });

  it("flips a card from the keyboard", () => {
    render(<Security />);
    const card = within(screen.getByTestId("security-grid")).getAllByRole("button")[0];
    fireEvent.keyDown(card, { key: "Enter" });
    expect(card).toHaveAttribute("aria-pressed", "true");
  });
});

describe("Proof", () => {
  it("only shows badges for checks that were actually measured as passing", () => {
    render(<Proof data={fixture} />);
    expect(screen.getByTestId("badge-tests").textContent).toContain("926 tests passed");
    expect(screen.getByText("ruff: clean")).toBeInTheDocument();
    expect(screen.queryByText("mypy strict: clean")).toBeNull(); // false in fixture
    expect(screen.getByText(/Kafka was not running for this export/)).toBeInTheDocument();
  });

  it("turns the test badge to a failure state when tests failed", () => {
    render(<Proof data={{ ...fixture, tests: { ...fixture.tests!, failed: 2, exit_code: 1 } }} />);
    expect(screen.getByTestId("badge-tests").className).toContain("badge-fail");
  });
});
