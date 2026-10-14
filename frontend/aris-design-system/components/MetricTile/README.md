# MetricTile

A metric tile shows one measured number large, with its label and one line of context, and counts up once the first time it scrolls into view.

**Consumer provides:** `value` exactly as measured (pass `"0.990"` as a string to keep trailing zeros), `label`, optional `unit`, `context`, `decimals`, `animate`.

- Number in `metric-xl` with tabular figures; label in `label` style, `sky-700`, uppercase; context in `body-s`, `ink-600`, with comparison numbers in `mono`.
- Only real, measured numbers. Never round up, never add filler stats.
- Lead fraud results with AUC and PR-AUC; accuracy is secondary on imbalanced data. Place the honesty note ("AUC and PR-AUC lead. Accuracy is secondary on imbalanced fraud data.") under the metric group in `body-s`.
- Count-up runs once (first view), ~1.4s ease-out; reduced motion shows the final value immediately. Screen readers always get the final value.
