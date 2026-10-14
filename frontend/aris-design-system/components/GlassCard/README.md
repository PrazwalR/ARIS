# GlassCard

The glass card is ARIS's default container: white at 70% over the sky, backdrop-blurred, with a pale-sky hairline.

**Consumer provides:** any of `icon`, `eyebrow`, `title`, `body`, or free `children`; `lift={false}` for static panels (charts, the architecture map).

- Fill `glass`, blur `blur-glass`, edge `border-sky`, radius `radius-lg`, padding `space-5`, shadow `shadow-sm`.
- Hover lifts 4px to `shadow-lift` over `--ar-dur-ui` with the ARIS ease-out.
- Use for explainer steps, feature tiles, metric groups. For chart panels over busy backgrounds set `lift={false}`.
- No coloured left-border accents, no emoji, no gradient fills.
- Where `backdrop-filter` is unsupported the card reads on `glass` alone; on very busy backgrounds switch to `surface-raised`.
