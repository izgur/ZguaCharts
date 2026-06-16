function ema(values, period) {
  var alpha = 2 / (period + 1);
  var out = [];
  var current = null;
  values.forEach(function (value, index) {
    value = Number(value);
    if (current === null) current = value;
    else current = value * alpha + current * (1 - alpha);
    out[index] = current;
  });
  return out;
}

function sma(values, period) {
  var out = [];
  var sum = 0;
  values.forEach(function (value, index) {
    sum += Number(value);
    if (index >= period) sum -= Number(values[index - period]);
    out[index] = index >= period - 1 ? sum / period : null;
  });
  return out;
}

function rsi(values, period) {
  var out = [];
  var gains = 0;
  var losses = 0;
  for (var i = 0; i < values.length; i += 1) {
    if (i === 0) {
      out.push(null);
      continue;
    }
    var change = values[i] - values[i - 1];
    var gain = Math.max(change, 0);
    var loss = Math.max(-change, 0);
    if (i <= period) {
      gains += gain;
      losses += loss;
      out.push(i === period ? 100 - 100 / (1 + (gains / period) / Math.max(losses / period, 1e-9)) : null);
    } else {
      gains = (gains * (period - 1) + gain) / period;
      losses = (losses * (period - 1) + loss) / period;
      out.push(100 - 100 / (1 + gains / Math.max(losses, 1e-9)));
    }
  }
  return out;
}

function atr(candles, period) {
  var trueRanges = candles.map(function (candle, index) {
    if (index === 0) return candle.high - candle.low;
    var prevClose = candles[index - 1].close;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - prevClose), Math.abs(candle.low - prevClose));
  });
  return ema(trueRanges, period);
}

function adx(candles, period) {
  var plusDm = [];
  var minusDm = [];
  var trueRanges = [];
  candles.forEach(function (candle, index) {
    if (index === 0) {
      plusDm[index] = 0;
      minusDm[index] = 0;
      trueRanges[index] = candle.high - candle.low;
      return;
    }
    var prev = candles[index - 1];
    var upMove = candle.high - prev.high;
    var downMove = prev.low - candle.low;
    plusDm[index] = upMove > downMove && upMove > 0 ? upMove : 0;
    minusDm[index] = downMove > upMove && downMove > 0 ? downMove : 0;
    trueRanges[index] = Math.max(candle.high - candle.low, Math.abs(candle.high - prev.close), Math.abs(candle.low - prev.close));
  });
  var atrValues = ema(trueRanges, period);
  var plusDi = ema(plusDm, period).map(function (value, index) {
    return atrValues[index] ? 100 * value / atrValues[index] : null;
  });
  var minusDi = ema(minusDm, period).map(function (value, index) {
    return atrValues[index] ? 100 * value / atrValues[index] : null;
  });
  var dx = plusDi.map(function (value, index) {
    var sum = (value || 0) + (minusDi[index] || 0);
    return sum ? 100 * Math.abs((value || 0) - (minusDi[index] || 0)) / sum : null;
  });
  return {
    adx: ema(dx.map(function (value) { return value === null ? 0 : value; }), period),
    plusDi: plusDi,
    minusDi: minusDi
  };
}

function donchian(candles, period) {
  var high = [];
  var low = [];
  candles.forEach(function (_candle, index) {
    if (index < period - 1) {
      high[index] = null;
      low[index] = null;
      return;
    }
    var slice = candles.slice(index - period + 1, index + 1);
    high[index] = slice.reduce(function (max, candle) { return Math.max(max, candle.high); }, slice[0].high);
    low[index] = slice.reduce(function (min, candle) { return Math.min(min, candle.low); }, slice[0].low);
  });
  return { high: high, low: low };
}

function vwap(candles) {
  var out = [];
  var pv = 0;
  var volume = 0;
  candles.forEach(function (candle, index) {
    var typical = (candle.high + candle.low + candle.close) / 3;
    pv += typical * candle.volume;
    volume += candle.volume;
    out[index] = volume ? pv / volume : null;
  });
  return out;
}

function pivotLevels(candles) {
  return candles.map(function (candle, index) {
    var source = index > 0 ? candles[index - 1] : candle;
    var pivot = (source.high + source.low + source.close) / 3;
    var r1 = pivot * 2 - source.low;
    var s1 = pivot * 2 - source.high;
    return {
      pivot: pivot,
      pivotR1: r1,
      pivotS1: s1,
      pivotR2: pivot + (source.high - source.low),
      pivotS2: pivot - (source.high - source.low)
    };
  });
}

function anchoredVwapFrom(candles, startIndex) {
  var pv = 0;
  var volume = 0;
  for (var i = Math.max(0, startIndex); i < candles.length; i += 1) {
    var typical = (candles[i].high + candles[i].low + candles[i].close) / 3;
    pv += typical * candles[i].volume;
    volume += candles[i].volume;
  }
  return volume ? pv / volume : null;
}

