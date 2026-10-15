import { describe, expect, it } from "vitest";
import { fixed, grouped, isValidIfsc, parseRupees, percent, rupees, scientific } from "./format";
import { pillDecision } from "./api";

describe("fixed", () => {
  it("keeps the reported precision, trailing zeros included", () => {
    expect(fixed(0.99)).toBe("0.990");
    expect(fixed(0.6552, 4)).toBe("0.6552");
  });
});

describe("rupees", () => {
  it("uses Indian digit grouping", () => {
    expect(rupees(500_000)).toBe("₹5,000");
    expect(rupees(12_345_678)).toBe("₹1,23,456.78");
  });
  it("shows paise only when present", () => {
    expect(rupees(250_050)).toBe("₹2,500.50");
    expect(rupees(100)).toBe("₹1");
  });
});

describe("parseRupees", () => {
  it.each([
    ["2,500", 250_000],
    ["₹5,000", 500_000],
    ["10.5", 1_050],
    ["0.01", 1],
    [" 1 000 ", 100_000],
  ])("%s -> %d paise", (text, minor) => expect(parseRupees(text)).toBe(minor));

  it.each(["", "0", "-5", "1.234", "abc", "1e3", "NaN", "Infinity", "1..2"])(
    "rejects %j",
    (text) => expect(parseRupees(text)).toBeNull(),
  );
});

describe("isValidIfsc", () => {
  it("accepts the API's IFSC shape, case-insensitively", () => {
    expect(isValidIfsc("HDFC0001234")).toBe(true);
    expect(isValidIfsc("hdfc0001234")).toBe(true);
  });
  it.each(["HDFC1001234", "HDF0001234", "HDFC00012345", "HDFC000123!", ""])("rejects %j", (s) =>
    expect(isValidIfsc(s)).toBe(false),
  );
});

describe("grouped", () => {
  it("groups counts internationally, not in lakhs", () => {
    expect(grouped(227_845)).toBe("227,845");
  });
});

describe("misc", () => {
  it("formats percentages and tiny errors", () => {
    expect(percent(0.0057, 2)).toBe("0.57%");
    expect(scientific(5.96e-8)).toBe("6.0e-8");
  });
  it("maps API decisions to design-system pill states", () => {
    expect(pillDecision("step_up")).toBe("stepup");
    expect(pillDecision("block")).toBe("block");
    expect(pillDecision("allow")).toBe("allow");
  });
});
