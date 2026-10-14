# Using ARIS in your frontend

Drop this folder into your project, e.g. `frontend/src/design-system/aris/`.

```
aris-design-system/
  tokens.css            CSS variables (light + dark), type classes (.h1, .body, .mono …)
  tokens.json           The same tokens as data
  README.md             Brand book: colour, type, motion, voice rules
  components/
    aris.esm.js         React components as an ES module  ← use this in React apps
    bundle.css          Component styles (+ Google Fonts import)
    index.d.ts          Prop types / docs
    bundle.js           Same components as a classic script (window.Aris), for plain HTML
    <Component>/README.md, preview.html   Guidelines and standalone examples
```

## React (Vite / CRA / Next.js)

Import the CSS once, at your app entry (`main.jsx`, `App.jsx`, or Next's `app/layout.tsx`):

```js
import "./design-system/aris/tokens.css";
import "./design-system/aris/components/bundle.css";
```

Use components:

```jsx
import { Button, StatusPill, HashChip, GlassCard, MetricTile, RocChart, SkyBackground } from "./design-system/aris/components/aris.esm.js";

export default function Hero() {
  return (
    <SkyBackground mesh minHeight={480}>
      <div className="ar-scrim">
        <h1 className="display-xl">Banks that warn each other — without sharing a single customer record.</h1>
        <Button iconEnd="arrowRight">See how it works</Button>
        <Button variant="secondary">View the metrics</Button>
      </div>
      <StatusPill decision="block" size="lg">Blocked</StatusPill>
      <MetricTile label="Global AUC · ULB" value="0.990" />
    </SkyBackground>
  );
}
```

TypeScript: add `// @ts-ignore` on the import or a `declare module` pointing at `components/index.d.ts`.
Next.js: the module starts with `"use client"` and is safe to server-render.

## Dark mode

Follows the OS by default. Force a theme with `<html data-theme="dark">` or `data-theme="light"`.

## Plain HTML

Load `tokens.css`, `components/bundle.css`, React 18 + ReactDOM 18 UMD, then `components/bundle.js`; components are on `window.Aris`.
