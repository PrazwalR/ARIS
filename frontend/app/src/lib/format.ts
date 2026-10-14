/** Formatting rules from the design system's content fundamentals: numbers at
 * the precision reported, Indian digit grouping for rupees. */

/** A measured value at fixed precision: 0.99 -> "0.990" when digits = 3. */
export function fixed(value: number, digits = 3): string {
  return value.toFixed(digits);
}

/** Paise to rupees with Indian grouping: 500000 -> "₹5,000", 12345678 -> "₹1,23,456.78". */
export function rupees(minor: number): string {
  const major = minor / 100;
  const hasPaise = minor % 100 !== 0;
  return (
    "₹" +
    major.toLocaleString("en-IN", {
      minimumFractionDigits: hasPaise ? 2 : 0,
      maximumFractionDigits: 2,
    })
  );
}

/** Rupee text typed by a user -> paise, or null if it is not a positive amount
 * with at most two decimal places. */
export function parseRupees(text: string): number | null {
  const cleaned = text.replace(/[₹,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole, frac = ""] = cleaned.split(".");
  const minor = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return minor > 0 && Number.isSafeInteger(minor) ? minor : null;
}

export function grouped(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

/** A tiny value in scientific form, the way the measurement reports it. */
export function scientific(value: number, digits = 1): string {
  return value.toExponential(digits);
}

/** IFSC as the API requires it: 4 letters, a zero, 6 alphanumerics. */
export function isValidIfsc(text: string): boolean {
  return /^[A-Z]{4}0[A-Z0-9]{6}$/.test(text.trim().toUpperCase());
}

export function shortTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
