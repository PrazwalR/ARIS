# Button

Buttons start actions and navigate between story sections; primary is sky blue with navy text, never white on blue.

**Consumer provides:** `children` (a verb-led label in sentence case), optional `variant`, `size`, `icon` / `iconEnd`, and any native button props (`onClick`, `disabled`, `aria-*`).

- One `primary` per view — the hero pairs "See how it works" (`primary`, `iconEnd="arrowRight"`) with "View the metrics" (`secondary`).
- `secondary` over the sky background: its `glass` fill keeps the label legible at every phase of the day cycle.
- `ghost` for tertiary links inside cards and the footer (Security model, Metrics, Review runbook).
- Hover lightens the fill to `sky-hover` and runs a single shimmer sweep; press nudges 1px down. No bounce, no glow.
- Do not put white text on `sky-500` or `sky-600` — it fails AA. The component already sets `on-sky`.
- Focus shows the 2px `focus` ring at 2px offset.