function swingFeatures(candles, options) {
  options = options || {};
  var left = Math.max(1, Number(options.left || options.swingLeft || 3));
  var right = Math.max(1, Number(options.right || options.swingRight || 3));
  var goldenTolerancePct = Number(options.goldenPocketTolerancePct || 0.25);
  var rows = [];
  var lastHigh = null;
  var previousHigh = null;
  var lastLow = null;
  var previousLow = null;

  function isSwingHigh(index) {
    if (index < left || index + right >= candles.length) return false;
    for (var offset = 1; offset <= left; offset += 1) {
      if (candles[index].high <= candles[index - offset].high) return false;
    }
    for (var r = 1; r <= right; r += 1) {
      if (candles[index].high < candles[index + r].high) return false;
    }
    return true;
  }

  function isSwingLow(index) {
    if (index < left || index + right >= candles.length) return false;
    for (var offset = 1; offset <= left; offset += 1) {
      if (candles[index].low >= candles[index - offset].low) return false;
    }
    for (var r = 1; r <= right; r += 1) {
      if (candles[index].low > candles[index + r].low) return false;
    }
    return true;
  }

  function emptyRow() {
    return {
      confirmedSwingHigh: false,
      confirmedSwingLow: false,
      lastSwingHigh: null,
      lastSwingHighTime: null,
      lastSwingLow: null,
      lastSwingLowTime: null,
      higherHigh: false,
      lowerHigh: false,
      higherLow: false,
      lowerLow: false,
      structureBreakUp: false,
      structureBreakDown: false,
      marketStructureTrend: "unknown",
      fib382: null,
      fib50: null,
      fib618: null,
      fib786: null,
      fibExt1272: null,
      fibExt1618: null,
      goldenPocketLow: null,
      goldenPocketHigh: null,
      goldenPocketProximityPct: null,
      nearGoldenPocket: false,
      anchoredVwapFromSwingLow: null,
      anchoredVwapFromSwingHigh: null
    };
  }

  for (var i = 0; i < candles.length; i += 1) {
    var row = emptyRow();
    var candle = candles[i];
    var brokeUpBeforeUpdate = lastHigh && candle.close > lastHigh.price;
    var brokeDownBeforeUpdate = lastLow && candle.close < lastLow.price;
    var candidate = i - right;

    if (candidate >= 0 && isSwingHigh(candidate)) {
      previousHigh = lastHigh;
      lastHigh = { price: candles[candidate].high, time: candles[candidate].time, index: candidate };
      row.confirmedSwingHigh = true;
      row.higherHigh = !!(previousHigh && lastHigh.price > previousHigh.price);
      row.lowerHigh = !!(previousHigh && lastHigh.price < previousHigh.price);
    }
    if (candidate >= 0 && isSwingLow(candidate)) {
      previousLow = lastLow;
      lastLow = { price: candles[candidate].low, time: candles[candidate].time, index: candidate };
      row.confirmedSwingLow = true;
      row.higherLow = !!(previousLow && lastLow.price > previousLow.price);
      row.lowerLow = !!(previousLow && lastLow.price < previousLow.price);
    }

    row.lastSwingHigh = lastHigh ? lastHigh.price : null;
    row.lastSwingHighTime = lastHigh ? lastHigh.time : null;
    row.lastSwingLow = lastLow ? lastLow.price : null;
    row.lastSwingLowTime = lastLow ? lastLow.time : null;
    row.structureBreakUp = !!brokeUpBeforeUpdate;
    row.structureBreakDown = !!brokeDownBeforeUpdate;
    row.marketStructureTrend = row.structureBreakUp || (lastHigh && previousHigh && lastHigh.price > previousHigh.price && lastLow && previousLow && lastLow.price > previousLow.price)
      ? "up"
      : row.structureBreakDown || (lastHigh && previousHigh && lastHigh.price < previousHigh.price && lastLow && previousLow && lastLow.price < previousLow.price)
        ? "down"
        : "unknown";

    if (lastHigh && lastLow && lastLow.index < lastHigh.index) {
      var range = lastHigh.price - lastLow.price;
      if (range > 0) {
        row.fib382 = lastHigh.price - range * 0.382;
        row.fib50 = lastHigh.price - range * 0.5;
        row.fib618 = lastHigh.price - range * 0.618;
        row.fib786 = lastHigh.price - range * 0.786;
        row.fibExt1272 = lastHigh.price + range * 0.272;
        row.fibExt1618 = lastHigh.price + range * 0.618;
        row.goldenPocketLow = Math.min(row.fib618, row.fib786);
        row.goldenPocketHigh = Math.max(row.fib618, row.fib786);
        if (candle.close && row.goldenPocketLow !== null) {
          var distance = candle.close >= row.goldenPocketLow && candle.close <= row.goldenPocketHigh
            ? 0
            : Math.min(Math.abs(candle.close - row.goldenPocketLow), Math.abs(candle.close - row.goldenPocketHigh));
          row.goldenPocketProximityPct = distance / candle.close * 100;
          row.nearGoldenPocket = row.goldenPocketProximityPct <= goldenTolerancePct;
        }
      }
    }
    if (lastLow) row.anchoredVwapFromSwingLow = anchoredVwapFrom(candles.slice(0, i + 1), lastLow.index);
    if (lastHigh) row.anchoredVwapFromSwingHigh = anchoredVwapFrom(candles.slice(0, i + 1), lastHigh.index);
    rows[i] = row;
  }
  return rows;
}

