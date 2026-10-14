# SkyBackground

The sky background is ARIS's living backdrop: a slow dawn → midday → dusk gradient, blurred drifting clouds, and in network sections a faint mesh of bank nodes with a travelling signal.

**Consumer provides:** `children` (the section content), optional `phase` (`"auto"` 75s loop, or pin `"dawn" | "midday" | "dusk"` — e.g. derived from the visitor's local hour), `mesh` for network sections, `minHeight`.

- Stops: `sky-dawn-top`, `sky-dawn-horizon`, `sky-midday`, `sky-dusk`; clouds `cloud` + `blur-cloud`; mesh `mesh-line`, signal `sky-500`.
- Text never sits directly on the sky: wrap copy in `.ar-scrim` (uses `scrim`) or a `GlassCard`.
- Everything stays low-contrast and slow — the background explains "a network of banks", it never decorates.
- `prefers-reduced-motion` freezes the gradient, clouds and signal.
- No purple, no neon, no glow, no AI-brain imagery.
