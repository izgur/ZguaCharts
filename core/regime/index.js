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

function mapRegimeToCandles(tradingCandles, regimeCandles) {
  var regimes = buildBtcRegimeFrame(regimeCandles);
  var pointer = -1;
  return data.normalizeCandles(tradingCandles || []).map(function (candle) {
    while (pointer + 1 < regimes.length && regimes[pointer + 1].time <= candle.time) {
      pointer += 1;
    }
    var regime = pointer >= 0 ? regimes[pointer] : null;
    return Object.assign({}, candle, {
      btcRegime: regime ? regime.regime : "neutral",
      btcRegimeTime: regime ? regime.time : null,
      btcClose4h: regime ? regime.close : null,
      btcEma50_4h: regime ? regime.ema50 : null,
      btcEma200_4h: regime ? regime.ema200 : null
    });
  });
}

module.exports = {
  buildBtcRegimeFrame: buildBtcRegimeFrame,
  mapRegimeToCandles: mapRegimeToCandles
};
