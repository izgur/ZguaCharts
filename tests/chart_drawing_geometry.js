const assert = require("assert");
const { tools, geometry, clipLine } = require("../static/chart-drawings");
const points = [{ x: 20, y: 80 }, { x: 80, y: 20 }, { x: 90, y: 90 }, { x: 150, y: 50 }, { x: 180, y: 100 }, { x: 220, y: 60 }, { x: 260, y: 80 }];
assert.equal(tools.length, 74);
for (const [id, , , count] of tools) {
  const result = geometry(id, points.slice(0, count || 4), points);
  assert.ok(result.lines.length > 0, `${id} renders lines`);
  for (const line of result.lines) assert.ok(Number.isFinite(line.a.x + line.a.y + line.b.x + line.b.y));
}
assert.deepEqual(clipLine({ x: 20, y: 40 }, { x: 30, y: 40 }, "extended", 100, 100), [{ x: 0, y: 40 }, { x: 100, y: 40 }]);
assert.deepEqual(clipLine({ x: 20, y: 40 }, { x: 30, y: 40 }, "ray", 100, 100), [{ x: 20, y: 40 }, { x: 100, y: 40 }]);
assert.equal(clipLine({ x: 20, y: 140 }, { x: 30, y: 140 }, "extended", 100, 100), null);
const parallel = geometry("parallel", points.slice(0, 3));
assert.equal(parallel.lines[0].b.y - parallel.lines[0].a.y, parallel.lines[1].b.y - parallel.lines[1].a.y);
const flat = geometry("flat", points.slice(0, 3));
assert.equal(flat.lines[1].a.y, flat.lines[1].b.y);
const regression = geometry("regression", [{ x: 0, y: 10 }, { x: 10, y: 20 }], [{ x: 0, y: 10 }, { x: 5, y: 15 }, { x: 10, y: 20 }]);
assert.equal(regression.lines[0].a.y, 10);
assert.equal(regression.lines[0].b.y, 20);
assert.notDeepEqual(geometry("pitchfork", points), geometry("schiff", points));
assert.notDeepEqual(geometry("schiff", points), geometry("modified-schiff", points));
assert.notDeepEqual(geometry("modified-schiff", points), geometry("inside", points));
const fib = geometry("fib-retracement", [{ x: 0, y: 100 }, { x: 10, y: 200 }]);
assert.equal(fib.lines[0].a.y, 200);
assert.equal(fib.lines[4].a.y, 138.2);
const extension = geometry("fib-extension", [{ x: 0, y: 100 }, { x: 10, y: 200 }, { x: 20, y: 150 }], [], { levels: [0, 1, 1.618] });
assert.equal(extension.lines[1].a.y, 250);
const zone = geometry("fib-time-zone", [{ x: 10, y: 100 }, { x: 20, y: 200 }]);
assert.equal(zone.lines[5].a.x, 90);
const time = geometry("fib-time", [{ x: 10, y: 100 }, { x: 20, y: 200 }, { x: 30, y: 120 }], [], { levels: [1] });
assert.equal(time.lines[0].a.x, 40);
const fan = geometry("gann-fan", [{ x: 0, y: 100 }, { x: 10, y: 200 }], [], { unit: 2 });
assert.equal(fan.lines[4].b.y, 120);
assert.ok(geometry("fib-spiral", points).lines.length >= 300);
for (const [id, , , count] of tools) {
  const shape = geometry(id, points.slice(0, count || 4));
  for (const polygon of shape.fills) polygon.forEach((p) => assert.ok(Number.isFinite(p.x + p.y)));
  for (const entry of shape.labels || []) assert.ok(Number.isFinite(entry.point.x + entry.point.y));
}
assert.equal(geometry("xabcd", points.slice(0, 5)).labels.map((l) => l.text).join(""), "XABCD");
assert.equal(geometry("wave-triple", points.slice(0, 6)).labels.map((l) => l.text).join(""), "0WXYXZ");
assert.equal(geometry("rectangle", points.slice(0, 2)).lines.length, 4);
assert.equal(geometry("long-position", points.slice(0, 3)).fills.length, 2);
assert.equal(geometry("cyclic", points.slice(0, 2)).lines.length, 24);
assert.equal(geometry("text", points.slice(0, 1), [], { text: "<script>" }).labels[0].text, "<script>");
assert.ok(geometry("curve", points.slice(0, 3)).lines.length >= 90);
for (const [id] of tools) for (const p of [[], [points[0]], [points[0], points[0]]]) {
  const shape = geometry(id, p);
  shape.lines.forEach((l) => assert.ok(Number.isFinite(l.a.x + l.a.y + l.b.x + l.b.y), id));
}
console.log("All 74 drawing tools: geometry, patterns, shapes, annotations and clipping checks passed.");
