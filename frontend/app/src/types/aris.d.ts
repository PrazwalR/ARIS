// The design system ships prop interfaces (components/index.d.ts) but no
// export declarations for aris.esm.js; this binds the two together.
declare module "@aris/components/aris.esm.js" {
  import type { ButtonHTMLAttributes, FC } from "react";
  import type {
    ButtonProps,
    GlassCardProps,
    HashChipProps,
    IconName,
    MetricTileProps,
    RocChartProps,
    SecurityCardProps,
    SkyBackgroundProps,
    StatusPillProps,
    TerminalCardProps,
    ToggleChipProps,
  } from "@aris/components/index";

  // index.d.ts extends ButtonHTMLAttributes, but its own `react` import
  // cannot resolve from outside this app, so restate the native attributes.
  export const Button: FC<ButtonProps & ButtonHTMLAttributes<HTMLButtonElement>>;
  export const StatusPill: FC<StatusPillProps & { className?: string }>;
  export const HashChip: FC<HashChipProps & { className?: string }>;
  export const GlassCard: FC<GlassCardProps & { className?: string }>;
  export const ToggleChip: FC<ToggleChipProps & { className?: string }>;
  export const MetricTile: FC<MetricTileProps & { className?: string }>;
  export const SecurityCard: FC<SecurityCardProps & { className?: string }>;
  export const TerminalCard: FC<TerminalCardProps & { className?: string }>;
  export const RocChart: FC<RocChartProps & { className?: string }>;
  export const SkyBackground: FC<SkyBackgroundProps & { className?: string }>;
  export const Icon: FC<{ name: IconName }>;
}
