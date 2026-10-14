/** ARIS components — window.Aris. React 18, classic script, no build step. */
import type { ReactNode, ButtonHTMLAttributes, CSSProperties } from "react";

export type IconName = "check" | "shield" | "shieldCheck" | "shieldX" | "alert" | "lock" | "key" | "arrowRight" | "info";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = sky fill with navy text; secondary = glass + control border; ghost = text only. Default "primary". */
  variant?: "primary" | "secondary" | "ghost";
  size?: "md" | "sm";
  /** Leading icon. */
  icon?: IconName;
  /** Trailing icon, e.g. "arrowRight" for a forward CTA. */
  iconEnd?: IconName;
  children: ReactNode;
}

export interface StatusPillProps {
  /** BankBot decision. Each carries its own icon and word. */
  decision: "allow" | "stepup" | "block";
  size?: "md" | "lg";
  /** Overrides the default word ("Allow", "Step-up", "Block"), e.g. "Blocked". */
  children?: ReactNode;
}

export interface HashChipProps {
  /** Full hex string (risk_id, signature, key fingerprint). */
  value: string;
  /** Optional muted prefix, e.g. "risk_id". */
  label?: string;
  /** Characters kept at each end when collapsed. Default 4 / 4. */
  head?: number;
  tail?: number;
  defaultExpanded?: boolean;
}

export interface GlassCardProps {
  icon?: IconName;
  eyebrow?: string;
  title?: string;
  body?: string;
  /** Rise 4px with shadow-lift on hover. Default true. */
  lift?: boolean;
  as?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

export interface ToggleChipProps {
  label: string;
  /** Small muted helper after the label. */
  hint?: string;
  /** Controlled state; omit to let the chip manage itself. */
  pressed?: boolean;
  defaultPressed?: boolean;
  onChange?: (pressed: boolean) => void;
}

export interface MetricTileProps {
  /** The measured number, exactly as reported (e.g. 0.990, 3517). Decimals are inferred from it. */
  value: number | string;
  label?: string;
  unit?: string;
  prefix?: ReactNode;
  /** One line of context, e.g. "vs 0.572 mean local". */
  context?: ReactNode;
  decimals?: number;
  /** Count up once when scrolled into view. Default true; off under reduced motion. */
  animate?: boolean;
  duration?: number;
}

export interface SecurityCardProps {
  /** Hardened area, e.g. "Key rotation". */
  title: string;
  /** Front-face one-liner. */
  summary?: string;
  /** Back face: what was done. */
  done: string;
  /** Back face: stated limitation; also shows an amber note chip on the front. */
  limitation?: string;
  /** Front chip text when a limitation exists. Default "Note". */
  noteLabel?: string;
}

export interface TerminalCardProps {
  /** A real command that was run. */
  command: string;
  /** Its real result line, e.g. "357 passed". */
  result?: string;
  title?: string;
  speed?: number;
  animate?: boolean;
}

export interface RocSeries {
  label?: string;
  /** [fpr, tpr] pairs from 0..1, starting [0,0] and ending [1,1]. */
  points: [number, number][];
  /** The global / federated model: bold sky line, drawn last. */
  emphasis?: boolean;
  /** Colour token name, e.g. "chart-teal". */
  color?: string;
  /** Nudge the direct label's y (0..1) when labels collide. */
  labelY?: number;
}

export interface RocChartProps {
  series: RocSeries[];
  title?: string;
  ariaLabel?: string;
  width?: number;
  height?: number;
  animate?: boolean;
}

export interface SkyBackgroundProps {
  /** "auto" runs the 75s dawn → midday → dusk loop; or pin one phase. */
  phase?: "auto" | "dawn" | "midday" | "dusk";
  /** Draw the faint bank-network mesh with a travelling signal. */
  mesh?: boolean;
  minHeight?: number | string;
  style?: CSSProperties;
  children?: ReactNode;
}
