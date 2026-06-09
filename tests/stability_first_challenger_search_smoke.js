const assert = require("assert");

const lab = require("../core/research/stabilityFirstChallengerSearch");

function fold(totalReturnPct, profitFactor, status = "PASS") {
  return {
    status,
    trades: 20,
    totalReturnPct,
    profitFactor,
    maxDrawdownPct: Math.abs(Math.min(totalReturnPct, 0)) + 2,
    winRate: 52
  };
}

function candidateFixture(overrides = {}) {
  const folds = overrides.folds || [
    fold(1.4, 1.25),
    fold(1.1, 1.2),
    fold(0.8, 1.15),
    fold(1.3, 1.22)
  ];
  const walkForward = lab.walkForwardSummary(folds);
  const concentration = lab.returnConcentration(folds);
  const fullPeriod = Object.assign({
    status: "PASS",
    trades: 80,
    totalReturnPct: 5.2,
    profitFactor: 1.32,
    maxDrawdownPct: 8,
    winRate: 53
  }, overrides.fullPeriod || {});
  const candidate = {
    strategy: "SimpleAtrTrendV2",
    symbol: "ETHUSDT",
    timeframe: "1h",
    days: 365,
    antiLookaheadStatus: "PASS",
    fullPeriod,
    walkForward,
    returnConcentration: concentration,
    stress: overrides.stress || { status: "SURVIVES_MODERATE_STRESS" },
    recentWindows: overrides.recentWindows || { status: "RECENTLY_CONSISTENT" },
    reproducibility: overrides.reproducibility || { status: "REPRODUCIBLE" }
  };
  candidate.stabilityScore = lab.stabilityScore(
    candidate.fullPeriod,
    candidate.walkForward,
    candidate.returnConcentration,
    candidate.stress,
    candidate.recentWindows,
    candidate.reproducibility
  );
  return candidate;
}

const slices = lab.foldSlices(Array.from({ length: 10 }, (_, index) => ({ index })), 4);
assert.deepStrictEqual(slices.map((slice) => slice.candles.length), [2, 2, 2, 4], "fold slices should be deterministic and chronological");
assert.strictEqual(slices[0].candles[0].index, 0, "first fold starts at the oldest candle");
assert.strictEqual(slices[3].candles[3].index, 9, "last fold ends at the newest candle");

const stableCandidate = candidateFixture();
const fragileCandidate = candidateFixture({
  folds: [
    fold(13, 2.1),
    fold(-4, 0.65, "FAIL"),
    fold(-3, 0.7, "FAIL"),
    fold(-2, 0.82, "FAIL")
  ],
  fullPeriod: {
    trades: 90,
    totalReturnPct: 18,
    profitFactor: 1.65,
    maxDrawdownPct: 14
  }
});
assert(
  stableCandidate.stabilityScore > fragileCandidate.stabilityScore,
  "stable fold distribution should outrank fragile high-return concentration"
);
assert.strictEqual(fragileCandidate.returnConcentration.classification, "HIGHLY_CONCENTRATED", "fragile candidate should show high return concentration");

const benchmark = candidateFixture({
  folds: [
    fold(1, 1.15),
    fold(-1.3, 0.88, "FAIL"),
    fold(-0.4, 0.95, "FAIL"),
    fold(0.7, 1.05)
  ],
  fullPeriod: {
    trades: 70,
    totalReturnPct: 3,
    profitFactor: 1.18,
    maxDrawdownPct: 10
  }
});
stableCandidate.benchmarkComparison = lab.benchmarkComparison(stableCandidate, benchmark);
stableCandidate.eligibility = lab.eligibility(stableCandidate, benchmark, lab.ELIGIBILITY_GATES);
assert.strictEqual(stableCandidate.eligibility.status, "CHALLENGER_ELIGIBLE", "stable candidate should pass challenger gates versus weaker benchmark");

fragileCandidate.benchmarkComparison = lab.benchmarkComparison(fragileCandidate, benchmark);
fragileCandidate.eligibility = lab.eligibility(fragileCandidate, benchmark, lab.ELIGIBILITY_GATES);
assert.notStrictEqual(fragileCandidate.eligibility.status, "CHALLENGER_ELIGIBLE", "fragile candidate should not pass hard stability gates");
assert(
  fragileCandidate.eligibility.failedGates.some((gate) => ["fold pass count", "negative folds"].includes(gate.name)),
  "fragile candidate should fail chronological fold gates"
);

const comparison = lab.benchmarkComparison(stableCandidate, benchmark);
assert(comparison.stabilityScoreDelta > 0, "benchmark comparison should expose positive stability delta");
assert(comparison.foldPassDelta > 0, "benchmark comparison should expose better fold pass count");

console.log("stability_first_challenger_search_smoke ok");
