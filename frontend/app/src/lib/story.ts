/** The hero story's fixed actors (design-system README: "the hero story"). */
export const STORY = { ifsc: "HDFC0001234", account: "ACC-999", amountMinor: 500_000 } as const;

/** One row of the policy walk-through. `expect` is the decision the
 * PolicyConfig defaults (block >= 85 at confidence >= 0.5, step-up >= 50,
 * step-up above ₹1,00,000, fail closed on outage) call for; the page shows it
 * next to the decision BankBot actually returns, so a mismatch would be seen. */
export interface PolicyCase {
  id: string;
  label: string;
  account: string;
  amountMinor: number;
  flag?: { riskScore: number; confidence: number };
  forge?: boolean;
  outage?: boolean;
  expect: "allow" | "step_up" | "block";
  why: string;
}

export const CASES: readonly PolicyCase[] = [
  { id: "clean", label: "Unflagged receiver", account: "ACC-1001", amountMinor: 250_000, expect: "allow", why: "Nothing on the network, small amount" },
  { id: "large", label: "Unflagged, large amount", account: "ACC-1002", amountMinor: 15_000_000, expect: "step_up", why: "Above ₹1,00,000 needs a second factor" },
  { id: "high", label: "Flagged 92", account: "ACC-1003", amountMinor: 500_000, flag: { riskScore: 92, confidence: 0.94 }, expect: "block", why: "Score ≥ 85 and confident" },
  { id: "edge85", label: "Flagged exactly 85", account: "ACC-1004", amountMinor: 500_000, flag: { riskScore: 85, confidence: 0.9 }, expect: "block", why: "Threshold is inclusive" },
  { id: "edge84", label: "Flagged 84", account: "ACC-1005", amountMinor: 500_000, flag: { riskScore: 84, confidence: 0.9 }, expect: "step_up", why: "One below block: verify instead" },
  { id: "mid", label: "Flagged 70", account: "ACC-1006", amountMinor: 500_000, flag: { riskScore: 70, confidence: 0.9 }, expect: "step_up", why: "50–84 asks for verification" },
  { id: "low", label: "Flagged 40", account: "ACC-1007", amountMinor: 500_000, flag: { riskScore: 40, confidence: 0.9 }, expect: "allow", why: "Below 50 is not acted on" },
  { id: "unsure", label: "Flagged 92, unsure", account: "ACC-1008", amountMinor: 500_000, flag: { riskScore: 92, confidence: 0.3 }, expect: "step_up", why: "Confidence < 0.5 cannot freeze payments" },
  { id: "forged", label: "Flagged 92, then forged retraction", account: "ACC-1009", amountMinor: 500_000, flag: { riskScore: 92, confidence: 0.94 }, forge: true, expect: "block", why: "Forgery rejected; the real flag stands" },
  { id: "outage", label: "Bus outage", account: "ACC-1001", amountMinor: 250_000, outage: true, expect: "step_up", why: "Same transfer as row 1, now fail-closed" },
];