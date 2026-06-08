const assert = require("assert");
const lab = require("../core/research/regimeFilterCounterfactual");

function frame(count) {
  const rows = [];
  let close = 100;
  for (let index = 0; index < count; index += 1) {
    close += index < 40 ? 0.2 : -0.05;
    rows.push({
      __index: index,
      time: 1700000000 + index * 3600,
      close,
      ema50: close + (index < 70 ? 2 : -2),
      ema200: close,
      atrPct: index < 50 ? 0.5 : 2.5,
      rsi14: index < 80 ? 60 : 40
    });
  }
  return rows;
}

const rows = frame(120);
const classifiedEarly = lab.classifyRegime(rows, rows[45].time);
const classifiedLate = lab.classifyRegime(rows, rows[95].time);

assert.strictEqual(classifiedEarly.momentum, "bullish");
assert.strictEqual(classifiedLate.momentum, "bearish");
assert.ok(classifiedEarly.rowTime <= rows[45].time, "classifier must use at-or-before candle only");
assert.ok(classifiedLate.rowTime <= rows[95].time, "classifier must not use a future row");

const folds = lab.foldSlices(rows, 4);
assert.strictEqual(folds.length, 4);
assert.ok(folds[0].candles[0].time < folds[1].candles[0].time, "folds must be chronological");

const definitions = lab.variantDefinitions();
assert.ok(definitions.some((item) => item.id === "excludeWorstExact"));
assert.ok(definitions.some((item) => item.id === "lowOrMediumVolBullish"));

const weak = lab.learnedWeakDefinition([
  { causalRegime: "a", returnPct: -1 },
  { causalRegime: "a", returnPct: -0.5 },
  { causalRegime: "a", returnPct: -0.25 },
  { causalRegime: "b", returnPct: 2 },
  { causalRegime: "b", returnPct: 1 },
  { causalRegime: "b", returnPct: -0.2 }
], 3);
assert.deepStrictEqual(weak.learnedWeakRegimes, ["a"]);
assert.strictEqual(weak.allow({ regime: "a" }), false);
assert.strictEqual(weak.allow({ regime: "b" }), true);

console.log("regime filter counterfactual smoke tests passed");
