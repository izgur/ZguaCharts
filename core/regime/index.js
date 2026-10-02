const indicators = require("../indicators");
const data = require("../data");

function buildBtcRegimeFrame(candles) {
  var normalized = data.normalizeCandles(candles || []);
  var frame = indicators.buildIndicatorFrame(normalized, { emaTrendFast: 50, emaSlow: 200 });
  return frame.map(function (row) {
    var state = "neutral";
    if (row.close > row.ema200 && row.ema50 > row.ema200) state = "bullish";
    else if (row.close < row.ema200 && row.ema50 < row.ema200) state = "bearish";
    return {
      time: row.time,
      close: row.close,
      ema50: row.ema50,
      ema200: row.ema200,
      regime: state
    };
  });
}

function mapRegimeToCandles(tradingCandles, regimeCandles, benchmarkCandles, options) {
  options = options || {};
  var regimes = buildBtcRegimeFrame(regimeCandles);
  var benchmarkFrame = buildBenchmarkFrame(benchmarkCandles);
  var trading = data.normalizeCandles(tradingCandles || []);
  var signalSeconds = options.interval ? data.intervalToMs(options.interval) / 1000 : (trading.length > 1 ? trading[1].time - trading[0].time : 3600);
  var regimeSeconds = data.intervalToMs(options.regimeInterval || "4h") / 1000;
  var benchmarkSeconds = options.benchmarkInterval ? data.intervalToMs(options.benchmarkInterval) / 1000 : signalSeconds;
  var pointer = -1;
  var benchmarkPointer = -1;
  return trading.map(function (candle) {
    var knownAt = candle.time + signalSeconds;
    while (pointer + 1 < regimes.length && regimes[pointer + 1].time + regimeSeconds <= knownAt) {
      pointer += 1;
    }
    while (benchmarkPointer + 1 < benchmarkFrame.length && benchmarkFrame[benchmarkPointer + 1].time + benchmarkSeconds <= knownAt) {
      benchmarkPointer += 1;
    }
    var regime = pointer >= 0 ? regimes[pointer] : null;
    var benchmark = benchmarkPointer >= 0 ? benchmarkFrame[benchmarkPointer] : null;
    return Object.assign({}, candle, {
      btcRegime: regime ? regime.regime : "neutral",
      btcRegimeTime: regime ? regime.time : null,
      btcClose4h: regime ? regime.close : null,
      btcEma50_4h: regime ? regime.ema50 : null,
      btcEma200_4h: regime ? regime.ema200 : null,
      rsBenchmarkClose4h: benchmark ? benchmark.close : null,
      rsBenchmarkTime: benchmark ? benchmark.time : null
    });
  });
}

function buildBenchmarkFrame(candles) {
  return data.normalizeCandles(candles || []).map(function (row) {
    return {
      time: row.time,
      close: row.close
    };
  });
}

module.exports = {
  buildBtcRegimeFrame: buildBtcRegimeFrame,
  buildBenchmarkFrame: buildBenchmarkFrame,
  mapRegimeToCandles: mapRegimeToCandles
};
