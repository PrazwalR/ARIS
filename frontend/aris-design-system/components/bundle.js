/* @ds-bundle: {"format":4,"namespace":"Aris","components":[{"name":"Button"},{"name":"StatusPill"},{"name":"HashChip"},{"name":"GlassCard"},{"name":"ToggleChip"},{"name":"MetricTile"},{"name":"SecurityCard"},{"name":"TerminalCard"},{"name":"RocChart"},{"name":"SkyBackground"}]} */
(function () {
  "use strict";
  var React = window.React;
  var h = React.createElement;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;

  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }
  function reducedMotion() {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  /* Thin-line icons, 1.75 stroke, 24 grid (Lucide-style geometry). */
  var PATHS = {
    check: ["M20 6 9 17l-5-5"],
    shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"],
    shieldCheck: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "m9 12 2 2 4-4"],
    shieldX: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "m9.5 9.5 5 5", "m14.5 9.5-5 5"],
    alert: ["M12 9v4", "M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
    lock: ["M5 11h14v10H5z", "M8 11V7a4 4 0 0 1 8 0v4"],
    key: ["M15 7a4 4 0 1 1-3.5 6L5 19.5V22H2v-3l6.5-6.5A4 4 0 0 1 15 7z", "M16 8h.01"],
    arrowRight: ["M5 12h14", "m13 6 6 6-6 6"],
    info: ["M12 16v-4", "M12 8h.01", "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"]
  };
  function Icon(props) {
    var p = PATHS[props.name] || PATHS.info;
    return h("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true", focusable: "false" },
      p.map(function (d, i) { return h("path", { key: i, d: d }); }));
  }

  /* Button */
  function Button(props) {
    var variant = props.variant || "primary";
    var rest = Object.assign({}, props);
    ["variant", "size", "icon", "iconEnd", "className", "children"].forEach(function (k) { delete rest[k]; });
    return h("button", Object.assign({ type: "button" }, rest, {
      className: cx("ar-btn", "ar-btn-" + variant, props.size === "sm" && "ar-btn-sm", props.className)
    }),
      props.icon ? h(Icon, { name: props.icon }) : null,
      props.children,
      props.iconEnd ? h(Icon, { name: props.iconEnd }) : null);
  }

  /* StatusPill — decision state, icon + word, never colour alone */
  var DECISIONS = {
    allow: { icon: "shieldCheck", label: "Allow" },
    stepup: { icon: "alert", label: "Step-up" },
    block: { icon: "shieldX", label: "Block" }
  };
  function StatusPill(props) {
    var d = DECISIONS[props.decision] || DECISIONS.allow;
    var key = DECISIONS[props.decision] ? props.decision : "allow";
    return h("span", { className: cx("ar-pill", "ar-pill-" + key, props.size === "lg" && "ar-pill-lg", props.className) },
      h(Icon, { name: d.icon }), props.children || d.label);
  }

  /* HashChip — middle-truncated, expands on click */
  function truncateMiddle(s, head, tail) {
    if (!s || s.length <= head + tail + 1) return s;
    return s.slice(0, head) + "…" + s.slice(s.length - tail);
  }
  function HashChip(props) {
    var st = useState(!!props.defaultExpanded), open = st[0], setOpen = st[1];
    var v = String(props.value || "");
    var head = props.head || 4, tail = props.tail || 4;
    return h("button", {
      type: "button", className: cx("ar-hash", props.className),
      "aria-expanded": open, title: open ? "Collapse" : v,
      "aria-label": (props.label ? props.label + " " : "") + (open ? v : "hash, truncated. Activate to expand."),
      onClick: function () { setOpen(!open); }
    },
      props.label ? h("span", { className: "ar-hash-label" }, props.label) : null,
      h("span", { className: "ar-hash-value" }, open ? v : truncateMiddle(v, head, tail)));
  }

  /* GlassCard */
  function GlassCard(props) {
    return h(props.as || "div", { className: cx("ar-card", props.lift !== false && "ar-card-lift", props.className), style: props.style },
      props.icon ? h("div", { className: "ar-card-icon" }, h(Icon, { name: props.icon })) : null,
      props.eyebrow ? h("p", { className: "ar-card-eyebrow" }, props.eyebrow) : null,
      props.title ? h("h3", { className: "ar-card-title" }, props.title) : null,
      props.body ? h("p", { className: "ar-card-body" }, props.body) : null,
      props.children);
  }

  /* ToggleChip — pressed state with spring knob */
  function ToggleChip(props) {
    var controlled = typeof props.pressed === "boolean";
    var st = useState(!!props.defaultPressed), inner = st[0], setInner = st[1];
    var on = controlled ? props.pressed : inner;
    return h("button", {
      type: "button", className: cx("ar-toggle", props.className), "aria-pressed": on,
      onClick: function () { if (!controlled) setInner(!on); if (props.onChange) props.onChange(!on); }
    },
      h("span", { className: "ar-toggle-track", "aria-hidden": "true" }, h("span", { className: "ar-toggle-knob" })),
      props.label,
      props.hint ? h("span", { className: "ar-toggle-hint" }, props.hint) : null);
  }

  /* MetricTile — count-up on first view only */
  function MetricTile(props) {
    var target = Number(props.value);
    var decimals = props.decimals != null ? props.decimals : ((String(props.value).split(".")[1] || "").length);
    var st = useState(props.animate === false || reducedMotion() ? target : 0), shown = st[0], setShown = st[1];
    var ref = useRef(null);
    useEffect(function () {
      if (props.animate === false || reducedMotion()) { setShown(target); return; }
      var el = ref.current, raf = 0, done = false;
      function run() {
        var t0 = performance.now(), dur = props.duration || 1400;
        function tick(now) {
          var p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
          setShown(target * e);
          if (p < 1) raf = requestAnimationFrame(tick);
        }
        raf = requestAnimationFrame(tick);
      }
      if (typeof IntersectionObserver === "function" && el) {
        var io = new IntersectionObserver(function (es) {
          if (!done && es[0].isIntersecting) { done = true; io.disconnect(); run(); }
        }, { threshold: 0.4 });
        io.observe(el);
        return function () { io.disconnect(); cancelAnimationFrame(raf); };
      }
      run();
      return function () { cancelAnimationFrame(raf); };
    }, [target]);
    var text = shown.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return h("div", { className: cx("ar-metric", props.className), ref: ref },
      props.label ? h("span", { className: "ar-metric-label" }, props.label) : null,
      h("span", { className: "ar-metric-value", "aria-label": props.value + (props.unit ? " " + props.unit : "") },
        props.prefix || null, text, props.unit ? h("small", null, props.unit) : null),
      props.context ? h("span", { className: "ar-metric-context" }, props.context) : null);
  }

  /* SecurityCard — flips on hover/focus/click to show what was done + the stated limitation */
  function SecurityCard(props) {
    var st = useState(false), flipped = st[0], setFlipped = st[1];
    return h("div", {
      className: cx("ar-flip", props.className), tabIndex: 0, role: "button",
      "aria-pressed": flipped, "data-flipped": flipped,
      "aria-label": props.title + ". " + (props.done || "") + (props.limitation ? " Note: " + props.limitation : ""),
      onClick: function () { setFlipped(!flipped); },
      onKeyDown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFlipped(!flipped); } }
    },
      h("div", { className: "ar-flip-inner" },
        h("div", { className: "ar-flip-face ar-card" },
          h("span", { className: "ar-flip-check" }, h(Icon, { name: "check" })),
          h("h3", { className: "ar-card-title", style: { margin: 0 } }, props.title),
          props.summary ? h("p", { className: "ar-card-body" }, props.summary) : null,
          props.limitation ? h("span", { className: "ar-note", style: { marginTop: "auto" } }, h(Icon, { name: "info" }), props.noteLabel || "Note") : null),
        h("div", { className: "ar-flip-face ar-flip-back ar-card" },
          h("p", { className: "ar-flip-k" }, "What was done"),
          h("p", { className: "ar-card-body", style: { color: "var(--ink-900)" } }, props.done),
          props.limitation ? h("p", { className: "ar-flip-k", style: { marginTop: "var(--space-2)" } }, "Stated limitation") : null,
          props.limitation ? h("span", { className: "ar-note" }, h(Icon, { name: "info" }), props.limitation) : null)));
  }

  /* TerminalCard — types a real command, then prints its result */
  function TerminalCard(props) {
    var cmd = props.command || "";
    var instant = props.animate === false || reducedMotion();
    var st = useState(instant ? cmd.length : 0), n = st[0], setN = st[1];
    useEffect(function () {
      if (instant) return;
      var i = 0, id = setInterval(function () { i++; setN(i); if (i >= cmd.length) clearInterval(id); }, props.speed || 55);
      return function () { clearInterval(id); };
    }, [cmd]);
    var finished = n >= cmd.length;
    return h("div", { className: cx("ar-term", props.className), role: "figure", "aria-label": "Terminal: " + cmd + " — " + (props.result || "") },
      h("div", { className: "ar-term-bar", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("span", null, props.title || "aris — zsh")),
      h("div", { className: "ar-term-body", "aria-hidden": "true" },
        h("span", { className: "ar-term-prompt" }, "$ "), cmd.slice(0, n),
        finished && props.result ? h(React.Fragment, null, "\n", h("span", { className: "ar-term-ok" }, props.result)) : null,
        !finished || !props.result ? h("span", { className: "ar-term-caret" }) : null));
  }

  /* RocChart — local banks thin, global bold sky, direct labels, no legend */
  function RocChart(props) {
    var W = props.width || 360, H = props.height || 300, m = { t: 20, r: 96, b: 40, l: 56 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var x = function (v) { return m.l + v * iw; }, y = function (v) { return m.t + (1 - v) * ih; };
    var ticks = [0, 0.25, 0.5, 0.75, 1];
    var series = props.series || [];
    var anim = props.animate !== false && !reducedMotion();
    function path(pts) { return pts.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1); }).join(" "); }
    return h("figure", { className: cx("ar-chart", props.className), style: { margin: 0 } },
      h("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": props.ariaLabel || props.title || "ROC curve" },
        props.title ? h("text", { className: "ar-chart-title", x: m.l, y: 12 }, props.title) : null,
        ticks.map(function (t) {
          return h("g", { key: t },
            h("line", { className: "ar-chart-grid", x1: x(0), x2: x(1), y1: y(t), y2: y(t) }),
            h("line", { className: "ar-chart-grid", x1: x(t), x2: x(t), y1: y(0), y2: y(1) }),
            h("text", { className: "ar-chart-axis", x: m.l - 8, y: y(t) + 4, textAnchor: "end" }, t.toFixed(2)),
            h("text", { className: "ar-chart-axis", x: x(t), y: y(0) + 18, textAnchor: "middle" }, t.toFixed(2)));
        }),
        h("line", { className: "ar-chart-diag", x1: x(0), y1: y(0), x2: x(1), y2: y(1) }),
        h("text", { className: "ar-chart-axis", x: x(0.5), y: H - 2, textAnchor: "middle" }, "False positive rate"),
        h("text", { className: "ar-chart-axis", x: 0, y: 0, textAnchor: "middle", transform: "translate(10 " + (m.t + ih / 2) + ") rotate(-90)" }, "True positive rate"),
        series.map(function (s, i) {
          var bold = !!s.emphasis, last = s.points[s.points.length - 1];
          var color = "var(--" + (s.color || (bold ? "chart-sky" : "chart-ocean")) + ")";
          var ly = s.labelY != null ? s.labelY : last[1];
          return h("g", { key: i },
            h("path", {
              d: path(s.points), pathLength: 1,
              className: cx("ar-chart-line", anim && "ar-chart-draw"),
              style: { stroke: color, strokeWidth: bold ? 2.75 : 1.25, opacity: bold ? 1 : 0.7, animationDelay: anim ? (bold ? series.length * 90 + 200 : i * 90) + "ms" : undefined }
            }),
            s.label ? h("text", { className: bold ? "ar-chart-label" : "ar-chart-label-muted", x: x(1) + 8, y: y(ly) + 4 }, s.label) : null);
        })));
  }

  /* SkyBackground — living sky: day-cycle gradient, drifting clouds, optional bank mesh */
  var MESH = [[0.12, 0.62], [0.3, 0.3], [0.52, 0.5], [0.74, 0.26], [0.88, 0.6]];
  function SkyBackground(props) {
    var phase = props.phase || "auto";
    var showMesh = !!props.mesh;
    return h("div", { className: cx("ar-sky", props.className), style: Object.assign({ minHeight: props.minHeight || 240 }, props.style) },
      h("div", { className: "ar-sky-grad", "data-phase": phase === "auto" ? null : phase, "aria-hidden": "true" }),
      h("div", { className: "ar-sky-cloud", "aria-hidden": "true", style: { width: "46%", height: "38%", top: "8%", left: "4%" } }),
      h("div", { className: "ar-sky-cloud", "aria-hidden": "true", style: { width: "34%", height: "30%", top: "46%", left: "58%", animationDuration: "120s" } }),
      showMesh ? h("svg", { className: "ar-sky-mesh", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" },
        MESH.map(function (a, i) {
          return MESH.slice(i + 1).map(function (b, j) {
            return h("line", { key: i + "-" + j, x1: a[0] * 100, y1: a[1] * 100, x2: b[0] * 100, y2: b[1] * 100, vectorEffect: "non-scaling-stroke" });
          });
        }),
        h("line", { className: "ar-sky-hot", x1: MESH[1][0] * 100, y1: MESH[1][1] * 100, x2: MESH[2][0] * 100, y2: MESH[2][1] * 100, vectorEffect: "non-scaling-stroke" })
      ) : null,
      h("div", { className: "ar-sky-content" }, props.children));
  }

  var api = { Button: Button, StatusPill: StatusPill, HashChip: HashChip, GlassCard: GlassCard, ToggleChip: ToggleChip, MetricTile: MetricTile, SecurityCard: SecurityCard, TerminalCard: TerminalCard, RocChart: RocChart, SkyBackground: SkyBackground, Icon: Icon };
  window.Aris = Object.assign(window.Aris || {}, api);
})();
