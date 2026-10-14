# StatusPill

A status pill shows a BankBot decision — allow, step-up or block — with an icon and a word, so the state never rides on colour alone.

**Consumer provides:** `decision` (`"allow" | "stepup" | "block"`), optional `size="lg"` for the hero transfer card, optional `children` to replace the default word (e.g. "Blocked", "Verified").

- Tint grounds `allow-100` / `stepup-100` / `block-100` with `allow-700` / `stepup-700` / `block-700` text — all 4.6:1 or better.
- Use the same three states everywhere: live feed rows, the policy gauge legend, the forged-retraction result ("signature invalid" is a `block` pill).
- Uppercase is reserved for pills and eyebrows.
- Don't invent extra states (e.g. "pending"); show progress with a muted `ink-600` label instead.
