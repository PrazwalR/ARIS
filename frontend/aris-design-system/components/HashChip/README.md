# HashChip

A hash chip shows a long hex value — a `risk_id`, an Ed25519 signature, a key fingerprint — truncated in the middle (`b742…2d37`) and expanded on click.

**Consumer provides:** `value` (the full string), optional `label` (muted prefix such as `risk_id`), optional `head` / `tail` lengths (default 4 / 4), `defaultExpanded`.

- Set in `mono-s`; ground `surface-100`, edge `border-sky`, radius `radius-sm`.
- Every real account identifier in the UI is shown as a hash chip — this is what the privacy-lens mode switches all account numbers to.
- Keep the head/tail symmetric unless the prefix carries meaning (signature algorithm tags).
- The full value is always available: on click, in `title`, and to screen readers when expanded.