function macd(values, fast, slow, signal) {
  var fastEma = ema(values, fast);
  var slowEma = ema(values, slow);
  var line = values.map(function (_value, index) { return fastEma[index] - slowEma[index]; });
  var signalLine = ema(line, signal);
  var histogram = line.map(function (value, index) { return value - signalLine[index]; });
  return { line: line, signal: signalLine, histogram: histogram };
}

function bollinger(values, period, stddev) {
  var middle = sma(values, period);
  var upper = [];
  var lower = [];
  for (var i = 0; i < values.length; i += 1) {
    if (i < period - 1) {
      upper[i] = null;
      lower[i] = null;
      continue;
    }
    var slice = values.slice(i - period + 1, i + 1);
    var mean = middle[i];
    var variance = slice.reduce(function (sum, value) { return sum + Math.pow(value - mean, 2); }, 0) / period;
    var deviation = Math.sqrt(variance);
    upper[i] = mean + stddev * deviation;
    lower[i] = mean - stddev * deviation;
  }
  return { upper: upper, middle: middle, lower: lower };
}

function supertrend(candles, period, multiplier) {
  var atrValues = atr(candles, period);
  var line = [];
  var direction = [];
  for (var i = 0; i < candles.length; i += 1) {
    var hl2 = (candles[i].high + candles[i].low) / 2;
    var upper = hl2 + multiplier * atrValues[i];
    var lower = hl2 - multiplier * atrValues[i];
    if (i === 0) {
      line[i] = lower;
      direction[i] = 1;
    } else if (candles[i].close >= line[i - 1]) {
      direction[i] = 1;
      line[i] = Math.max(lower, line[i - 1]);
    } else {
      direction[i] = -1;
      line[i] = Math.min(upper, line[i - 1]);
    }
  }
  return { line: line, direction: direction };
}

function buildIndicatorFrame(candles, params) {
  params = params || {};
  var close = candles.map(function (c) { return c.close; });
  var ema9Values = ema(close, params.emaFast || 9);
  var ema20Values = ema(close, params.emaReclaim || 20);
  var ema21Values = ema(close, params.emaMomentumSlow || 21);
  var ema50Values = ema(close, params.emaTrendFast || 50);
  var ema100Values = ema(close, params.emaMid || 100);
  var ema200Values = ema(close, params.emaSlow || 200);
  var rsiValues = rsi(close, params.rsiPeriod || 14);
  var macdData = macd(close, params.macdFast || 12, params.macdSlow || 26, params.macdSignal || 9);
  var bb = bollinger(close, params.bbPeriod || 20, params.bbStddev || 2);
  var atrValues = atr(candles, params.atrPeriod || 14);
  var adxData = adx(candles, params.adxPeriod || 14);
  var dc20 = donchian(candles, params.donchian20 || 20);
  var dc55 = donchian(candles, params.donchian55 || 55);
  var dc100 = donchian(candles, params.donchian100 || 100);
  var vwapValues = vwap(candles);
  var pivots = pivotLevels(candles);
  var swing = swingFeatures(candles, params);
  var volumeMa20 = sma(candles.map(function (c) { return c.volume; }), params.volumeMa || 20);
  var st = supertrend(candles, params.supertrendPeriod || 10, params.supertrendMultiplier || 3);
  return candles.map(function (candle, index) {
    return Object.assign({}, candle, swing[index] || {}, pivots[index] || {}, {
      __index: index,
      ema9: ema9Values[index],
      ema20: ema20Values[index],
      ema21: ema21Values[index],
      ema50: ema50Values[index],
      ema100: ema100Values[index],
      ema200: ema200Values[index],
      rsi14: rsiValues[index],
      atr14: atrValues[index],
      atrPct: candle.close ? atrValues[index] / candle.close : 0,
      adx14: adxData.adx[index],
      plusDi14: adxData.plusDi[index],
      minusDi14: adxData.minusDi[index],
      donchianHigh20: dc20.high[index],
      donchianLow20: dc20.low[index],
      donchianHigh55: dc55.high[index],
      donchianLow55: dc55.low[index],
      donchianHigh100: dc100.high[index],
      donchianLow100: dc100.low[index],
      vwap: vwapValues[index],
      macdLine: macdData.line[index],
      macdSignal: macdData.signal[index],
      macdHistogram: macdData.histogram[index],
      bbUpper: bb.upper[index],
      bbMiddle: bb.middle[index],
      bbLower: bb.lower[index],
      supertrendLine: st.line[index],
      supertrendDirection: st.direction[index],
      volumeMa20: volumeMa20[index]
    });
  });
}

module.exports = {
  ema: ema,
  sma: sma,
  rsi: rsi,
  atr: atr,
  adx: adx,
  donchian: donchian,
  vwap: vwap,
  pivotLevels: pivotLevels,
  swingFeatures: swingFeatures,
  macd: macd,
  bollinger: bollinger,
  supertrend: supertrend,
  buildIndicatorFrame: buildIndicatorFrame
};
