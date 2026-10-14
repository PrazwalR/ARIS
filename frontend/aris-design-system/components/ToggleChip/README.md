# ToggleChip

A toggle chip switches one layer of the system on or off — DP-SGD, Secure Aggregation, Krum, the privacy lens — with a spring-sliding knob.

**Consumer provides:** `label`, optional `hint`, and either `pressed` + `onChange` (controlled) or `defaultPressed`.

- Off: `surface-raised` with a `border-control` outline. On: `sky-100` ground, `sky-600` outline, `sky-500` track, `on-sky` knob.
- Announced as a pressed/unpressed button; the state is also visible by knob position, not colour alone.
- Group related chips in one row with `space-3` gaps; the visual they control must react within `--ar-dur-ui` (e.g. turning on Krum rejects the malicious bank's oversized update).
- Don't use for navigation or for one-shot actions — that's a Button.
