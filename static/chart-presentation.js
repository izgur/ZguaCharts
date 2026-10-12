(function (root) {
  "use strict";
  const styles = [
    ["bars", "Bars", "BarSeries"], ["candles", "Candles", "CandlestickSeries"],
    ["hollow", "Hollow candles", "CandlestickSeries"], ["line", "Line", "LineSeries"],
    ["markers", "Line with markers", "LineSeries"], ["step", "Step line", "LineSeries"],
    ["area", "Area", "AreaSeries"], ["baseline", "Baseline", "BaselineSeries"],
    ["columns", "Columns", "HistogramSeries"], ["heikin", "Heikin Ashi", "CandlestickSeries"],
  ];
  function seriesData(candles, style) {
    if (style === "heikin") {
      let previous;
      return candles.map((c) => {
        const close = (c.open + c.high + c.low + c.close) / 4;
        const open = previous ? (previous.open + previous.close) / 2 : (c.open + c.close) / 2;
        previous = { time: c.time, open, close, high: Math.max(c.high, open, close), low: Math.min(c.low, open, close) };
        return previous;
      });
    }
    if (["candles", "hollow", "bars"].includes(style)) return candles;
    return candles.map((c) => ({ time: c.time, value: c.close, ...(style === "columns" ? { color: c.close >= c.open ? "#12b886" : "#ff5c7a" } : {}) }));
  }
  function changes(candles) {
    const last = candles.at(-1);
    const change = (seconds) => {
      if (!last) return null;
      const reference = [...candles].reverse().find((c) => c.time <= last.time - seconds);
      if (!reference || !reference.close || last.time - seconds - reference.time > 1800) return null;
      const value = last.close - reference.close;
      return { value, percent: value / reference.close * 100 };
    };
    return { price: last?.close, volume: last?.volume, change1h: change(3600), change24h: change(86400) };
  }
  function sidebarWidth(value, available) {
    const max = Math.max(200, Math.min(720, available - 320));
    return Math.round(Math.max(200, Math.min(max, Number.isFinite(Number(value)) ? Number(value) : 230)));
  }
  function replacePreservingScroll(element, markup) {
    if (element.renderedMarkup === markup) return false;
    const top = element.scrollTop, left = element.scrollLeft;
    const scroller = element.querySelector(".watch-strategy-trades");
    const tradeTop = scroller?.scrollTop || 0, tradeLeft = scroller?.scrollLeft || 0;
    const selected = element.querySelector(".strategy-trade-row.is-selected")?.dataset.strategyTradeIndex;
    element.innerHTML = markup; element.renderedMarkup = markup;
    element.scrollTop = top; element.scrollLeft = left;
    const next = element.querySelector(".watch-strategy-trades");
    if (next) { next.scrollTop = tradeTop; next.scrollLeft = tradeLeft; }
    if (selected !== undefined) element.querySelector(`[data-strategy-trade-index="${selected}"]`)?.classList.add("is-selected");
    return true;
  }
  const api = { styles, seriesData, changes, sidebarWidth, replacePreservingScroll };
  if (typeof module !== "undefined") module.exports = api; else root.ChartPresentation = api;
})(typeof window !== "undefined" ? window : globalThis);
