(function (root) {
  "use strict";
  const tools = [
    ["trend", "Trendline", "move-up-right", 2, "Lines", "t"],
    ["ray", "Ray", "arrow-up-right", 2, "Lines"],
    ["info", "Info line", "ruler", 2, "Lines"],
    ["extended", "Extended line", "move-diagonal", 2, "Lines"],
    ["angle", "Trend angle", "triangle-right", 2, "Lines"],
    ["horizontal", "Horizontal line", "minus", 1, "Lines", "h"],
    ["horizontal-ray", "Horizontal ray", "arrow-right", 1, "Lines", "j"],
    ["vertical", "Vertical line", "move-vertical", 1, "Lines", "v"],
    ["cross", "Crossline", "plus", 1, "Lines", "c"],
    ["parallel", "Parallel channel", "git-compare", 3, "Channels"],
    ["regression", "Regression trend", "chart-no-axes-combined", 2, "Channels"],
    ["flat", "Flat top/bottom", "align-start-horizontal", 3, "Channels"],
    ["disjoint", "Disjoint channel", "git-branch", 4, "Channels"],
    ["pitchfork", "Pitchfork", "git-fork", 3, "Pitchforks"],
    ["schiff", "Schiff pitchfork", "git-fork", 3, "Pitchforks"],
    ["modified-schiff", "Modified Schiff pitchfork", "git-fork", 3, "Pitchforks"],
    ["inside", "Inside pitchfork", "git-fork", 3, "Pitchforks"],
    ["fib-retracement", "Fib retracement", "list-filter", 2, "Fibonacci", "f"],
    ["fib-extension", "Trend-based fib extension", "chart-no-axes-combined", 3, "Fibonacci"],
    ["fib-channel", "Fib channel", "git-compare", 3, "Fibonacci"],
    ["fib-time-zone", "Fib time zone", "columns-3", 2, "Fibonacci"],
    ["fib-fan", "Fib speed resistance fan", "radio-tower", 2, "Fibonacci"],
    ["fib-time", "Trend-based fib time", "chart-no-axes-column-increasing", 3, "Fibonacci"],
    ["fib-circles", "Fib circles", "target", 2, "Fibonacci"],
    ["fib-spiral", "Fib spiral", "rotate-cw", 2, "Fibonacci"],
    ["fib-arcs", "Fib speed resistance arcs", "radio", 2, "Fibonacci"],
    ["fib-wedge", "Fib wedge", "shell", 3, "Fibonacci"],
    ["pitchfan", "Pitchfan", "git-fork", 3, "Fibonacci"],
    ["gann-box", "Gann box", "grid-3x3", 2, "Gann"],
    ["gann-fixed", "Gann square fixed", "scan", 2, "Gann"],
    ["gann-square", "Gann square", "grid-2x2", 2, "Gann"],
    ["gann-fan", "Gann fan", "radio-tower", 2, "Gann"],
    ["xabcd", "XABCD pattern", "waypoints", 5, "Chart patterns"],
    ["cypher", "Cypher pattern", "waypoints", 5, "Chart patterns"],
    ["head-shoulders", "Head and shoulders", "chart-no-axes-combined", 7, "Chart patterns"],
    ["abcd", "ABCD pattern", "waypoints", 4, "Chart patterns"],
    ["pattern-triangle", "Triangle pattern", "triangle", 5, "Chart patterns"],
    ["three-drives", "Three drives pattern", "waypoints", 7, "Chart patterns"],
    ["wave-impulse", "Elliott impulse wave (1-2-3-4-5)", "waypoints", 6, "Elliott waves"],
    ["wave-correction", "Elliott correction wave (A-B-C)", "waypoints", 4, "Elliott waves"],
    ["wave-triangle", "Elliott triangle wave (A-B-C-D-E)", "waypoints", 6, "Elliott waves"],
    ["wave-double", "Elliott double combo wave (W-X-Y)", "waypoints", 4, "Elliott waves"],
    ["wave-triple", "Elliott triple combo wave (W-X-Y-X-Z)", "waypoints", 6, "Elliott waves"],
    ["cyclic", "Cyclic lines", "columns-3", 2, "Cycles"],
    ["time-cycles", "Time cycles", "waves", 2, "Cycles"],
    ["sine", "Sine line", "waves", 2, "Cycles"],
    ["long-position", "Long position", "trending-up", 3, "Forecasting"],
    ["short-position", "Short position", "trending-down", 3, "Forecasting"],
    ["price-range", "Price range", "move-vertical", 2, "Measures"],
    ["date-range", "Date range", "move-horizontal", 2, "Measures"],
    ["date-price-range", "Date and price range", "scan", 2, "Measures"],
    ["brush", "Brush", "brush", 0, "Brushes"],
    ["highlighter", "Highlighter", "highlighter", 0, "Brushes"],
    ["arrow", "Arrow", "move-up-right", 2, "Arrows"],
    ["arrow-up", "Arrow mark up", "arrow-up", 1, "Arrows"],
    ["arrow-down", "Arrow mark down", "arrow-down", 1, "Arrows"],
    ["rectangle", "Rectangle", "square", 2, "Shapes"],
    ["rotated-rectangle", "Rotated rectangle", "diamond", 3, "Shapes"],
    ["path", "Path", "route", 0, "Shapes"],
    ["circle", "Circle", "circle", 2, "Shapes"],
    ["ellipse", "Ellipse", "circle", 2, "Shapes"],
    ["polyline", "Polyline", "waypoints", 0, "Shapes"],
    ["triangle", "Triangle", "triangle", 3, "Shapes"],
    ["curve", "Curve", "spline", 3, "Shapes"],
    ["double-curve", "Double curve", "spline", 4, "Shapes"],
    ["text", "Text", "type", 1, "Text and notes"],
    ["note", "Note", "sticky-note", 1, "Text and notes"],
    ["price-note", "Price note", "badge-dollar-sign", 1, "Text and notes"],
    ["pin", "Pin", "map-pin", 1, "Text and notes"],
    ["callout", "Callout", "message-square", 2, "Text and notes"],
    ["comment", "Comment", "message-circle", 1, "Text and notes"],
    ["price-label", "Price label", "tag", 1, "Text and notes"],
    ["flag", "Flag mark", "flag", 1, "Text and notes"],
    ["emoji", "Emoji", "smile", 1, "Markers"],
  ];
  const categories = {
    lines: ["Line tools", "move-up-right", ["Lines", "Channels", "Pitchforks"]],
    fibonacci: ["Fibonacci and Gann tools", "list-filter", ["Fibonacci", "Gann"]],
    patterns: ["Pattern and cycle tools", "waypoints", ["Chart patterns", "Elliott waves", "Cycles"]],
    measures: ["Position and measure tools", "ruler", ["Forecasting", "Measures"]],
    shapes: ["Brushes and shapes", "shapes", ["Brushes", "Arrows", "Shapes"]],
    notes: ["Text and markers", "type", ["Text and notes", "Markers"]],
  };
  const categoryOf = (id) => Object.keys(categories).find((key) => categories[key][2].includes(tools.find((t) => t[0] === id)?.[4]));
  const freehand = (id) => ["brush", "highlighter"].includes(id);
  const variablePath = (id) => ["path", "polyline"].includes(id);
  const annotation = (id) => categoryOf(id) === "notes";
  const patternLabels = {
    xabcd: ["X", "A", "B", "C", "D"], cypher: ["X", "A", "B", "C", "D"],
    abcd: ["A", "B", "C", "D"], "head-shoulders": ["", "LS", "", "H", "", "RS", ""],
    "three-drives": ["0", "1", "A", "2", "B", "3", "C"],
    "wave-impulse": ["0", "1", "2", "3", "4", "5"], "wave-correction": ["0", "A", "B", "C"],
    "wave-triangle": ["0", "A", "B", "C", "D", "E"], "wave-double": ["0", "W", "X", "Y"],
    "wave-triple": ["0", "W", "X", "Y", "X", "Z"],
  };
  const fibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618];
  const timeLevels = [0, 1, 2, 3, 5, 8, 13, 21];
  const fibonacciTool = (id) => id.startsWith("fib-") || id === "pitchfan";
  const advancedTool = (id) => fibonacciTool(id) || id.startsWith("gann-");
  function defaultLevels(type) { return type === "fib-time-zone" ? timeLevels : fibLevels; }
  const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  const shifted = (p, dx, dy) => ({ x: p.x + dx, y: p.y + dy });

  function ratioGeometry(type, p, options = {}) {
    const lines = [], fills = [], labels = [];
    const line = (a, b, mode = "segment", dashed = false) => lines.push({ a, b, mode, dashed });
    const label = (text, point) => labels.push({ text, point });
    const [a, b, c] = p;
    const levels = options.levels || defaultLevels(type);
    if (!a || !b) return { lines, fills, labels };
    const dx = b.x - a.x, dy = b.y - a.y;
    const at = (r) => shifted(a, dx * r, dy * r);
    const radius = Math.hypot(dx, dy), angle = Math.atan2(dy, dx);
    const arc = (center, rx, ry, start, end) => {
      const count = Math.max(24, Math.ceil(Math.abs(end - start) * 24));
      let previous = null;
      for (let i = 0; i <= count; i++) {
        const theta = start + (end - start) * i / count;
        const point = { x: center.x + Math.cos(theta) * rx, y: center.y + Math.sin(theta) * ry };
        if (previous) line(previous, point);
        previous = point;
      }
    };
    if (["fib-retracement", "fib-extension"].includes(type)) {
      if (type === "fib-extension" && !c) { line(a, b, "segment", true); return { lines, fills, labels }; }
      const origin = type === "fib-extension" ? c : b;
      const delta = type === "fib-extension" ? dy : -dy;
      const left = Math.min(...p.map((v) => v.x)), right = Math.max(...p.map((v) => v.x));
      levels.forEach((r, i) => {
        const y = origin.y + delta * r;
        line({ x: left, y }, { x: right, y });
        label(`${r} (${y.toFixed(2)})`, { x: right, y });
        if (i) fills.push([{ x: left, y: origin.y + delta * levels[i - 1] }, { x: right, y: origin.y + delta * levels[i - 1] }, { x: right, y }, { x: left, y }]);
      });
      line(a, b, "segment", true); if (c) line(b, c, "segment", true);
    } else if (type === "fib-channel") {
      line(a, b, "ray", true);
      if (c) {
        const offset = c.y - a.y - (dx ? dy / dx * (c.x - a.x) : 0);
        levels.forEach((r) => { const first = shifted(a, 0, offset * r), last = shifted(b, 0, offset * r); line(first, last, "ray"); label(String(r), last); });
      }
    } else if (type === "fib-time-zone" || type === "fib-time") {
      if (type === "fib-time" && !c) { line(a, b, "segment", true); return { lines, fills, labels }; }
      const origin = type === "fib-time" ? c : a;
      levels.forEach((r) => { const point = { x: origin.x + dx * r, y: origin.y }; line(point, shifted(point, 0, 1), "extended"); label(String(r), point); });
      line(a, b, "segment", true); if (c) line(b, c, "segment", true);
    } else if (type === "fib-fan") {
      levels.forEach((r) => { const end = { x: b.x, y: a.y + dy * r }; line(a, end, "ray"); label(String(r), end); });
    } else if (type === "pitchfan") {
      line(a, b, "ray", true);
      if (c) levels.filter((r) => r <= 1).forEach((r) => { const end = { x: b.x + (c.x - b.x) * r, y: b.y + (c.y - b.y) * r }; line(a, end, "ray"); label(String(r), end); });
    } else if (type === "fib-spiral") {
      // Golden-ratio growth for each quarter turn, with the second anchor at radius 1.
      let previous = null;
      for (let i = 0; i <= 320; i++) {
        const theta = -Math.PI * 2 + i / 320 * Math.PI * 4;
        const r = radius * Math.pow((1 + Math.sqrt(5)) / 2, theta / (Math.PI / 2));
        const point = { x: a.x + r * Math.cos(angle + theta), y: a.y + r * Math.sin(angle + theta) };
        if (previous) line(previous, point);
        previous = point;
      }
    } else if (["fib-circles", "fib-arcs", "fib-wedge"].includes(type)) {
      let start = 0, end = Math.PI * 2;
      if (type === "fib-arcs") { start = angle - Math.PI / 2; end = angle + Math.PI / 2; }
      if (type === "fib-wedge") {
        if (!c) { line(a, b); return { lines, fills, labels }; }
        start = angle; end = Math.atan2(c.y - a.y, c.x - a.x);
        while (end < start) end += Math.PI * 2;
        if (end - start > Math.PI) end -= Math.PI * 2;
        line(a, b, "ray"); line(a, c, "ray");
      }
      levels.filter((r) => r > 0).forEach((r) => { arc(a, radius * r, radius * r, start, end); label(String(r), { x: a.x + radius * r * Math.cos(end), y: a.y + radius * r * Math.sin(end) }); });
      line(a, b, "segment", true);
    } else if (type === "gann-fan") {
      const unit = options.unit > 0 ? options.unit : Math.abs(dy / (dx || 1));
      for (const ratio of [1 / 8, 1 / 4, 1 / 3, 1 / 2, 1, 2, 3, 4, 8]) {
        const end = { x: b.x, y: a.y + Math.sign(dy || 1) * Math.abs(dx) * unit * ratio };
        line(a, end, "ray"); label(ratio < 1 ? `1:${Math.round(1 / ratio)}` : `${ratio}:1`, end);
      }
    } else if (type.startsWith("gann-")) {
      const boxLevels = [0, 0.25, 0.5, 0.75, 1];
      boxLevels.forEach((r) => {
        const v = at(r);
        line({ x: a.x, y: v.y }, { x: b.x, y: v.y });
        line({ x: v.x, y: a.y }, { x: v.x, y: b.y });
        label(String(r), { x: b.x, y: v.y });
      });
      fills.push([a, { x: b.x, y: a.y }, b, { x: a.x, y: b.y }]);
      line(a, b); line({ x: a.x, y: b.y }, { x: b.x, y: a.y });
      if (type !== "gann-box") {
        for (const r of [0.25, 0.5, 0.75]) { line(a, { x: b.x, y: a.y + dy * r }); line(a, { x: a.x + dx * r, y: b.y }); }
        const start = dx >= 0 ? 0 : Math.PI, end = start + Math.sign(dx * dy || 1) * Math.PI / 2;
        for (const r of [0.25, 0.5, 0.75, 1]) arc(a, Math.abs(dx) * r, Math.abs(dy) * r, start, end);
      }
    }
    return { lines, fills, labels };
  }

  // Geometry is independent of the chart so it can be tested without a browser.
  function geometry(type, p, samples = [], options = {}) {
    if (advancedTool(type)) return ratioGeometry(type, p, options);
    if (!["lines", "fibonacci"].includes(categoryOf(type))) return extraGeometry(type, p, options);
    const lines = [], fills = [];
    const line = (a, b, mode = "segment", dashed = false) => lines.push({ a, b, mode, dashed });
    const [a, b, c, d] = p;
    if (!a) return { lines, fills };
    if (type === "horizontal" || type === "horizontal-ray" || type === "cross") {
      line(a, shifted(a, 1, 0), type === "horizontal-ray" ? "ray" : "extended");
    }
    if (type === "vertical" || type === "cross") line(a, shifted(a, 0, 1), "extended");
    if (!b) return { lines, fills };
    if (["trend", "info", "angle", "ray", "extended"].includes(type)) {
      line(a, b, type === "ray" ? "ray" : type === "extended" ? "extended" : "segment");
    } else if (type === "regression") {
      if (samples.length < 2) return { lines, fills };
      const mx = samples.reduce((n, s) => n + s.x, 0) / samples.length;
      const my = samples.reduce((n, s) => n + s.y, 0) / samples.length;
      const denom = samples.reduce((n, s) => n + (s.x - mx) ** 2, 0);
      const slope = denom ? samples.reduce((n, s) => n + (s.x - mx) * (s.y - my), 0) / denom : 0;
      const deviation = Math.sqrt(samples.reduce((n, s) => n + (s.y - (my + slope * (s.x - mx))) ** 2, 0) / samples.length);
      const left = { x: Math.min(a.x, b.x), y: 0 }, right = { x: Math.max(a.x, b.x), y: 0 };
      left.y = my + slope * (left.x - mx); right.y = my + slope * (right.x - mx);
      line(left, right, "segment", true);
      line(shifted(left, 0, deviation * 2), shifted(right, 0, deviation * 2));
      line(shifted(left, 0, -deviation * 2), shifted(right, 0, -deviation * 2));
      fills.push([shifted(left, 0, deviation * 2), shifted(right, 0, deviation * 2), shifted(right, 0, -deviation * 2), shifted(left, 0, -deviation * 2)]);
    } else if (["parallel", "flat", "disjoint"].includes(type)) {
      line(a, b);
      if (c) {
        let q, r;
        if (type === "parallel") {
          const slope = b.x === a.x ? 0 : (b.y - a.y) / (b.x - a.x);
          const offset = c.y - (a.y + slope * (c.x - a.x));
          q = shifted(a, 0, offset); r = shifted(b, 0, offset);
        } else if (type === "flat") {
          q = { x: a.x, y: c.y }; r = { x: b.x, y: c.y };
        } else { q = c; r = d; }
        if (r) {
          line(q, r); line(midpoint(a, q), midpoint(b, r), "segment", true);
          fills.push([a, b, r, q]);
        }
      }
    } else if (c) {
      const median = midpoint(b, c);
      let origin = a, upper = b, lower = c;
      if (type === "schiff") origin = { x: a.x, y: (a.y + b.y) / 2 };
      if (type === "modified-schiff") origin = midpoint(a, b);
      if (type === "inside") { upper = midpoint(a, b); lower = midpoint(a, c); }
      const target = type === "inside" ? midpoint(upper, lower) : median;
      const dx = target.x - origin.x, dy = target.y - origin.y;
      line(origin, target, "ray", true);
      line(upper, shifted(upper, dx, dy), "ray"); line(lower, shifted(lower, dx, dy), "ray");
      line(b, c, "segment", true);
      line(a, b, "segment", true);
    } else line(a, b, "segment", true);
    return { lines, fills };
  }

  function extraGeometry(type, p, options = {}) {
    const lines = [], fills = [], labels = [];
    const line = (a, b, mode = "segment", dashed = false) => lines.push({ a, b, mode, dashed });
    const polygon = (vertices) => { vertices.forEach((a, i) => line(a, vertices[(i + 1) % vertices.length])); fills.push(vertices); };
    const path = (vertices) => vertices.forEach((a, i) => { if (i) line(vertices[i - 1], a); });
    const curve = (fn) => path(Array.from({ length: 97 }, (_, i) => fn(i / 96)));
    const [a, b, c, d] = p;
    if (!a) return { lines, fills, labels };
    const mark = (text, point) => labels.push({ text, point });
    if (annotation(type)) {
      line(a, shifted(a, 0, -18));
      if (b) line(a, b);
      const text = options.text || (type === "emoji" ? "\u{1F642}" : type === "pin" ? "Pin" : type === "flag" ? "Flag" : "Text");
      mark(text, shifted(b || a, 0, -20));
    } else if (type === "arrow-up" || type === "arrow-down") {
      const direction = type === "arrow-up" ? 1 : -1, base = shifted(a, 0, direction * 25);
      line(base, a); line(a, shifted(a, -8, direction * 10)); line(a, shifted(a, 8, direction * 10));
    } else if (patternLabels[type] || type === "pattern-triangle" || freehand(type) || variablePath(type)) {
      path(p);
      (patternLabels[type] || []).forEach((text, i) => { if (p[i] && text) mark(text, shifted(p[i], 0, -12)); });
      if (["xabcd", "cypher"].includes(type) && p.length >= 5) { fills.push([p[0], p[1], p[2]], [p[2], p[3], p[4]]); line(p[0], p[2], "segment", true); line(p[2], p[4], "segment", true); }
      if (type === "head-shoulders" && p.length >= 5) line(p[2], p[4], "extended", true);
      if (type === "pattern-triangle" && p.length >= 4) { line(p[0], p[2], "ray", true); line(p[1], p[3], "ray", true); }
    } else if (b) {
      const dx = b.x - a.x, dy = b.y - a.y;
      if (type === "arrow") {
        line(a, b); const angle = Math.atan2(dy, dx);
        for (const offset of [-0.45, 0.45]) line(b, shifted(b, -12 * Math.cos(angle + offset), -12 * Math.sin(angle + offset)));
      } else if (["rectangle", "date-price-range", "price-range", "date-range"].includes(type)) {
        polygon([a, { x: b.x, y: a.y }, b, { x: a.x, y: b.y }]);
      } else if (type === "rotated-rectangle") {
        line(a, b);
        if (c) { const len = Math.hypot(dx, dy) || 1, offset = ((c.x - a.x) * -dy + (c.y - a.y) * dx) / len; polygon([a, b, shifted(b, -dy / len * offset, dx / len * offset), shifted(a, -dy / len * offset, dx / len * offset)]); }
      } else if (type === "triangle") { if (c) polygon([a, b, c]); else line(a, b); }
      else if (["circle", "ellipse"].includes(type)) {
        const center = type === "circle" ? a : midpoint(a, b), radius = Math.hypot(dx, dy);
        const rx = type === "circle" ? radius : Math.abs(dx) / 2, ry = type === "circle" ? radius : Math.abs(dy) / 2;
        curve((t) => shifted(center, Math.cos(t * Math.PI * 2) * rx, Math.sin(t * Math.PI * 2) * ry));
      } else if (type === "curve" || type === "double-curve") {
        if (!c || (type === "double-curve" && !d)) path(p);
        else curve((t) => {
          const s = 1 - t;
          return type === "curve" ? { x: s * s * a.x + 2 * s * t * b.x + t * t * c.x, y: s * s * a.y + 2 * s * t * b.y + t * t * c.y } :
            { x: s ** 3 * a.x + 3 * s * s * t * b.x + 3 * s * t * t * c.x + t ** 3 * d.x, y: s ** 3 * a.y + 3 * s * s * t * b.y + 3 * s * t * t * c.y + t ** 3 * d.y };
        });
      } else if (["cyclic", "time-cycles", "sine"].includes(type)) {
        const spacing = Math.abs(dx);
        if (spacing < 1) line(a, b);
        else if (type === "cyclic") for (let i = 0; i < 24; i++) { const point = shifted(a, dx * i, 0); line(point, shifted(point, 0, 1), "extended"); }
        else if (type === "sine") curve((t) => shifted(a, dx * t * 6, Math.sin(t * Math.PI * 12) * dy));
        else for (let i = 0; i < 12; i++) curve((t) => ({ x: a.x + dx * (i + t), y: a.y - Math.sin(t * Math.PI) * spacing / 2 }));
      } else if (["long-position", "short-position"].includes(type)) {
        line(a, b);
        if (c) { const right = Math.max(a.x, b.x, c.x), left = Math.min(a.x, b.x, c.x); for (const point of [a, b, c]) line({ x: left, y: point.y }, { x: right, y: point.y }); fills.push([{ x: left, y: a.y }, { x: right, y: a.y }, { x: right, y: b.y }, { x: left, y: b.y }], [{ x: left, y: a.y }, { x: right, y: a.y }, { x: right, y: c.y }, { x: left, y: c.y }]); }
      }
    }
    return { lines, fills, labels };
  }

  function clipLine(a, b, mode, width, height) {
    const dx = b.x - a.x, dy = b.y - a.y;
    if (Math.abs(dx) + Math.abs(dy) < 1e-9) return null;
    let lo = mode === "extended" ? -Infinity : 0, hi = mode === "segment" ? 1 : Infinity;
    for (const [start, delta, max] of [[a.x, dx, width], [a.y, dy, height]]) {
      if (Math.abs(delta) < 1e-9) { if (start < 0 || start > max) return null; }
      else {
        const t1 = -start / delta, t2 = (max - start) / delta;
        lo = Math.max(lo, Math.min(t1, t2)); hi = Math.min(hi, Math.max(t1, t2));
      }
    }
    return hi >= lo ? [shifted(a, lo * dx, lo * dy), shifted(a, hi * dx, hi * dy)] : null;
  }
  function distance(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
  }

  function snapPoint(point, candidates, mode, radius = 12) {
    if (mode === "off") return null;
    let nearest = null, best = Infinity;
    for (const candidate of candidates) {
      if (!Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) continue;
      const distance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
      if (distance < best) { best = distance; nearest = candidate; }
    }
    return mode === "strong" || best <= radius ? nearest : null;
  }
  function zoomRange(range, factor) {
    if (!range || !Number.isFinite(range.from + range.to) || range.to <= range.from || !Number.isFinite(factor) || factor <= 0) return null;
    const center = (range.from + range.to) / 2, span = Math.max(5, Math.min(100000, (range.to - range.from) * factor));
    return { from: center - span / 2, to: center + span / 2 };
  }
  function measurementStats(a, b, bars) {
    const seconds = Math.round(Math.abs(b.time - a.time)), delta = b.price - a.price;
    const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60), remainder = Math.round(seconds % 60);
    const duration = [days && days + "d", hours && hours + "h", minutes && minutes + "m", (!days && !hours && !minutes || remainder) && remainder + "s"].filter(Boolean).join(" ");
    return { delta, percent: a.price ? delta / a.price * 100 : 0, bars: Math.round(Math.abs(bars)), duration };
  }
  const removableDrawings = (items, includeLocked) => items.filter((item) => includeLocked || !item.locked);

  function attach(pane, options = {}) {
    const wrap = pane.chartEl.parentElement;
    wrap.classList.add("has-drawing-tools");
    const rail = document.createElement("div"); rail.className = "drawing-rail";
    rail.setAttribute("role", "toolbar"); rail.setAttribute("aria-label", "Chart drawing tools");
    const icon = (name) => `<i data-lucide="${name}"></i>`;
    const button = (action, name, symbol) => `<button type="button" data-action="${action}" title="${name}" aria-label="${name}">${icon(symbol)}</button>`;
    rail.innerHTML = button("pointer", "Pointer", "mouse-pointer-2") + button("indicators", "Chart indicators", "chart-no-axes-combined") + Object.entries(categories).map(([id, [name, symbol]]) => button(id, name, symbol)).join("") +
      '<div class="drawing-rail-separator"></div>' + button("undo", "Undo", "undo-2") + button("redo", "Redo", "redo-2") +
      '<div class="drawing-rail-separator"></div>' + button("measure", "Measure bars, time and percent", "ruler") +
      button("zoom-in", "Zoom in", "zoom-in") + button("zoom-out", "Zoom out", "zoom-out") +
      button("magnet", "Magnet mode", "magnet") + button("lock", "Lock drawings", "lock-keyhole") +
      button("visibility", "Hide options", "eye") + button("remove", "Remove options", "trash-2");
    const menu = document.createElement("div"); menu.className = "drawing-tool-menu"; menu.hidden = true;
    menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", "Line tools");
    function buildMenu(category) {
      let group = "";
      menu.setAttribute("aria-label", categories[category][0]);
      menu.innerHTML = tools.filter(([id]) => categoryOf(id) === category).map(([id, name, symbol, , section, shortcut]) => {
      const heading = section !== group ? `<div class="drawing-menu-heading">${section}</div>` : ""; group = section;
      return heading + `<button type="button" role="menuitem" data-tool="${id}">${icon(symbol)}<span>${name}</span>${shortcut ? `<small>Alt + ${shortcut.toUpperCase()}</small>` : ""}</button>`;
      }).join("");
      root.lucide.createIcons();
      menu.scrollTop = 0;
    }
    buildMenu("lines");
    const canvas = document.createElement("canvas"); canvas.className = "drawing-canvas";
    canvas.setAttribute("aria-hidden", "true");
    const style = document.createElement("div"); style.className = "drawing-style"; style.hidden = true;
    style.innerHTML = '<input type="color" value="#58c4dc" aria-label="Drawing color" title="Drawing color"><select aria-label="Line width" title="Line width"><option value="1">1 px</option><option value="2" selected>2 px</option><option value="3">3 px</option><option value="4">4 px</option></select><input class="drawing-levels" type="text" aria-label="Fibonacci levels" title="Fibonacci ratios, comma-separated" hidden><input class="drawing-unit" type="number" min="0.000001" step="any" aria-label="Gann price per bar" title="Gann fan: price units per bar (blank = anchor slope)" placeholder="Auto" hidden>' + button("item-lock", "Lock selected drawing", "lock-keyhole") + button("delete", "Delete selected drawing", "trash-2");
    wrap.append(rail, canvas, style);
    const textInput = document.createElement("input"); textInput.className = "drawing-text";
    textInput.type = "text"; textInput.maxLength = 120; textInput.setAttribute("aria-label", "Annotation text");
    style.prepend(textInput);
    const emojiInput = document.createElement("select"); emojiInput.className = "drawing-emoji";
    emojiInput.setAttribute("aria-label", "Emoji marker");
    for (const [value, name] of [["\u{1F642}", "Smile"], ["\u{1F680}", "Rocket"], ["\u{1F4A1}", "Idea"], ["\u{2764}", "Heart"], ["\u{1F525}", "Fire"], ["\u{2B50}", "Star"], ["\u{1F6A9}", "Flag"], ["\u{2705}", "Check"], ["\u{274C}", "Cross"], ["\u{1F440}", "Eyes"]]) {
      const option = document.createElement("option"); option.value = value; option.textContent = value + " " + name; emojiInput.append(option);
    }
    style.prepend(emojiInput);
    wrap.append(menu);
    wrap.tabIndex = 0;
    const grip = document.createElement("button");
    grip.type = "button"; grip.className = "drawing-style-grip";
    grip.title = "Move drawing settings"; grip.setAttribute("aria-label", grip.title);
    grip.innerHTML = '<i data-lucide="grip-vertical"></i>'; style.prepend(grip);
    root.lucide.createIcons();
    const menuActions = [...Object.keys(categories), "magnet", "visibility", "remove"];
    for (const id of menuActions) rail.querySelector('[data-action="' + id + '"]').setAttribute("aria-expanded", "false");
    const settingsKey = root.ChartAccounts?.key("zgua-drawing-controls-v1:" + (pane.index || 0)) || "zgua-drawing-controls-v1:" + (pane.index || 0);
    const settings = { magnet: "off", snapIndicators: false, removeLocked: false, lockNew: false, hideDrawings: false, hideIndicators: false, hidePositions: false };
    try {
      const saved = JSON.parse(localStorage.getItem(settingsKey) || "{}");
      for (const key of Object.keys(settings)) if (typeof settings[key] === "boolean") settings[key] = saved[key] === true;
      if (["off", "weak", "strong"].includes(saved.magnet)) settings.magnet = saved.magnet;
    } catch { /* Default controls remain usable when storage is unavailable. */ }
    const ctx = canvas.getContext("2d"), abort = new AbortController();
    const on = (target, event, handler, capture = false) => target.addEventListener(event, handler, { signal: abort.signal, capture });
    let toolbarDrag, toolbarSelected;
    function clampStyle(x,y) {
      const rect=wrap.getBoundingClientRect(), left=Math.max(50,8-rect.left), top=Math.max(8,8-rect.top);
      const right=Math.min(wrap.clientWidth-(pane.chart.priceScale('right').width()||96),innerWidth-rect.left)-8;
      const bottom=Math.min(wrap.clientHeight,innerHeight-rect.top)-8;
      style.style.transform='none';style.style.maxWidth=Math.max(32,right-left)+'px';style.style.maxHeight=Math.max(32,bottom-top)+'px';
      style.style.left=Math.max(left,Math.min(right-style.offsetWidth,x))+'px';
      style.style.top=Math.max(top,Math.min(bottom-style.offsetHeight,y))+'px';
    }
    function positionStyle(item) {
      if(current()?.id!==item.id||style.hidden)return;
      const point=screen(item.points[0]), rect=wrap.getBoundingClientRect(), chartRect=pane.chartEl.getBoundingClientRect();
      const x=(Number.isFinite(point.x)?point.x:100)+chartRect.left-rect.left;
      const y=(Number.isFinite(point.y)?point.y:80)+chartRect.top-rect.top;
      const above=y-style.offsetHeight-10;
      clampStyle(x-style.offsetWidth/2,above>=8?above:y+10);
    }
    on(grip, "pointerdown", (event) => {
      event.preventDefault(); event.stopPropagation();
      const rect = style.getBoundingClientRect(), parent = wrap.getBoundingClientRect();
      toolbarDrag = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      style.style.transform = "none"; style.style.left = (rect.left - parent.left) + "px";
      style.style.top = (rect.top - parent.top) + "px"; grip.setPointerCapture(event.pointerId);
    });
    on(grip, "pointermove", (event) => {
      if (!toolbarDrag) return;
      const rect = wrap.getBoundingClientRect();
      clampStyle(event.clientX-rect.left-toolbarDrag.x,event.clientY-rect.top-toolbarDrag.y);
    });
    const finishToolbarDrag = () => { toolbarDrag = null; };
    on(grip, "pointerup", finishToolbarDrag); on(grip, "pointercancel", finishToolbarDrag);
    let drawings = [], selected = null, draft = [], tool = "pointer", hover = null, drag = null;
    let hidden = settings.hideDrawings, key = "", undo = [], redo = [], hits = [], frame, lastPaint = 0, width = 0, height = 0;
    let measurement = null, measurePointer = null, activeMenu = null;
    let color = "#58c4dc", lineWidth = 2;
    const marketKey = () => {
      const name = `zgua-drawings-v1:${pane.sourceSelect.value}:${pane.symbolSelect.value}:${pane.timeframeSelect.value}`;
      return root.ChartAccounts?.key(name) || name;
    };
    const persist = (targetKey = key) => {
      try {
        const value = JSON.stringify(drawings);
        localStorage.setItem(targetKey, value);
        root.ChartAccounts?.changed();
        window.dispatchEvent(new CustomEvent("zgua-drawings-changed", { detail: { key: targetKey, value, origin: rail } }));
      } catch (e) { console.warn("Drawing storage unavailable", e); }
    };
    const saveSettings = () => { settings.hideDrawings = hidden; try { localStorage.setItem(settingsKey, JSON.stringify(settings)); root.ChartAccounts?.changed(); } catch { /* Session controls still work. */ } };
    const snapshot = (includeIndicators = false) => JSON.stringify({ drawings, lockNew: settings.lockNew, ...(includeIndicators ? { indicators: options.getIndicators?.() || [] } : {}) });
    const remember = (includeIndicators = false) => { undo.push(snapshot(includeIndicators)); if (undo.length > 100) undo.shift(); redo = []; };
    const restoreSnapshot = (value) => { const saved = JSON.parse(value); drawings = saved.drawings; settings.lockNew = saved.lockNew; saveSettings(); if (saved.indicators) options.setIndicators?.(saved.indicators); };
    const current = () => drawings.find((item) => item.id === selected);
    const closeMenu = () => { menu.hidden = true; activeMenu = null; for (const category of menuActions) rail.querySelector(`[data-action="${category}"]`).setAttribute("aria-expanded", "false"); };
    const selectTool = (id) => {
      if (drawingPointer !== null && wrap.hasPointerCapture(drawingPointer)) wrap.releasePointerCapture(drawingPointer);
      if (measurePointer !== null && wrap.hasPointerCapture(measurePointer)) wrap.releasePointerCapture(measurePointer);
      drawingPointer = null; measurePointer = null; measurement = null; tool = id; draft = []; hover = null; closeMenu(); update(); paint();
    };
    function receive(value) {
      if (drag || draft.length) return;
      try { const next = JSON.parse(value || "[]"); if (!Array.isArray(next)) return; drawings = next; }
      catch { return; }
      if (!current()) selected = null;
      undo = []; redo = []; update(); paint();
    }
    on(window, "zgua-drawings-changed", (e) => { if (e.detail.key === key && e.detail.origin !== rail) receive(e.detail.value); });
    on(window, "storage", (e) => { if (e.key === key) receive(e.newValue); });
    function update() {
      if ((tool !== "pointer" || selected || drag) && pane.markerDetailEl) pane.markerDetailEl.hidden = true;
      rail.querySelector('[data-action="pointer"]').classList.toggle("is-active", tool === "pointer");
      rail.querySelector('[data-action="pointer"]').setAttribute("aria-pressed", String(tool === "pointer"));
      for (const id of Object.keys(categories)) rail.querySelector('[data-action="' + id + '"]').classList.toggle("is-active", categoryOf(tool) === id);
      rail.querySelector('[data-action="undo"]').disabled = !undo.length;
      rail.querySelector('[data-action="redo"]').disabled = !redo.length;
      for (const [id, active] of [["measure", tool === "measure"], ["magnet", settings.magnet !== "off"], ["lock", drawings.length ? drawings.every((d) => d.locked) : settings.lockNew]]) {
        const button = rail.querySelector(`[data-action="${id}"]`);
        button.setAttribute("aria-pressed", String(active)); button.classList.toggle("is-active", active);
      }
      rail.querySelector('[data-action="visibility"]').setAttribute("aria-pressed", String(hidden || settings.hideIndicators || settings.hidePositions));
      style.hidden = !selected || hidden;
      const item = current(), levelsInput = style.querySelector(".drawing-levels"), unitInput = style.querySelector(".drawing-unit");
      style.querySelectorAll("input,select").forEach((input) => { input.disabled = !!item?.locked; });
      style.querySelector('[data-action="delete"]').disabled = !item || (item.locked && !settings.removeLocked);
      style.querySelector('[data-action="item-lock"]').setAttribute("aria-pressed", String(!!item?.locked));
      const lockButton = rail.querySelector('[data-action="lock"]');
      lockButton.title = lockButton.getAttribute("aria-pressed") === "true" ? "Unlock drawings" : "Lock drawings";
      style.querySelector('[data-action="item-lock"]').title = item?.locked ? "Unlock selected drawing" : "Lock selected drawing";
      levelsInput.hidden = !item || !fibonacciTool(item.type) || item.type === "fib-spiral";
      unitInput.hidden = item?.type !== "gann-fan";
      textInput.hidden = !item || !annotation(item.type) || item.type === "emoji";
      emojiInput.hidden = item?.type !== "emoji";
      if (item) {
        style.querySelector('input[type="color"]').value = item.color; style.querySelector('select[aria-label="Line width"]').value = item.width;
        textInput.value = item.text || ""; emojiInput.value = item.text || "\u{1F642}";
        levelsInput.value = (item.levels || defaultLevels(item.type)).join(","); unitInput.value = item.unit || "";
      }
      wrap.style.cursor = tool === "pointer" ? "" : "crosshair";
      if(item&&toolbarSelected!==item.id){toolbarSelected=item.id;requestAnimationFrame(()=>positionStyle(item));}else if(!item)toolbarSelected=null;
      menu.querySelectorAll("[data-tool]").forEach((el) => el.classList.toggle("is-active", el.dataset.tool === tool));
    }
    function refreshMarket() {
      const next = marketKey();
      if (key === next) return;
      const previous = key; key = next;
      if (previous) persist(previous);
      try { const stored = JSON.parse(localStorage.getItem(key) || "[]"); drawings = Array.isArray(stored) ? stored.filter((d) => tools.some((t) => t[0] === d.type) && Array.isArray(d.points) && d.points.every((p) => Number.isFinite(p.time) && Number.isFinite(p.price)) && /^#[0-9a-f]{6}$/i.test(d.color) && [1, 2, 3, 4].includes(d.width)) : []; }
      catch { drawings = []; }
      selected = null; draft = []; measurement = null; undo = []; redo = []; update();
    }
    function step() { const candles = pane.candles || []; return candles.length > 1 ? candles[candles.length - 1].time - candles[candles.length - 2].time : 60; }
    function logicalTime(index) {
      const candles = pane.candles || []; if (!candles.length) return null;
      const lo = Math.floor(index), fraction = index - lo;
      const at = (i) => i < 0 ? candles[0].time + i * step() : i >= candles.length ? candles[candles.length - 1].time + (i - candles.length + 1) * step() : candles[i].time;
      return at(lo) + fraction * (at(lo + 1) - at(lo));
    }
    function timeLogical(time) {
      const candles = pane.candles || []; if (!candles.length) return null;
      if (time < candles[0].time) return (time - candles[0].time) / step();
      if (time >= candles[candles.length - 1].time) return candles.length - 1 + (time - candles[candles.length - 1].time) / step();
      let lo = 0, hi = candles.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (candles[mid].time <= time) lo = mid; else hi = mid; }
      return lo + (time - candles[lo].time) / (candles[hi].time - candles[lo].time);
    }
    function screen(p) { return { x: pane.chart.timeScale().logicalToCoordinate(timeLogical(p.time)), y: pane.series.priceToCoordinate(p.price) }; }
    function anchor(p, shouldSnap = true) {
      const logical = pane.chart.timeScale().coordinateToLogical(p.x), price = pane.series.coordinateToPrice(p.y);
      const time = logical === null ? null : logicalTime(logical);
      if (time === null || price === null) return null;
      const raw = { time, price }, index = Math.round(logical), candle = pane.candles?.[index];
      if (!shouldSnap || settings.magnet === "off" || !candle) return raw;
      const values = [candle.open, candle.high, candle.low, candle.close];
      if (settings.snapIndicators && !settings.hideIndicators) for (const series of pane.overlaySeries || []) {
        if (typeof series.dataByIndex === "function") values.push(series.dataByIndex(index)?.value);
      }
      const candidates = values.filter(Number.isFinite).map((value) => ({ ...screen({ time: candle.time, price: value }), time: candle.time, price: value }));
      const snapped = snapPoint(p, candidates, settings.magnet);
      return snapped ? { time: snapped.time, price: snapped.price } : raw;
    }
    const position = (event) => { const r = pane.chartEl.getBoundingClientRect(); return { x: event.clientX - r.left, y: event.clientY - r.top }; };
    function draw(item, preview = false) {
      const points = item.points.map(screen);
      if (item.type === "gann-fixed" && points.length > 1 && item.fixedSize) points[1] = shifted(points[0], item.fixedSize.x, item.fixedSize.y);
      if (points.some((p) => p.x === null || p.y === null || !Number.isFinite(p.x + p.y))) return;
      const samples = item.type === "regression" && item.points.length > 1 ? (pane.candles || []).filter((c) => c.time >= Math.min(...item.points.map((p) => p.time)) && c.time <= Math.max(...item.points.map((p) => p.time))).map((c) => screen({ time: c.time, price: c.close })) : [];
      const worldGeometry = ["fib-retracement", "fib-extension", "fib-channel", "fib-time-zone", "fib-time", "fib-fan", "pitchfan", "gann-fan", "gann-box", "gann-square"].includes(item.type);
      const worldPoints = item.points.map((p) => ({ x: timeLogical(p.time), y: p.price }));
      const shape = geometry(item.type, worldGeometry ? worldPoints : points, samples, item);
      if (worldGeometry) {
        const project = (p) => ({ x: pane.chart.timeScale().logicalToCoordinate(p.x), y: pane.series.priceToCoordinate(p.y) });
        shape.lines = shape.lines.map((s) => ({ ...s, a: project(s.a), b: project(s.b) })).filter((s) => [s.a.x, s.a.y, s.b.x, s.b.y].every(Number.isFinite));
        shape.fills = shape.fills.map((polygon) => polygon.map(project)).filter((polygon) => polygon.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)));
        shape.labels = (shape.labels || []).map((l) => ({ ...l, point: project(l.point) })).filter((l) => Number.isFinite(l.point.x) && Number.isFinite(l.point.y));
      }
      ctx.strokeStyle = item.color; ctx.lineWidth = item.type === "highlighter" ? item.width * 6 : item.width; ctx.globalAlpha = item.type === "highlighter" ? 0.3 : preview ? 0.65 : 1;
      for (const [index, polygon] of shape.fills.entries()) { ctx.beginPath(); polygon.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath(); ctx.fillStyle = ["long-position", "short-position"].includes(item.type) ? index === 0 ? "#ff5c7a25" : "#12b88625" : item.color + "15"; ctx.fill(); }
      for (const segment of shape.lines) {
        const clipped = clipLine(segment.a, segment.b, segment.mode, width, height); if (!clipped) continue;
        ctx.setLineDash(segment.dashed ? [5, 4] : []); ctx.beginPath(); ctx.moveTo(clipped[0].x, clipped[0].y); ctx.lineTo(clipped[1].x, clipped[1].y); ctx.stroke();
        if (!preview) hits.push({ id: item.id, a: clipped[0], b: clipped[1] });
      }
      ctx.setLineDash([]);
      for (const entry of shape.labels || []) {
        if (entry.point.x >= 0 && entry.point.x <= width && entry.point.y >= 0 && entry.point.y <= height) {
          const text = ["price-note", "price-label"].includes(item.type) ? item.points[0].price.toFixed(2) + (item.text ? " " + item.text : "") : entry.text;
          const bounds = label(text, entry.point);
          if (!preview) hits.push({ id: item.id, ...bounds });
        }
      }
      if (["price-range", "date-range", "date-price-range", "long-position", "short-position"].includes(item.type) && item.points.length >= 2) {
        const [a, b, c] = item.points, delta = b.price - a.price;
        let text = item.type === "date-range" ? "" : delta.toFixed(2) + " (" + (a.price ? delta / a.price * 100 : 0).toFixed(2) + "%)";
        if (item.type !== "price-range" && !c) {
          const stats = measurementStats(a, b, timeLogical(b.time) - timeLogical(a.time));
          text += "  " + stats.bars + " bars / " + stats.duration;
        }
        if (c) {
          const long = item.type === "long-position", risk = Math.abs(a.price - b.price), reward = Math.abs(c.price - a.price);
          const valid = long ? b.price < a.price && c.price > a.price : b.price > a.price && c.price < a.price;
          text = valid && risk > 0 ? "Entry " + a.price.toFixed(2) + " / Stop " + b.price.toFixed(2) + " / Target " + c.price.toFixed(2) + " / R:R " + (reward / risk).toFixed(2) : "Stop / target on wrong side of entry";
        }
        const center = midpoint(points[0], points[1]);
        if (tool === "measure" && item === measurement) {
          center.y = Math.max(28, Math.min(center.y, height - 28));
          const stats = measurementStats(a, b, timeLogical(b.time) - timeLogical(a.time));
          label(`${stats.delta.toFixed(2)} (${stats.percent.toFixed(2)}%)`, { x: center.x, y: center.y - 12 });
          label(`${stats.bars} bars / ${stats.duration}`, { x: center.x, y: center.y + 12 });
        } else label(text.trim(), center);
      }
      if (item.type === "info" && points.length > 1) {
        const a = item.points[0], b = item.points[1]; const delta = b.price - a.price;
        label(`${delta.toFixed(2)} (${a.price ? (delta / a.price * 100).toFixed(2) : "0"}%)  |  ${Math.round(Math.abs(timeLogical(b.time) - timeLogical(a.time)))} bars  |  ${(Math.abs(b.time - a.time) / 3600).toFixed(1)}h`, midpoint(points[0], points[1]));
      }
      if (item.type === "angle" && points.length > 1) {
        const [a, b] = points, angle = Math.atan2(a.y - b.y, b.x - a.x);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(a.x + 38, a.y); ctx.stroke();
        ctx.beginPath(); ctx.arc(a.x, a.y, 28, Math.min(0, -angle), Math.max(0, -angle)); ctx.stroke();
        label(`${(angle * 180 / Math.PI).toFixed(1)} deg`, { x: a.x + 40, y: a.y - 12 });
      }
      if (item.type === "regression" && samples.length < 2) label("No candle samples", points[0]);
      if (item.id === selected || preview) points.forEach((p, index) => {
        if (freehand(item.type) && index !== 0 && index !== points.length - 1) return;
        ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx.fillStyle = "#11161d"; ctx.fill(); ctx.stroke();
        if (!preview) hits.push({ id: item.id, anchor: index, a: p, b: p });
      });
      ctx.globalAlpha = 1;
    }
    function label(text, p) {
      ctx.font = "11px sans-serif";
      const max = Math.max(12, width - 12); text = String(text);
      while (ctx.measureText(text).width > max && text.length > 1) text = text.slice(0, -1);
      const w = ctx.measureText(text).width + 12;
      const x = Math.max(0, Math.min(p.x, width - w)), y = Math.max(16, Math.min(p.y, height - 4));
      ctx.fillStyle = "#202a35"; ctx.fillRect(x, y - 15, w, 20); ctx.fillStyle = "#e4eaf0"; ctx.fillText(text, x + 6, y - 1);
      return { box: { x, y: y - 15, width: w, height: 20 } };
    }
    function paint() {
      refreshMarket();
      const w = pane.chartEl.clientWidth, h = pane.chartEl.clientHeight, ratio = window.devicePixelRatio || 1;
      if (!w || !h) return;
      if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) { canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio); }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, w, h);
      width = pane.chart.timeScale().width(); height = pane.chart.panes?.()[0]?.getHeight?.() || h - 26;
      hits = [];
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, width, height); ctx.clip();
      if (!hidden) drawings.forEach((item) => draw(item));
      if (!hidden && draft.length) { const count = tools.find((t) => t[0] === tool)?.[3] || 1; draw({ type: tool, points: hover && (variablePath(tool) || draft.length < count) ? [...draft, hover] : draft, color, width: lineWidth }, true); }
      if (measurement) draw(measurement, true);
      ctx.restore();
    }
    const hit = (p) => [...hits].reverse().find((h) => h.box ? p.x >= h.box.x && p.x <= h.box.x + h.box.width && p.y >= h.box.y && p.y <= h.box.y + h.box.height : distance(p, h.a, h.b) < (h.anchor === undefined ? 6 : 9));
    function positionMenu() {
      const rect = rail.getBoundingClientRect(), menuHeight = Math.min(menu.scrollHeight + 10, 720, window.innerHeight - 20);
      menu.style.left = `${Math.max(6, Math.min(rect.right + 4, window.innerWidth - menu.offsetWidth - 6))}px`;
      menu.style.top = `${Math.max(10, Math.min(rect.top + 5, window.innerHeight - menuHeight - 10))}px`;
      menu.style.maxHeight = `${menuHeight}px`;
    }
    on(window, "resize", () => { if (!menu.hidden) positionMenu(); });
    function refreshVisibility() {
      pane.node.classList.toggle("drawing-hide-indicators", settings.hideIndicators);
      for (const series of pane.overlaySeries || []) {
        const visible = !settings.hideIndicators && !pane.hiddenIndicators?.has(series.indicatorId);
        series.applyOptions({ visible });
        for (const child of series.indicatorSegments || []) child.applyOptions({ visible });
      }
      for (const series of pane.backtestOverlaySeries || []) series.applyOptions({ visible: !settings.hidePositions && pane.signalMarkerToggle?.checked !== false && !pane.replayActive && Boolean(pane.presetSelect?.value) && pane.presetSelect.value !== 'none' });
      pane.drawingPositionsHidden = settings.hidePositions;
      if (settings.hidePositions && pane.markerDetailEl) pane.markerDetailEl.hidden = true;
      options.onVisibilityChange?.();
    }
    function buildControlMenu(name) {
      menu.replaceChildren();
      menu.setAttribute("aria-label", { magnet: "Magnet modes", visibility: "Visibility options", remove: "Remove options" }[name]);
      const row = (label, control, checked, disabled = false) => {
        const button = document.createElement("button"); button.type = "button";
        const symbol = document.createElement("i"); symbol.dataset.lucide = name === "magnet" ? "magnet" : name === "visibility" ? "eye" : "trash-2";
        const text = document.createElement("span"); text.textContent = label; button.append(symbol, text);
        button.dataset.control = control; button.disabled = disabled;
        button.setAttribute("role", checked === undefined ? "menuitem" : name === "magnet" ? "menuitemradio" : "menuitemcheckbox");
        if (checked !== undefined) { button.setAttribute("aria-checked", String(checked)); button.classList.toggle("is-active", checked); }
        menu.append(button);
      };
      const setting = (label, key) => {
        const el = document.createElement("label"); el.className = "drawing-control-setting";
        const input = document.createElement("input"); input.type = "checkbox"; input.checked = settings[key]; input.dataset.setting = key;
        el.append(input, document.createTextNode(label)); menu.append(el);
      };
      if (name === "magnet") {
        for (const [value, label] of [["off", "Magnet off"], ["weak", "Weak magnet"], ["strong", "Strong magnet"]]) row(label, "magnet-" + value, settings.magnet === value);
        setting("Snap to indicators", "snapIndicators");
      } else if (name === "visibility") {
        row("Hide drawings", "hide-drawings", hidden);
        row("Hide indicators", "hide-indicators", settings.hideIndicators);
        row("Hide positions and orders", "hide-positions", settings.hidePositions);
        row("Hide all", "hide-all", hidden && settings.hideIndicators && settings.hidePositions);
      } else {
        const n = removableDrawings(drawings, settings.removeLocked).length, i = options.getIndicators?.().length || 0;
        row(`Remove ${n} drawings`, "remove-drawings", undefined, !n);
        row(`Remove ${i} indicators`, "remove-indicators", undefined, !i);
        row(`Remove ${n} drawings & ${i} indicators`, "remove-all", undefined, !n && !i);
        setting("Always remove locked drawings", "removeLocked");
      }
      root.lucide.createIcons();
    }
    function control(name) {
      if (name.startsWith("magnet-")) settings.magnet = name.slice(7);
      if (name.startsWith("hide-")) {
        if (name === "hide-all") hidden = settings.hideIndicators = settings.hidePositions = !(hidden && settings.hideIndicators && settings.hidePositions);
        if (name === "hide-drawings") hidden = !hidden;
        if (name === "hide-indicators") settings.hideIndicators = !settings.hideIndicators;
        if (name === "hide-positions") settings.hidePositions = !settings.hidePositions;
        selected = null; selectTool("pointer"); refreshVisibility();
      }
      if (name.startsWith("remove-")) {
        const indicators = name !== "remove-drawings";
        remember(indicators);
        if (name !== "remove-indicators") drawings = drawings.filter((item) => item.locked && !settings.removeLocked);
        if (indicators) options.setIndicators?.([]);
        selected = null; persist();
      }
      saveSettings(); closeMenu(); update(); paint();
    }
    function action(name) {
      if (name === "indicators") { closeMenu(); options.onIndicators?.(); }
      if (name === "pointer") selectTool("pointer");
      if (menuActions.includes(name)) {
        const same = rail.querySelector(`[data-action="${name}"]`).getAttribute("aria-expanded") === "true";
        closeMenu();
        if (!same) { if (categories[name]) buildMenu(name); else buildControlMenu(name); activeMenu = name; menu.hidden = false; }
        positionMenu();
        rail.querySelector(`[data-action="${name}"]`).setAttribute("aria-expanded", String(!menu.hidden));
      }
      if (name === "delete" && current() && (!current().locked || settings.removeLocked)) { remember(); drawings = drawings.filter((d) => d.id !== selected); selected = null; persist(); }
      if (name === "undo" && undo.length) { const entry = undo.pop(); redo.push(snapshot(Array.isArray(JSON.parse(entry).indicators))); restoreSnapshot(entry); selected = null; persist(); }
      if (name === "redo" && redo.length) { const entry = redo.pop(); undo.push(snapshot(Array.isArray(JSON.parse(entry).indicators))); restoreSnapshot(entry); selected = null; persist(); }
      if (name === "measure") { selected = null; selectTool(tool === "measure" ? "pointer" : "measure"); }
      if (name === "zoom-in" || name === "zoom-out") { const range = zoomRange(pane.chart.timeScale().getVisibleLogicalRange(), name === "zoom-in" ? 0.7 : 1 / 0.7); if (range) pane.chart.timeScale().setVisibleLogicalRange(range); }
      if (name === "lock") {
        const locked = !(drawings.length ? drawings.every((item) => item.locked) : settings.lockNew);
        remember(); drawings.forEach((item) => { item.locked = locked; }); settings.lockNew = locked;
        selected = null; saveSettings(); persist();
      }
      if (name === "item-lock" && current()) { remember(); current().locked = !current().locked; persist(); }
      update(); paint();
    }
    on(rail, "click", (e) => { const b = e.target.closest("[data-action]"); if (b) action(b.dataset.action); });
    on(style, "click", (e) => { const button = e.target.closest("[data-action]"); if (button) action(button.dataset.action); });
    function changeStyle(e) {
      const item = current(); if (!item || item.locked) return;
      if (e.target.matches(".drawing-levels")) {
        const raw = e.target.value.split(",").map((s) => s.trim());
        const levels = raw.map(Number);
        if (raw.some((s) => !s) || levels.length > 24 || !levels.length || levels.some((r) => !Number.isFinite(r) || r < 0 || r > 100)) { e.target.setCustomValidity("Enter up to 24 ratios between 0 and 100, separated by commas."); e.target.reportValidity(); return; }
        e.target.setCustomValidity(""); remember(); item.levels = [...new Set(levels)].sort((a, b) => a - b);
      } else if (e.target.matches(".drawing-unit")) {
        const unit = e.target.value === "" ? null : Number(e.target.value);
        if (unit !== null && (!Number.isFinite(unit) || unit <= 0)) { e.target.setCustomValidity("Price units per bar must be positive."); e.target.reportValidity(); return; }
        e.target.setCustomValidity(""); remember(); item.unit = unit;
      } else if (e.target.matches(".drawing-text,.drawing-emoji")) { remember(); item.text = e.target.value.slice(0, 120); }
      else { remember(); if (e.target.matches('input[type="color"]')) color = e.target.value; else lineWidth = Number(e.target.value); item.color = color; item.width = lineWidth; }
      persist(); update(); paint();
    }
    on(style, "change", changeStyle);
    on(style, "keydown", (e) => { if (e.key === "Enter" && e.target.matches(".drawing-levels,.drawing-unit,.drawing-text")) { e.preventDefault(); changeStyle(e); } });
    on(menu, "click", (e) => {
      const b = e.target.closest("[data-tool]"); if (b) { hidden = false; saveSettings(); selected = null; selectTool(b.dataset.tool); }
      const button = e.target.closest("[data-control]"); if (button) control(button.dataset.control);
    });
    on(menu, "change", (e) => { const key = e.target.dataset.setting; if (!["snapIndicators", "removeLocked"].includes(key)) return; settings[key] = e.target.checked; saveSettings(); buildControlMenu(activeMenu); positionMenu(); update(); });
    on(document, "pointerdown", (e) => { if (!wrap.contains(e.target) && !menu.contains(e.target)) closeMenu(); });
    let drawingPointer = null;
    function finishDrawing() {
      if (!draft.length || (tools.find((t) => t[0] === tool)?.[3] === 0 && draft.length < 2)) { draft = []; selectTool("pointer"); return; }
      remember(); const item = { id: crypto.randomUUID(), type: tool, points: draft, color, width: lineWidth, locked: settings.lockNew };
      if (annotation(tool)) item.text = tool === "emoji" ? "\u{1F642}" : ["price-note", "price-label"].includes(tool) ? "" : tools.find((t) => t[0] === tool)[1];
      if (fibonacciTool(tool)) item.levels = [...defaultLevels(tool)];
      if (tool === "gann-fixed") {
        const [a, b] = draft.map(screen), size = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        item.fixedSize = { x: Math.sign(b.x - a.x || 1) * size, y: Math.sign(b.y - a.y || 1) * size };
      }
      drawings.push(item); selected = item.id; draft = []; persist(); selectTool("pointer");
    }
    on(wrap, "pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button,input,select,.drawing-tool-menu,.drawing-style")) return;
      closeMenu();
      paint(); const p = position(e); if (p.x < 0 || p.y < 0 || p.x > width || p.y > height) return;
      const point = anchor(p); if (!point) return;
      if (tool !== "pointer") {
        e.preventDefault(); e.stopImmediatePropagation();
        if (tool === "measure") { measurement = { type: "date-price-range", points: [point, point], color, width: 1 }; measurePointer = e.pointerId; wrap.setPointerCapture(e.pointerId); }
        else if (freehand(tool)) { draft = [anchor(p, false)]; drawingPointer = e.pointerId; wrap.setPointerCapture(e.pointerId); }
        else {
        draft.push(point); hover = null;
        if (draft.length === tools.find((t) => t[0] === tool)[3]) {
          finishDrawing();
        }
        }
      } else {
        const target = hidden ? null : hit(p);
        selected = target?.id || null;
        if (selected) wrap.focus({ preventScroll: true });
        if (target && !current().locked) { e.preventDefault(); e.stopImmediatePropagation(); remember(); drag = { ...target, start: p, original: structuredClone(current().points) }; wrap.setPointerCapture(e.pointerId); }
      }
      update(); paint();
    }, true);
    on(wrap, "pointermove", (e) => {
      const p = position(e);
      if (measurePointer !== null) {
        e.preventDefault(); e.stopImmediatePropagation(); const point = anchor(p); if (point) measurement.points[1] = point; paint();
      } else if (drawingPointer !== null) {
        e.preventDefault(); e.stopImmediatePropagation();
        const point = anchor(p, false), previous = draft.at(-1);
        if (point && draft.length < 2000 && Math.hypot(p.x - screen(previous).x, p.y - screen(previous).y) >= 2) draft.push(point);
        paint();
      } else if (drag) {
        e.preventDefault(); e.stopImmediatePropagation();
        const item = current(); if (!item) return;
        if (drag.anchor !== undefined) {
          const point = anchor(p); if (point) item.points[drag.anchor] = point;
          if (item.type === "gann-fixed" && drag.anchor === 1) { const a = screen(item.points[0]), size = Math.max(Math.abs(p.x - a.x), Math.abs(p.y - a.y)); item.fixedSize = { x: Math.sign(p.x - a.x || 1) * size, y: Math.sign(p.y - a.y || 1) * size }; }
        }
        else item.points = drag.original.map((original) => { const sp = screen(original); return anchor(shifted(sp, p.x - drag.start.x, p.y - drag.start.y), false) || original; });
        paint();
      } else if (draft.length) { hover = anchor(p); paint(); }
    }, true);
    const endDrag = (e) => {
      if (measurePointer !== null) { measurePointer = null; if (e.type === "pointercancel") measurement = null; }
      if (drawingPointer !== null) { drawingPointer = null; if (e.type === "pointercancel") { draft = []; selectTool("pointer"); } else finishDrawing(); }
      if (drag) { drag = null; persist(); }
      if (wrap.hasPointerCapture(e.pointerId)) wrap.releasePointerCapture(e.pointerId);
    };
    on(wrap, "pointerup", endDrag, true); on(wrap, "pointercancel", endDrag, true);
    on(wrap, "dblclick", (e) => {
      if (!variablePath(tool) || e.target.closest("button,input,select")) return;
      e.preventDefault(); e.stopImmediatePropagation();
      if (draft.length > 1) { const a = screen(draft.at(-1)), b = screen(draft.at(-2)); if (Math.hypot(a.x - b.x, a.y - b.y) < 2) draft.pop(); }
      finishDrawing();
    }, true);
    on(document, "keydown", (e) => {
      if (!wrap.closest(".pane")?.contains(document.activeElement) && !wrap.matches(":hover")) return;
      if (e.target.closest("input,select,textarea,[contenteditable=true]")) return;
      if (e.key === "Escape") { selected = null; selectTool("pointer"); }
      if (e.key === "Enter" && variablePath(tool)) { e.preventDefault(); finishDrawing(); }
      if (e.key === "Delete" || e.key === "Backspace") { if (selected) { e.preventDefault(); action("delete"); } }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); action(e.shiftKey ? "redo" : "undo"); }
      if (e.altKey) { const choice = tools.find((t) => t[5] === e.key.toLowerCase()); if (choice) { e.preventDefault(); selectTool(choice[0]); } }
    });
    let lastView = "";
    function tick(now) {
      if (now - lastPaint > 100) {
        const view = JSON.stringify([marketKey(), pane.chartEl.clientWidth, pane.chartEl.clientHeight,
          pane.chart.timeScale().getVisibleLogicalRange(), pane.series.coordinateToPrice(0),
          pane.series.coordinateToPrice(pane.chartEl.clientHeight), pane.candles?.length, pane.candles?.at(-1)?.close]);
        if (view !== lastView) { paint(); if(!style.hidden)clampStyle(parseFloat(style.style.left)||50,parseFloat(style.style.top)||8); lastView = view; }
        lastPaint = now;
      }
      frame = requestAnimationFrame(tick);
    }
    refreshMarket(); refreshVisibility(); update(); frame = requestAnimationFrame(tick);
    return { refreshVisibility, showIndicators() { settings.hideIndicators = false; refreshVisibility(); persist(); }, isInteracting: () => tool !== "pointer" || !!selected || !!drag,
      destroy() { abort.abort(); cancelAnimationFrame(frame); persist(); rail.remove(); menu.remove(); canvas.remove(); style.remove(); wrap.classList.remove("has-drawing-tools"); } };
  }
  const api = { attach, tools, geometry, clipLine, snapPoint, zoomRange, measurementStats, removableDrawings };
  if (typeof module !== "undefined") module.exports = api;
  else root.ChartDrawingTools = api;
})(typeof window !== "undefined" ? window : globalThis);
