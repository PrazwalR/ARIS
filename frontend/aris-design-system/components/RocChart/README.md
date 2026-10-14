# RocChart

The ROC chart is the house chart style: local banks as thin ocean lines, the federated global model as one bold sky line drawn last, direct labels instead of a legend.

**Consumer provides:** `series` — each `{ label, points: [[fpr, tpr], …], emphasis?, color?, labelY? }` from real evaluation output — plus `title`, `ariaLabel`, optional `width`, `height`, `animate`.

- Gridlines and the dashed chance diagonal in `chart-grid`; tick text `mono-s` in `ink-600`; labels in `ink-900` (emphasised) or `ink-600`.
- Series order and colours: `chart-sky`, `chart-teal`, `chart-ocean`, `chart-sand`, `chart-coral`. Never purple.
- Lines draw themselves over 1.6s, staggered; the global line starts last. Reduced motion draws them complete.
- Use the same frame for PR curves (relabel axes in the consumer) and for the "each bank alone ↔ banks together" comparison slider.
- The preview's curves are illustrative shapes only; feed real ROC points (e.g. the ULB run, global AUC 0.990) in product.
- Tooltips, where added, show exact values in `mono`.
