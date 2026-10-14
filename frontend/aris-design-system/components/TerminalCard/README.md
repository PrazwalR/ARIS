# TerminalCard

The terminal card types out a real command and then its real result, as proof in the quality strip.

**Consumer provides:** `command`, `result` — both copied from an actual run — optional `title`, `speed`, `animate`.

- Always deep-ocean (`#0e2236` ground, `#e9f2fa` text) in both themes; prompt in lifted sky, result in lifted green. Radius `radius-md`, shadow `shadow-float`.
- One command per card. Keep the result line literal (`357 passed`), and update it whenever the suite is re-run before a review.
- Sits beside the badges: tests passing, CI green on Python 3.10 and 3.13, mypy strict, linted.
- Reduced motion prints the full text at once.
