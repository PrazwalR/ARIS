ARIS is a privacy-preserving fraud-intelligence network for banks: banks exchange signed, pseudonymous risk signals and jointly train a fraud model with federated learning, while raw transactions, account numbers and labels stay inside each bank. This system dresses that story for academic reviewers, a project guide, and bank security and risk teams. It should read like a central-bank research portal, not a crypto or AI startup: trustworthy, precise, calm, institutional.

## Content fundamentals

- **Voice.** Plain, exact, unhurried. Say what the system does and what it does not. "No raw transactions, account numbers or labels ever leave a bank." Not "revolutionary AI-powered security".
- **Person.** "Banks", "the network", "BankBot" as subjects. Address the reader as "you" only in CTAs ("See how it works").
- **Casing.** Sentence case for headings and buttons. Uppercase only for eyebrows and status pills.
- **Numbers.** Real, measured values only, written to the precision reported (`0.990`, not `0.99`; `3,517 signals/s`). Indian digit grouping is fine for rupee amounts (₹5,000). Put comparison numbers in `mono`.
- **Honesty.** Always pair results with their caveat: "AUC and PR-AUC lead. Accuracy is secondary on imbalanced fraud data." State limitations on security cards ("SoftHSM2 — real HSM hardware not available"). Describe OPRF and HSM as implemented components, never as production deployment.
- **The hero story** is the canonical example, told in this order: Anu sends ₹5,000 to ACC-999 → Bank B has already flagged it → Bank A's BankBot checks the network before money moves → **Blocked**. Then a hostile bank forges a "score 0" retraction → rejected: signature invalid.
- **No emoji.** Not in copy, not in UI.

## Visual foundations

### Colour

Sky-blue led, on cloud-white and mist; slate navy for text, never black.

- Page ground `surface-000`; alternate bands `surface-100`; tinted bands `sky-100`.
- Body and headings in `ink-900`; secondary text `ink-600`; accent text and links `sky-700`.
- Brand fills `sky-500` with `on-sky` text. Never white text on `sky-500` or `sky-600` (fails AA).
- Decisions: allow → `allow-*`, step-up → `stepup-*`, block / fraud → `block-*`. The `-500` hue marks dots, gauge zones and chart cells; `-700` carries text and icons on the `-100` tint. Every state also carries an icon and a word.
- Charts in order: `chart-sky`, `chart-teal`, `chart-ocean`, `chart-sand`, `chart-coral`; grid `chart-grid`. The global model is always `chart-sky`, bold. Sky, teal and sand are under 3:1 as thin lines on light grounds, so lines are always direct-labelled.
- Dark theme is deep ocean navy (`surface-000` = `#0e2236`) with lifted sky accents — never black.
- Forbidden: purple or violet anywhere (including gradients and charts), neon, pure black-and-white minimalism, glowing "AI brain" imagery.

### The living sky

Every page sits on `SkyBackground`: a gradient through `sky-dawn-top` → `sky-midday` → `sky-dawn-horizon` (dawn peach) and `sky-dusk`, on a 75s ambient loop or pinned to the visitor's local hour; blurred `cloud` shapes drifting with gentle parallax; in network sections a faint `mesh-line` web of bank nodes where a line briefly brightens to `sky-500` as a signal travels. Keep it low-contrast. Any copy over the sky goes on a `GlassCard` or `.ar-scrim` (`scrim`) so AA holds at every moment of the cycle.

### Type

- Headings `display` family (Manrope): `display-xl` hero, `h1` sections, `h2` panels, `h3` cards.
- Body `sans` (IBM Plex Sans): `body-l` intros, `body` default, `body-s` cards and captions, `label` pills and eyebrows.
- Numbers, code, hashes `mono` (IBM Plex Mono): `mono`, `mono-s`.
- Metrics use `metric-xl` with tabular figures.
- At 375px, `display-xl` drops to 38/44 and `h1` to 30/38.
- Faces load from Google Fonts (the `@import` at the top of `components/bundle.css`).

### Space, layout, shape

- 4px grid: `space-1` … `space-9`. Side gutters ≥ `space-4` (16px) on mobile, `space-6` on desktop. Section padding `space-8` mobile, `space-9` desktop.
- Mobile-first at 375px; no horizontal scroll. The architecture diagram becomes a vertical stepped flow on mobile.
- Radii: `radius-sm` chips and inputs, `radius-md` buttons and tiles, `radius-lg` cards and panels, `radius-pill` pills and bank nodes.
- Cards are glass-light: `glass` + `blur-glass` + 1px `border-sky`, `shadow-sm` at rest, `shadow-lift` on hover. Shadows are navy/sky tinted, never gray-black.
- No coloured left-border cards, no gradient-filled buttons.

### Motion

Motion explains the system; it never decorates.

- Easing `cubic-bezier(0.22, 1, 0.36, 1)` (`--ar-ease`). UI 300–700ms (`--ar-dur-ui` 320ms); storytelling sequences 1.2–2s (`--ar-dur-story` 1600ms).
- Scroll reveals: fade + 16px rise, staggered 70ms per item. Count-ups once, on first view.
- Hover: cards rise 4px to `shadow-lift`; primary buttons get one shimmer sweep; toggles slide on a gentle spring.
- Signal pulses are concentric rings expanding from the sending bank and fading out.
- Nothing bounces or spins gratuitously. `prefers-reduced-motion` freezes the sky to a static gradient, draws charts complete, and swaps flip faces without rotation.

### States and accessibility

- WCAG AA on all text in both themes, including over the sky (via scrim).
- Focus: 2px solid `focus` ring, 2px offset, on every interactive element (4.2:1 light, 8:1 dark).
- Control outlines use `border-control` (3:1); `border-sky` is decorative only.
- Status is never colour alone: `StatusPill` always shows an icon and a word.

## Iconography

Thin-line icons on a 24px grid, 1.75 stroke, round caps and joins — Lucide geometry. The bundle ships the core set as `Aris.Icon` (`shield`, `shieldCheck`, `shieldX`, `lock`, `key`, `check`, `alert`, `info`, `arrowRight`). For anything else use Lucide at the same stroke (e.g. `lucide-static@0.441.0`): padlocks, seals, nodes, clouds, gates. Icons take `currentColor`: `sky-600` on light cards, the `-700` status inks inside pills. No filled icon styles, no emoji, no illustrations of brains or robots.

There is no ARIS logo yet: set the name as the word **ARIS** in `display` 800, `ink-900`, letter-spacing −0.02em.

## Components

`Button`, `StatusPill`, `HashChip`, `GlassCard`, `ToggleChip`, `MetricTile`, `SecurityCard`, `TerminalCard`, `RocChart`, `SkyBackground` — each card below has guidelines and a live preview. Build sections from them:

- **Hero:** `SkyBackground mesh` + `display-xl` headline on a scrim; transfer card is a `GlassCard` with `shadow-float` that flips to `StatusPill decision="block" size="lg"`.
- **How it works:** sticky visual + steps as `GlassCard`s; `HashChip` for the HMAC `risk_id`.
- **Federated learning:** `ToggleChip` row (DP-SGD, Secure Aggregation, Krum); round counter in `mono`.
- **Metrics:** `MetricTile` groups, `RocChart`, and the honesty note in `body-s`.
- **Security:** eight `SecurityCard`s.
- **Proof strip:** `TerminalCard` (`pytest → 357 passed`) beside allow-tinted badges.
