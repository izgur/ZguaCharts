"""Additional causal OHLCV studies. No Pine interpreter or strategy execution."""
import math
import numpy as np
import pandas as pd
from community_indicators import CATALOG as COMMUNITY_CATALOG, calculate as calculate_community


def study(label, defaults, description, overlay=False):
    return {"label": label, "defaults": defaults, "parameters": description,
            "placement": "overlay" if overlay else "pane"}


CATALOG = {
    "zgua": study("Zgua Divergences", {"left": 3, "right": 3, "min_bars": 5, "max_bars": 80, "lookback": 3, "price_equal_pct": 0.05, "osc_equal_pct": 1.0, "min_osc_pct": 2.0, "max_events": 80, "regular": 1, "hidden": 1, "exaggerated": 1, "weak": 0, "rsi_enabled": 1, "macd_enabled": 1, "hist_enabled": 1, "wt_enabled": 1, "stoch_enabled": 1, "obv_enabled": 1, "cmf_enabled": 1, "length": 14, "fast": 12, "slow": 26, "signal": 9, "channel": 10, "average": 21, "flow": 20, "draw_lines": 1, "engulfing": 0, "sweep": 0}, "regular/hidden/exaggerated/weak bullish and bearish; seven studies; strict confirmed price pivots, oscillator sampled at those pivots; observation on next bar opening; optional engulfing/sweep context", True),
    "wma": study("Weighted Moving Average", {"length": 20}, "close; linear weights", True),
    "rma": study("Smoothed Moving Average", {"length": 20}, "close; Wilder smoothing; SMA seed", True),
    "dema": study("Double EMA", {"length": 20}, "close; 2 EMA - EMA of EMA", True),
    "tema": study("Triple EMA", {"length": 20}, "close; triple exponential combination", True),
    "hma": study("Hull Moving Average", {"length": 20}, "close; weighted averages; rounded square-root length", True),
    "vwma": study("Volume-weighted Moving Average", {"length": 20}, "close; rolling volume weights", True),
    "alma": study("Arnaud Legoux Moving Average", {"length": 20, "offset": 0.85, "sigma": 6}, "close; Gaussian weights", True),
    "lsma": study("Least Squares Moving Average", {"length": 25}, "close; trailing linear regression endpoint", True),
    "donchian": study("Donchian Channels", {"length": 20}, "rolling high/low; includes current candle", True),
    "keltner": study("Keltner Channels", {"length": 20, "atr": 10, "multiplier": 2}, "close EMA; Wilder ATR bands", True),
    "envelope": study("Moving Average Envelope", {"length": 20, "percent": 2.5}, "close SMA; percent bands", True),
    "regression": study("Linear Regression Channel", {"length": 100, "deviation": 2}, "trailing close regression; residual population deviation", True),
    "adx": study("Average Directional Index", {"length": 14, "smoothing": 14}, "Wilder directional movement and smoothing"),
    "dmi": study("Directional Movement Index", {"length": 14, "smoothing": 14}, "+DI; -DI; ADX; Wilder smoothing"),
    "aroon": study("Aroon", {"length": 14}, "bars since high/low over length + 1 bars; latest tie wins"),
    "aroonosc": study("Aroon Oscillator", {"length": 14}, "Aroon up minus down"),
    "cci": study("Commodity Channel Index", {"length": 20}, "HLC3; mean absolute deviation; scale 0.015"),
    "obv": study("On-balance Volume", {}, "signed close-to-close volume; initial volume seed"),
    "mfi": study("Money Flow Index", {"length": 14}, "HLC3 times volume; positive/negative money-flow ratio"),
    "cmf": study("Chaikin Money Flow", {"length": 20}, "close location times volume; rolling ratio"),
    "ad": study("Accumulation / Distribution", {}, "cumulative close-location volume; zero-range contribution zero"),
    "chaikin": study("Chaikin Oscillator", {"fast": 3, "slow": 10}, "fast EMA minus slow EMA of accumulation/distribution"),
    "roc": study("Rate of Change", {"length": 9}, "close change over length bars, percent"),
    "momentum": study("Momentum", {"length": 10}, "close minus close length bars ago"),
    "stochastic": study("Stochastic", {"length": 14, "k": 3, "d": 3}, "close position in high/low range; SMA K and D; guides 80/20"),
    "williams": study("Williams Percent Range", {"length": 14}, "close position in high/low range, -100 to 0; guides -80/-20"),
    "ao": study("Awesome Oscillator", {"fast": 5, "slow": 34}, "HL2 fast SMA minus slow SMA"),
    "bop": study("Balance of Power", {"length": 14}, "SMA of (close - open) / (high - low)"),
    "bullbear": study("Bull Bear Power", {"length": 13}, "high and low minus close EMA"),
    "bbpercent": study("Bollinger Bands %B", {"length": 20, "deviation": 2}, "(close - lower band) / band width; guides 0/1"),
    "bbwidth": study("Bollinger BandWidth", {"length": 20, "deviation": 2}, "band width / SMA basis, percent"),
    "trix": study("TRIX", {"length": 18, "signal": 9}, "triple EMA one-bar rate of change, percent; EMA signal"),
    "tsi": study("True Strength Index", {"long": 25, "short": 13, "signal": 13}, "double EMA of close momentum / absolute momentum, percent"),
    "ulcer": study("Ulcer Index", {"length": 14}, "RMS percentage drawdown from rolling close high"),
    "vortex": study("Vortex Indicator", {"length": 14}, "rolling cross-bar high/low movement / true range"),
    "efi": study("Elder Force Index", {"length": 13}, "EMA of close change times volume"),
    "hvol": study("Historical Volatility", {"length": 20, "annualization": 365}, "log-return sample deviation; annualization in bars/year; percent"),
    "cmo": study("Chande Momentum Oscillator", {"length": 14}, "rolling gains minus losses / gains plus losses, percent"),
    "pvt": study("Price-volume Trend", {}, "cumulative close percent change times volume"),
    "pvo": study("Percentage Volume Oscillator", {"fast": 12, "slow": 26, "signal": 9}, "volume EMA difference / slow EMA, percent; EMA signal"),
    "squeeze": study("Squeeze Momentum", {"length": 20, "deviation": 2, "multiplier": 1.5}, "independent BB/KC squeeze; close EMA and Wilder ATR KC; trailing regression momentum; squeeze flag 1/0"),
    "wavetrend": study("WaveTrend Oscillator", {"channel": 10, "average": 21, "signal": 4}, "HLC3; EMA deviation normalization 0.015; EMA oscillator; SMA signal; guides +/-53"),
}


CATALOG.update(COMMUNITY_CATALOG)
CATALOG.update({
    "ichimoku": study("Ichimoku Cloud", {"conversion": 9, "base": 26, "span": 52, "displacement": 26}, "high/low midpoints; leading spans +displacement-1; lagging close -displacement+1; display offsets only", True),
    "psar": study("Parabolic SAR", {"start": 0.02, "increment": 0.02, "maximum": 0.2}, "Wilder recursive SAR; initial direction from first two closes; two-bar extreme clamp", True),
    "kama": study("Kaufman Adaptive Moving Average", {"length": 10, "fast": 2, "slow": 30}, "close; efficiency ratio; squared adaptive smoothing; SMA seed", True),
    "fisher": study("Fisher Transform", {"length": 9}, "HL2 range normalization; recursive 0.33/0.67 smoothing; clamp 0.999; previous Fisher trigger"),
    "choppiness": study("Choppiness Index", {"length": 14}, "100 log10(sum true range / high-low range) / log10(length); guides 61.8/38.2"),
    "chandelier": study("Chandelier Exit", {"length": 22, "multiplier": 3}, "rolling extremes minus/plus Wilder ATR; raw long/short stop levels", True),
    "chandekroll": study("Chande Kroll Stop", {"length": 10, "multiplier": 1, "stop": 9}, "ATR-adjusted rolling extremes; second rolling stop window", True),
    "adr": study("Average Daily Range", {"length": 14}, "mean high-low range of completed UTC days; partial first day excluded"),
    "eom": study("Ease of Movement", {"length": 14, "scale": 10000}, "HL2 movement times range / volume; SMA; volume scaled"),
    "massindex": study("Mass Index", {"length": 25, "smoothing": 9}, "rolling sum of EMA(high-low) / double EMA(high-low)"),
    "coppock": study("Coppock Curve", {"fast": 11, "slow": 14, "smoothing": 10}, "WMA of two close rates of change; lengths are bars, traditionally monthly"),
    "kst": study("Know Sure Thing", {"roc1": 10, "roc2": 15, "roc3": 20, "roc4": 30, "smooth1": 10, "smooth2": 10, "smooth3": 10, "smooth4": 15, "signal": 9}, "weighted sum of four SMA-smoothed ROC series; SMA signal"),
    "ppo": study("Percentage Price Oscillator", {"fast": 12, "slow": 26, "signal": 9}, "price EMA difference / slow EMA, percent; EMA signal"),
    "priceosc": study("Price Oscillator", {"fast": 14, "slow": 28}, "fast SMA minus slow SMA; absolute price units"),
    "connors": study("Connors RSI", {"length": 3, "streak": 2, "rank": 100}, "mean of Wilder price RSI, streak RSI and ROC rank against preceding window; strict ties"),
    "nvi": study("Negative Volume Index", {"length": 255}, "seed 1000; compound price returns only on lower-volume bars; EMA signal"),
    "pvi": study("Positive Volume Index", {"length": 255}, "seed 1000; compound price returns only on higher-volume bars; EMA signal"),
    "netvolume": study("Net Volume", {}, "signed candle volume from close versus previous close; unchanged contributes zero"),
    "updownvolume": study("Up/down Volume", {}, "volume classified by close versus open; unchanged split equally; estimate, not trade delta"),
    "volume24h": study("24-hour Volume", {}, "rolling 24 elapsed hours of base volume; only loaded history available"),
    "performance": study("Performance", {"length": 20}, "trailing close return, percent; configured number of bars"),
    "median": study("Median", {"length": 20}, "rolling close median", True),
    "mcginley": study("McGinley Dynamic", {"length": 14, "factor": 0.6}, "close; recursive adaptive divisor factor * length * (close / previous)^4; SMA seed", True),
    "smi": study("Stochastic Momentum Index", {"length": 10, "smoothing": 3, "signal": 3}, "double EMA of close distance from range midpoint / double EMA of range; scale 200"),
    "smiergodic": study("SMI Ergodic Indicator", {"fast": 5, "slow": 20, "signal": 5}, "double-smoothed close momentum / absolute momentum; EMA signal; ratio units"),
    "smiosc": study("SMI Ergodic Oscillator", {"fast": 5, "slow": 20, "signal": 5}, "SMI Ergodic minus its EMA signal; ratio units"),
    "rankcorr": study("Rank Correlation Index", {"length": 9}, "Spearman correlation of time and close ranks; average ties; scale 100"),
    "rciribbon": study("RCI Ribbon", {"short": 10, "medium": 30, "long": 50}, "three Spearman rank correlations of time and close; average ties"),
    "alligator": study("Williams Alligator", {"jaw": 13, "teeth": 8, "lips": 5, "jaw_shift": 8, "teeth_shift": 5, "lips_shift": 3}, "HL2 Wilder smoothing; forward display shifts 8/5/3", True),
    "fractals": study("Williams Fractal", {"length": 2}, "strict swing high/low confirmed after length right bars; markers at confirmation, no backdated signals", True),
    "pivothighlow": study("Pivot Points High Low", {"left": 5, "right": 5}, "strict swing pivots; levels begin at confirmation after right bars", True),
    "pivotstandard": study("Pivot Points Standard", {}, "traditional P/S1/S2/S3/R1/R2/R3; preceding completed UTC day; partial first day excluded", True),
    "maribbon": study("Moving Average Ribbon", {"length1": 20, "length2": 50, "length3": 100, "length4": 200}, "four close SMAs", True),
    "macross": study("Moving Average Crossover", {"fast": 9, "slow": 21}, "close SMAs; crossover markers on observation candle", True),
    "twap": study("Time-weighted Average Price", {}, "UTC session arithmetic mean of HLC3 on equally-spaced bars; partial loaded session", True),
    "dpo": study("Detrended Price Oscillator", {"length": 21}, "unshifted causal variant: close floor(length/2)+1 bars ago minus current SMA"),
    "rvi": study("Relative Vigor Index", {"length": 10}, "SMA of weighted four-bar close-open / weighted high-low; weighted signal"),
    "relativevolatility": study("Relative Volatility Index", {"length": 10, "smoothing": 14}, "Wilder-smoothed sample close deviation assigned by close direction; scale 100"),
    "pmo": study("Price Momentum Oscillator", {"length": 35, "smoothing": 20, "signal": 10}, "double custom EMA (alpha 2/length) of one-bar ROC * 10; EMA signal"),
    "trendstrength": study("Trend Strength Index", {"length": 14}, "Pearson correlation of close with bar index; -1 to +1"),
    "bbtrend": study("BBTrend", {"short": 20, "long": 50, "deviation": 2}, "difference of absolute lower/upper short-vs-long Bollinger distances / short basis; percent"),
    "volstop": study("Volatility Stop", {"length": 20, "multiplier": 2}, "close extreme since reversal; Wilder ATR trailing stop; close-based direction", True),
    "klinger": study("Klinger Oscillator", {"fast": 34, "slow": 55, "signal": 13}, "HLC sum direction; cumulative range volume force; fast-minus-slow EMA; EMA signal"),
    "relativevoltime": study("Relative Volume at Time", {"length": 10}, "current bar volume / mean matching UTC clock-time bars in preceding loaded days; regular intraday bars"),
    "ultimate": study("Ultimate Oscillator", {"short": 7, "medium": 14, "long": 28}, "buying pressure / true range; 4:2:1 weighted rolling ratios; scale 100"),
    "woodiescci": study("Woodies CCI", {"fast": 6, "slow": 14}, "fast/slow HLC3 CCI; raw CCI lines, not a complete Woodies pattern system"),
    "rsidivergence": study("RSI Divergence Indicator", {"length": 14, "left": 5, "right": 5}, "regular bullish/bearish divergence between consecutive confirmed price pivots and Wilder RSI; markers at confirmation"),
    "zigzag": study("Zigzag (Confirmed)", {"percent": 5}, "close-extreme reversal threshold; pivot levels appear at confirmation, not backdated", True),
    "autofib": study("Auto Fib Retracement (Confirmed)", {"left": 5, "right": 5}, "0/0.236/0.382/0.5/0.618/0.786/1 of latest two confirmed alternating swing points; causal levels", True),
    "fibextension": study("Auto Fib Extension (Confirmed)", {"left": 5, "right": 5}, "latest three confirmed alternating swings; C + (B-A) times 0.618/1/1.618/2.618", True),
    "autolevels": study("Auto Key Levels (Confirmed)", {"left": 5, "right": 5}, "latest confirmed swing support/resistance; independent variant, not TradingView proprietary Auto Key Levels", True),
    "autotrendlines": study("Auto Trendlines (Confirmed)", {"left": 5, "right": 5}, "causal projection through last two confirmed swing highs and lows", True),
    "pitchfork": study("Auto Pitchfork (Confirmed)", {"left": 5, "right": 5}, "Andrews median through first pivot and midpoint of next two; parallel rails; latest confirmed alternating swings", True),
    "anchoredvwap": study("VWAP Auto-anchored", {"length": 100}, "reset when current volume exceeds previous window maximum; HLC3 weighted since anchor; independent high-volume anchor", True),
    "specialk": study("Pring Special K", {"signal": 100}, "12 fixed ROC/SMA pairs: 10/10,15/10,20/10,30/15,40/50,65/65,75/75,100/100,195/130,265/130,390/130,530/195; weights 1,2,3,4 repeated; SMA signal"),
})


for _family in ("regular", "hidden", "exaggerated", "weak"):
    CATALOG["zgua"]["defaults"].pop(_family)
    for _side in ("bullish", "bearish"):
        CATALOG["zgua"]["defaults"][_family+"_"+_side] = 1


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s, n):
    seed = s.rolling(n).mean()
    first = seed.first_valid_index()
    if first is None:
        return s * np.nan
    values = s.copy()
    values.iloc[:first] = np.nan
    values.iloc[first] = seed.iloc[first]
    return values.ewm(alpha=1 / n, adjust=False, min_periods=1).mean()


def wma(s, n):
    weights = np.arange(1, n + 1)
    return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def divide(a, b):
    return a / b.replace(0, np.nan)


def regression(s, n):
    weights = np.arange(n) - (n - 1) / 2
    slope = s.rolling(n).apply(lambda x: np.dot(x, weights) / np.dot(weights, weights), raw=True)
    return s.rolling(n).mean() + slope * (n - 1) / 2


def wilder_rsi(s, n):
    change = s.diff()
    gain, loss = rma(change.clip(lower=0), n), rma(-change.clip(upper=0), n)
    result = 100 * divide(gain, gain + loss)
    return result.mask((gain + loss) == 0, 50)


def rank_correlation(s, n):
    time = np.arange(n, dtype=float)
    def rank_window(values):
        ranks = pd.Series(values).rank(method="average").values
        return 0.0 if np.std(ranks) == 0 else np.corrcoef(time, ranks)[0, 1] * 100
    return s.rolling(n).apply(rank_window, raw=True)


def confirmed_pivots(high, low, left, right):
    highs, lows = pd.Series(np.nan, index=high.index), pd.Series(np.nan, index=low.index)
    for i in range(left + right, len(high)):
        pivot = i - right
        hi, lo = high.iloc[pivot], low.iloc[pivot]
        other_highs = pd.concat([high.iloc[pivot-left:pivot], high.iloc[pivot+1:i+1]])
        other_lows = pd.concat([low.iloc[pivot-left:pivot], low.iloc[pivot+1:i+1]])
        if hi > other_highs.max(): highs.iloc[i] = hi
        if lo < other_lows.min(): lows.iloc[i] = lo
    return highs, lows


def completed_daily(df):
    days = (df["time"] // 86400).astype(int)
    daily = df.groupby(days).agg({"high": "max", "low": "min", "close": "last"})
    # The first loaded day may be truncated; never use it as a complete session.
    daily.iloc[0] = np.nan
    return days, daily


def zgua_classify(price_a, price_b, osc_a, osc_b, bullish, price_tolerance, osc_tolerance, min_osc_change):
    """Disjoint geometric classes; names do not assign a success probability."""
    dp, do = price_b-price_a, osc_b-osc_a
    direction = 1 if bullish else -1
    if abs(dp) <= price_tolerance:
        return "exaggerated" if direction*do > max(osc_tolerance, min_osc_change) else None
    if abs(do) <= osc_tolerance:
        return "weak" if direction*dp < -price_tolerance else None
    if abs(do) <= max(osc_tolerance, min_osc_change):
        return None
    if direction*dp < -price_tolerance and direction*do > 0:
        return "regular"
    if direction*dp > price_tolerance and direction*do < 0:
        return "hidden"
    return None


def zgua_pivots(high, low, left, right):
    """Vectorized strict pivots, shifted to their confirmation observation."""
    left_high = high.shift(1).rolling(left, min_periods=left).max()
    right_high = high.iloc[::-1].shift(1).rolling(right, min_periods=right).max().iloc[::-1]
    left_low = low.shift(1).rolling(left, min_periods=left).min()
    right_low = low.iloc[::-1].shift(1).rolling(right, min_periods=right).min().iloc[::-1]
    return high.where((high > left_high) & (high > right_high)).shift(right), low.where((low < left_low) & (low < right_low)).shift(right)


def zgua_events(df, oscillators, p):
    """Only use bars preceding an observed next opening; last OHLC is never read."""
    high, low, close = (df[name] for name in ("high", "low", "close"))
    hi, lo = zgua_pivots(high, low, p["left"], p["right"])
    span = 2*p["max_bars"]+p["right"]+1
    oscillator_data = {name: (values.to_numpy(), (values.rolling(span, min_periods=2).max()-values.rolling(span, min_periods=2).min()).to_numpy()) for name, values in oscillators.items()}
    high_values, low_values, close_values = high.to_numpy(), low.to_numpy(), close.to_numpy()
    times = df.time.to_numpy()
    events = []
    for pivots, bullish in ((lo, True), (hi, False)):
        previous = []
        pivot_values = pivots.to_numpy()
        for i in np.flatnonzero(np.isfinite(pivot_values[:-1])):
            pivot = int(i)-p["right"]
            price = float(pivot_values[i])
            seen = set()
            for old in reversed(previous[-p["lookback"]:]):
                distance = pivot-old
                if not p["min_bars"] <= distance <= p["max_bars"]:
                    continue
                price_a = float((low_values if bullish else high_values)[old])
                matches = {}
                for name, (oscillator, scales) in oscillator_data.items():
                    a, b = oscillator[old], oscillator[pivot]
                    scale = float(scales[i])
                    if not np.isfinite(a) or not np.isfinite(b) or not np.isfinite(scale):
                        continue
                    if scale <= 0:
                        continue
                    kind = zgua_classify(price_a, price, a, b, bullish, abs(price_a)*p["price_equal_pct"]/100,
                                         scale*p["osc_equal_pct"]/100, scale*p["min_osc_pct"]/100)
                    if kind and p[kind+("_bullish" if bullish else "_bearish")]:
                        matches.setdefault(kind, []).append(name)
                for kind, names in matches.items():
                    if kind in seen: continue
                    seen.add(kind)
                    boundary = float(high_values[old:pivot+1].max() if bullish else low_values[old:pivot+1].min())
                    breakout = float(close_values[i]) > boundary if bullish else float(close_values[i]) < boundary
                    events.append({"kind": kind, "bullish": bullish, "firstTime": int(times[old]),
                                   "pivotTime": int(times[pivot]), "confirmedAt": int(times[i+1]),
                                   "firstPrice": price_a, "pivotPrice": price, "priceConfirmed": bool(breakout),
                                   "studies": names, "distance": int(distance)})
            previous.append(pivot)
    if p["engulfing"] or p["sweep"]:
        last_low = last_high = None
        for i in range(1, len(df)-1):
            old_open, old_close = float(df.open.iloc[i-1]), float(close.iloc[i-1])
            opening, closing = float(df.open.iloc[i]), float(close.iloc[i])
            for bullish in (True, False):
                engulf = (old_close < old_open and closing > opening and opening <= old_close and closing >= old_open) if bullish else (old_close > old_open and closing < opening and opening >= old_close and closing <= old_open)
                anchor = last_low if bullish else last_high
                swept = anchor is not None and ((low.iloc[i] < low.iloc[anchor] and closing > low.iloc[anchor]) if bullish else (high.iloc[i] > high.iloc[anchor] and closing < high.iloc[anchor]))
                for kind, enabled in (("engulfing", p["engulfing"] and engulf), ("sweep", p["sweep"] and swept)):
                    if enabled:
                        value = float(low.iloc[i] if bullish else high.iloc[i])
                        events.append({"kind": kind, "bullish": bullish, "firstTime": int(df.time.iloc[i]), "pivotTime": int(df.time.iloc[i]), "confirmedAt": int(df.time.iloc[i+1]), "firstPrice": value, "pivotPrice": value, "priceConfirmed": False, "studies": [], "distance": 0})
            if np.isfinite(lo.iloc[i]): last_low = i-p["right"]
            if np.isfinite(hi.iloc[i]): last_high = i-p["right"]
    return sorted(events, key=lambda event: (event["confirmedAt"], event["kind"], event["firstTime"]))[-p["max_events"]:]


def zgua_oscillators(df, p):
    close, high, low, volume = (df[name] for name in ("close", "high", "low", "volume"))
    typical = (high+low+close)/3
    macd = ema(close, p["fast"])-ema(close, p["slow"])
    basis = ema(typical, p["channel"])
    wt = ema(divide(typical-basis, ema((typical-basis).abs(), p["channel"])*0.015), p["average"])
    location = divide(2*close-high-low, high-low).fillna(0)
    candidates = [("RSI", "rsi_enabled", wilder_rsi(close, p["length"])),
                  ("MACD", "macd_enabled", macd), ("Histogram", "hist_enabled", macd-ema(macd, p["signal"])),
                  ("WaveTrend", "wt_enabled", wt), ("Stochastic", "stoch_enabled", divide(close-low.rolling(p["length"]).min(), high.rolling(p["length"]).max()-low.rolling(p["length"]).min())*100),
                  ("OBV", "obv_enabled", (np.sign(close.diff()).fillna(0)*volume).cumsum()),
                  ("CMF", "cmf_enabled", divide((location*volume).rolling(p["flow"]).sum(), volume.rolling(p["flow"]).sum()))]
    return {name: values for name, flag, values in candidates if p[flag]}


def zgua_study(df, p):
    p = dict(p)
    for family in ("regular", "hidden", "exaggerated", "weak"):
        p[family] = p[family+"_bullish"] or p[family+"_bearish"]
    events = zgua_events(df, zgua_oscillators(df, p), p)
    styles = [("regular", "Regular", "RB", "RS", "#32d59c", "#ff657f"),
              ("hidden", "Hidden", "HB", "HS", "#50c9e8", "#f6b454"),
              ("exaggerated", "Exaggerated", "EB", "ES", "#a4df63", "#e294dd"),
              ("weak", "Weak", "WB", "WS", "#b2c9a2", "#cca5b2"),
              ("engulfing", "Engulfing context", "Engulf B", "Engulf S", "#6ccdb5", "#d78a9d"),
              ("sweep", "Swing sweep context", "Sweep B", "Sweep S", "#6ca4dd", "#d9a16b")]
    result = []
    for kind, label, bull_tag, bear_tag, bull_color, bear_color in styles:
        if not p[kind]: continue
        for bullish in (True, False):
            selected = [event for event in events if event["kind"] == kind and event["bullish"] == bullish]
            color, tag = (bull_color, bull_tag) if bullish else (bear_color, bear_tag)
            values = pd.Series(np.nan, index=df.index)
            markers, segments = [], []
            for event in selected:
                index = df.index[df.time == event["confirmedAt"]][0]
                values.loc[index] = event["pivotPrice"]
                names = "/".join(event["studies"])
                markers.append({"id": "zgua:{}:{}:{}:{}:{}".format(kind, int(bullish), event["firstTime"], event["pivotTime"], event["confirmedAt"]), "time": event["confirmedAt"], "text": tag+" "+names+(" + break" if event["priceConfirmed"] else ""), "color": color, "position": "belowBar" if bullish else "aboveBar", "shape": "arrowUp" if bullish else "arrowDown"})
                if p["draw_lines"] and event["firstTime"] < event["pivotTime"]:
                    segments.append({"data": [{"time": event["firstTime"], "value": event["firstPrice"]}, {"time": event["pivotTime"], "value": event["pivotPrice"]}], "confirmedAt": event["confirmedAt"]})
            result.append({"name": label+(" bullish" if bullish else " bearish"), "values": values, "color": color, "type": "line", "pointOnly": True, "markers": markers, "markerGroup": "divergence", "segments": segments, "events": selected})
    return result


def calculate(df, key, p):
    """Return named series on the original index; warmup remains NaN, never backfilled."""
    if key in COMMUNITY_CATALOG:
        return calculate_community(df, key, p)
    if key == "zgua":
        return zgua_study(df, p)
    close, high, low, volume = (df[name] for name in ("close", "high", "low", "volume"))
    length = p.get("length", 14)
    hl2, typical = (high + low) / 2, (high + low + close) / 3
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    location = divide(2 * close - high - low, high - low).fillna(0)
    ad = (location * volume).cumsum()
    lines = []
    def line(name, values, color="#4dabf7", kind="line", offset=0):
        lines.append({"name": name, "values": values, "color": color, "type": kind, "offset": offset})
    def guide(value):
        lines.append({"name": str(value), "values": pd.Series(value, index=df.index), "color": "#65737e", "type": "line", "guide": True})
    label = CATALOG[key]["label"]
    if key in ("wma", "rma", "dema", "tema", "hma", "vwma", "alma", "lsma"):
        if key == "wma": result = wma(close, length)
        elif key == "rma": result = rma(close, length)
        elif key in ("dema", "tema"):
            a = ema(close, length); b = ema(a, length)
            result = 2 * a - b if key == "dema" else 3 * a - 3 * b + ema(b, length)
        elif key == "hma": result = wma(2 * wma(close, max(1, length // 2)) - wma(close, length), max(1, int(math.sqrt(length) + 0.5)))
        elif key == "vwma": result = divide((close * volume).rolling(length).sum(), volume.rolling(length).sum())
        elif key == "lsma": result = regression(close, length)
        else:
            weights = np.exp(-((np.arange(length) - p["offset"] * (length - 1)) ** 2) / (2 * (length / p["sigma"]) ** 2))
            weights /= weights.sum()
            result = close.rolling(length).apply(lambda x: np.dot(x, weights), raw=True)
        line(label, result)
    elif key in ("donchian", "keltner", "envelope", "regression"):
        if key == "donchian":
            upper, lower = high.rolling(length).max(), low.rolling(length).min(); middle = (upper + lower) / 2
        else:
            middle = regression(close, length) if key == "regression" else ema(close, length) if key == "keltner" else close.rolling(length).mean()
            if key == "keltner": width = rma(tr, p["atr"]) * p["multiplier"]
            elif key == "envelope": width = middle * p["percent"] / 100
            else:
                x = np.arange(length)
                width = close.rolling(length).apply(lambda y: np.std(y - np.polyval(np.polyfit(x, y, 1), x)), raw=True) * p["deviation"]
            upper, lower = middle + width, middle - width
        line(label + " Upper", upper); line(label + " Basis", middle, "#f5b84b"); line(label + " Lower", lower)
    elif key in ("adx", "dmi"):
        up, down = high.diff(), -low.diff()
        positive = up.where((up > down) & (up > 0), 0.0); negative = down.where((down > up) & (down > 0), 0.0)
        positive.iloc[0] = negative.iloc[0] = np.nan
        plus, minus = divide(rma(positive, length), rma(tr, length)) * 100, divide(rma(negative, length), rma(tr, length)) * 100
        dx = divide((plus - minus).abs(), plus + minus) * 100
        dx = dx.mask((plus + minus) == 0, 0)
        line("ADX", rma(dx, p["smoothing"]), "#f5b84b")
        if key == "dmi": line("+DI", plus, "#12b886"); line("-DI", minus, "#ff5c7a")
        guide(25)
    elif key in ("aroon", "aroonosc"):
        up = high.rolling(length + 1).apply(lambda x: (length - np.argmax(x[::-1])) / length * 100, raw=True)
        down = low.rolling(length + 1).apply(lambda x: (length - np.argmin(x[::-1])) / length * 100, raw=True)
        if key == "aroonosc": line(label, up - down); guide(0)
        else: line("Aroon Up", up, "#12b886"); line("Aroon Down", down, "#ff5c7a")
    elif key == "cci":
        deviation = typical.rolling(length).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        line(label, divide(typical - typical.rolling(length).mean(), deviation * 0.015)); guide(100); guide(-100)
    elif key in ("obv", "ad", "chaikin", "cmf", "pvt", "efi"):
        if key == "obv":
            direction = np.sign(close.diff()); direction.iloc[0] = 1
            result = (direction * volume).cumsum()
        elif key == "ad": result = ad
        elif key == "chaikin": result = ema(ad, p["fast"]) - ema(ad, p["slow"])
        elif key == "cmf": result = divide((location * volume).rolling(length).sum(), volume.rolling(length).sum())
        elif key == "pvt": result = (close.pct_change().fillna(0) * volume).cumsum()
        else: result = ema(close.diff() * volume, length)
        line(label, result)
    elif key == "mfi":
        raw = typical * volume; change = typical.diff()
        positive = raw.where(change > 0, 0).rolling(length).sum()
        negative = raw.where(change < 0, 0).rolling(length).sum()
        result = divide(positive, positive + negative) * 100
        line(label, result.mask((positive + negative) == 0, 50)); guide(80); guide(20)
    elif key in ("roc", "momentum", "cmo", "hvol", "ulcer"):
        if key == "roc": result = (divide(close, close.shift(length)) - 1) * 100
        elif key == "momentum": result = close - close.shift(length)
        elif key == "cmo":
            up, down = close.diff().clip(lower=0).rolling(length).sum(), -close.diff().clip(upper=0).rolling(length).sum()
            result = (divide(up - down, up + down) * 100).mask((up + down) == 0, 0)
        elif key == "hvol": result = np.log(close / close.shift()).rolling(length).std() * math.sqrt(p["annualization"]) * 100
        else:
            drawdown = (close / close.rolling(length).max() - 1) * 100
            result = np.sqrt(drawdown.pow(2).rolling(length).mean())
        line(label, result)
    elif key in ("stochastic", "williams"):
        hi, lo = high.rolling(length).max(), low.rolling(length).min()
        if key == "williams": line(label, -100 * divide(hi - close, hi - lo)); guide(-20); guide(-80)
        else:
            k = (100 * divide(close - lo, hi - lo)).rolling(p["k"]).mean()
            line("%K", k); line("%D", k.rolling(p["d"]).mean(), "#f5b84b"); guide(80); guide(20)
    elif key == "ao": line(label, hl2.rolling(p["fast"]).mean() - hl2.rolling(p["slow"]).mean(), kind="histogram")
    elif key == "bop": line(label, divide(close - df["open"], high - low).fillna(0).rolling(length).mean()); guide(0)
    elif key == "bullbear":
        basis = ema(close, length); line("Bull Power", high - basis, "#12b886"); line("Bear Power", low - basis, "#ff5c7a"); guide(0)
    elif key in ("bbpercent", "bbwidth"):
        middle, width = close.rolling(length).mean(), close.rolling(length).std(ddof=0) * p["deviation"]
        line(label, divide(close - middle + width, 2 * width) if key == "bbpercent" else divide(2 * width, middle) * 100)
        if key == "bbpercent": guide(0); guide(1)
    elif key in ("trix", "tsi", "pvo"):
        if key == "trix": result = ema(ema(ema(close, length), length), length).pct_change(fill_method=None) * 100
        elif key == "pvo": result = divide(ema(volume, p["fast"]) - ema(volume, p["slow"]), ema(volume, p["slow"])) * 100
        else:
            diff = close.diff(); result = divide(ema(ema(diff, p["long"]), p["short"]), ema(ema(diff.abs(), p["long"]), p["short"])) * 100
        line(label, result); line("Signal", ema(result, p["signal"]), "#f5b84b"); guide(0)
    elif key == "vortex":
        total = tr.rolling(length).sum()
        line("VI+", divide((high - low.shift()).abs().rolling(length).sum(), total), "#12b886")
        line("VI-", divide((low - high.shift()).abs().rolling(length).sum(), total), "#ff5c7a")
    elif key == "squeeze":
        basis, bbwidth = close.rolling(length).mean(), close.rolling(length).std(ddof=0) * p["deviation"]
        kc, kcwidth = ema(close, length), rma(tr, length) * p["multiplier"]
        valid = bbwidth.notna() & kcwidth.notna() & kc.notna()
        flag = ((basis - bbwidth > kc - kcwidth) & (basis + bbwidth < kc + kcwidth)).astype(float).where(valid)
        center = ((high.rolling(length).max() + low.rolling(length).min()) / 2 + basis) / 2
        line("Squeeze momentum", regression(close - center, length), kind="histogram")
        line("Squeeze active (1/0)", flag, "#b197fc"); guide(0)
    elif key == "wavetrend":
        basis = ema(typical, p["channel"])
        oscillator = ema(divide(typical - basis, ema((typical - basis).abs(), p["channel"]) * 0.015), p["average"])
        line("WaveTrend", oscillator); line("WT Signal", oscillator.rolling(p["signal"]).mean(), "#f5b84b"); guide(53); guide(-53)
    elif key == "ultimate":
        bottom = pd.concat([low, close.shift()], axis=1).min(axis=1)
        top = pd.concat([high, close.shift()], axis=1).max(axis=1)
        pressure, spread = close-bottom, top-bottom
        ratios = [divide(pressure.rolling(p[name]).sum(), spread.rolling(p[name]).sum()) for name in ("short", "medium", "long")]
        line(label, (4*ratios[0]+2*ratios[1]+ratios[2])*100/7); guide(70); guide(30)
    elif key == "woodiescci":
        for name, color in [("fast", "#4dabf7"), ("slow", "#f5b84b")]:
            deviation = typical.rolling(p[name]).apply(lambda x: np.mean(np.abs(x-np.mean(x))), raw=True)
            line(name.title()+" CCI", divide(typical-typical.rolling(p[name]).mean(), deviation*0.015), color)
        guide(100); guide(-100); guide(0)
    elif key == "rsidivergence":
        result = wilder_rsi(close, length); hi, lo = confirmed_pivots(high, low, p["left"], p["right"])
        line("RSI", result); guide(70); guide(30); markers = []
        for values, bullish in [(lo, True), (hi, False)]:
            previous = None
            for i in range(len(df)):
                if not np.isfinite(values.iloc[i]): continue
                pivot = i-p["right"]; current = (values.iloc[i], result.iloc[pivot])
                if previous and np.isfinite(current[1]) and np.isfinite(previous[1]):
                    divergence = current[0] < previous[0] and current[1] > previous[1] if bullish else current[0] > previous[0] and current[1] < previous[1]
                    if divergence: markers.append({"time": int(df.time.iloc[i]), "text": "Bull divergence" if bullish else "Bear divergence", "color": "#12b886" if bullish else "#ff5c7a", "position": "belowBar" if bullish else "aboveBar", "shape": "circle"})
                previous = current
        lines[0]["markers"] = sorted(markers, key=lambda item: item["time"])
    elif key == "zigzag":
        result = pd.Series(np.nan, index=df.index); extreme = close.iloc[0]; rising = True; confirmed = np.nan
        markers = []
        for i in range(1, len(df)):
            extreme = max(extreme, close.iloc[i]) if rising else min(extreme, close.iloc[i])
            reversed_now = close.iloc[i] <= extreme*(1-p["percent"]/100) if rising else close.iloc[i] >= extreme*(1+p["percent"]/100)
            if reversed_now:
                confirmed = extreme; rising = not rising; extreme = close.iloc[i]
                markers.append({"time": int(df.time.iloc[i]), "text": "Confirmed swing", "color": "#12b886" if rising else "#ff5c7a", "position": "belowBar" if rising else "aboveBar", "shape": "circle"})
            result.iloc[i] = confirmed
        line(label, result, "#f5b84b"); lines[-1]["markers"] = markers
    elif key in ("autofib", "fibextension", "autolevels", "autotrendlines", "pitchfork"):
        hi, lo = confirmed_pivots(high, low, p["left"], p["right"])
        names = ["0", "0.236", "0.382", "0.5", "0.618", "0.786", "1"] if key == "autofib" else ["0.618", "1", "1.618", "2.618"] if key == "fibextension" else ["Median", "Upper rail", "Lower rail"] if key == "pitchfork" else ["Resistance", "Support"]
        output = {name: pd.Series(np.nan, index=df.index) for name in names}
        swings, highs, lows = [], [], []
        for i in range(len(df)):
            for values, side, history in [(hi, "high", highs), (lo, "low", lows)]:
                if not np.isfinite(values.iloc[i]): continue
                point = (i-p["right"], values.iloc[i], side); history.append(point)
                if swings and swings[-1][2] == side: swings[-1] = point
                else: swings.append(point)
            if key == "autolevels":
                if highs: output["Resistance"].iloc[i] = highs[-1][1]
                if lows: output["Support"].iloc[i] = lows[-1][1]
            elif key == "autotrendlines":
                for name, history in [("Resistance", highs), ("Support", lows)]:
                    if len(history) >= 2:
                        a, b = history[-2:]; output[name].iloc[i] = b[1]+(i-b[0])*(b[1]-a[1])/(b[0]-a[0])
            elif key == "autofib" and len(swings) >= 2:
                a, b = swings[-2:]
                for name in names: output[name].iloc[i] = b[1] - (b[1]-a[1])*float(name)
            elif len(swings) >= 3:
                a, b, c = swings[-3:]
                if key == "fibextension":
                    for name in names: output[name].iloc[i] = c[1]+(b[1]-a[1])*float(name)
                elif key == "pitchfork":
                    midpoint = ((b[0]+c[0])/2, (b[1]+c[1])/2)
                    slope = (midpoint[1]-a[1])/(midpoint[0]-a[0])
                    output["Median"].iloc[i] = a[1]+slope*(i-a[0])
                    rails = [point[1]+slope*(i-point[0]) for point in (b,c)]
                    output["Upper rail"].iloc[i], output["Lower rail"].iloc[i] = max(rails), min(rails)
        for index, name in enumerate(names): line(name, output[name], ["#f5b84b", "#4dabf7", "#12b886", "#b197fc"][index % 4])
    elif key == "anchoredvwap":
        reset = volume > volume.shift().rolling(length).max()
        groups = reset.fillna(False).cumsum()
        line(label, divide((typical*volume).groupby(groups).cumsum(), volume.groupby(groups).cumsum()))
    elif key == "specialk":
        pairs = [(10,10),(15,10),(20,10),(30,15),(40,50),(65,65),(75,75),(100,100),(195,130),(265,130),(390,130),(530,195)]
        result = sum((index % 4 + 1)*((divide(close, close.shift(roc))-1)*100).rolling(smooth).mean() for index, (roc,smooth) in enumerate(pairs))
        line(label, result); line("Signal", result.rolling(p["signal"]).mean(), "#f5b84b"); guide(0)
    elif key == "ichimoku":
        conversion = (high.rolling(p["conversion"]).max() + low.rolling(p["conversion"]).min()) / 2
        base = (high.rolling(p["base"]).max() + low.rolling(p["base"]).min()) / 2
        span_b = (high.rolling(p["span"]).max() + low.rolling(p["span"]).min()) / 2
        shift = p["displacement"] - 1
        line("Conversion (Tenkan)", conversion, "#4dabf7")
        line("Base (Kijun)", base, "#ff5c7a")
        line("Leading Span A", (conversion + base) / 2, "#12b886", offset=shift)
        lines[-1]["cloud"] = "a"
        line("Leading Span B", span_b, "#ff5c7a", offset=shift)
        lines[-1]["cloud"] = "b"
        line("Lagging Close (Chikou)", close, "#b197fc", offset=-shift)
    elif key in ("kama", "mcginley"):
        result = pd.Series(np.nan, index=df.index)
        seed = close.rolling(length).mean()
        efficiency = divide((close - close.shift(length)).abs(), close.diff().abs().rolling(length).sum()).fillna(0)
        smooth = (efficiency * (2 / (p.get("fast", 2) + 1) - 2 / (p.get("slow", 30) + 1)) + 2 / (p.get("slow", 30) + 1)) ** 2
        for i in range(length - 1, len(df)):
            previous = result.iloc[i-1] if i else np.nan
            if not np.isfinite(previous): result.iloc[i] = seed.iloc[i]
            elif key == "kama": result.iloc[i] = previous + smooth.iloc[i] * (close.iloc[i] - previous)
            else:
                divisor = p["factor"] * length * (close.iloc[i] / previous) ** 4 if previous else 1
                result.iloc[i] = previous + (close.iloc[i] - previous) / max(1, divisor)
        line(label, result)
    elif key == "psar":
        result = pd.Series(np.nan, index=df.index)
        if len(df) > 1:
            rising = close.iloc[1] >= close.iloc[0]
            sar = low.iloc[0] if rising else high.iloc[0]
            extreme = high.iloc[1] if rising else low.iloc[1]
            acceleration = p["start"]
            for i in range(1, len(df)):
                if i > 1: sar += acceleration * (extreme - sar)
                if rising:
                    sar = min(sar, low.iloc[i-1], low.iloc[max(0, i-2)])
                    if low.iloc[i] < sar:
                        rising, sar, extreme, acceleration = False, max(extreme, high.iloc[i]), low.iloc[i], p["start"]
                    elif high.iloc[i] > extreme:
                        extreme = high.iloc[i]; acceleration = min(p["maximum"], acceleration + p["increment"])
                else:
                    sar = max(sar, high.iloc[i-1], high.iloc[max(0, i-2)])
                    if high.iloc[i] > sar:
                        rising, sar, extreme, acceleration = True, min(extreme, low.iloc[i]), high.iloc[i], p["start"]
                    elif low.iloc[i] < extreme:
                        extreme = low.iloc[i]; acceleration = min(p["maximum"], acceleration + p["increment"])
                result.iloc[i] = sar
        line(label, result, "#f5b84b")
        lines[-1]["pointOnly"] = True
    elif key == "fisher":
        hi, lo = hl2.rolling(length).max(), hl2.rolling(length).min()
        normalized = (divide(hl2 - lo, hi - lo) - 0.5).mask(hi == lo, 0)
        result = pd.Series(np.nan, index=df.index); value, previous = 0.0, 0.0
        for i in range(length - 1, len(df)):
            value = min(0.999, max(-0.999, 0.66 * normalized.iloc[i] + 0.67 * value))
            previous = 0.5 * math.log((1 + value) / (1 - value)) + 0.5 * previous
            result.iloc[i] = previous
        line("Fisher", result); line("Trigger", result.shift(), "#f5b84b"); guide(0)
    elif key == "choppiness":
        span = high.rolling(length).max() - low.rolling(length).min()
        line(label, 100 * np.log10(divide(tr.rolling(length).sum(), span)) / math.log10(length)); guide(61.8); guide(38.2)
    elif key in ("chandelier", "chandekroll"):
        width = rma(tr, length) * p["multiplier"]
        long_stop = high.rolling(length).max() - width
        short_stop = low.rolling(length).min() + width
        if key == "chandekroll":
            long_stop = long_stop.rolling(p["stop"]).max(); short_stop = short_stop.rolling(p["stop"]).min()
        line("Long stop", long_stop, "#12b886"); line("Short stop", short_stop, "#ff5c7a")
    elif key == "volstop":
        atr = rma(tr, length) * p["multiplier"]
        result = pd.Series(np.nan, index=df.index); rising, extreme, stop = True, None, None
        for i in range(length - 1, len(df)):
            price, width = close.iloc[i], atr.iloc[i]
            if stop is None: extreme, stop = price, price - width
            extreme = max(extreme, price) if rising else min(extreme, price)
            stop = max(stop, extreme - width) if rising else min(stop, extreme + width)
            if rising and price < stop or not rising and price > stop:
                rising = not rising; extreme = price; stop = price - width if rising else price + width
            result.iloc[i] = stop
        line(label, result); lines[-1]["colors"] = np.where(close >= result, "#12b886", "#ff5c7a")
    elif key in ("adr", "pivotstandard"):
        days, daily = completed_daily(df)
        if key == "adr": line(label, days.map((daily.high - daily.low).rolling(length).mean().shift()))
        else:
            h, l, c = daily.high.shift(), daily.low.shift(), daily.close.shift(); pivot = (h + l + c) / 3
            for name, values, color in [("P", pivot, "#f5b84b"), ("R1", 2*pivot-l, "#ff5c7a"), ("S1", 2*pivot-h, "#12b886"),
                                        ("R2", pivot+h-l, "#ff5c7a"), ("S2", pivot-h+l, "#12b886"),
                                        ("R3", h+2*(pivot-l), "#ff5c7a"), ("S3", l-2*(h-pivot), "#12b886")]:
                line(name, days.map(values), color)
    elif key == "eom": line(label, divide(hl2.diff() * (high - low) * p["scale"], volume).rolling(length).mean()); guide(0)
    elif key == "massindex":
        spread = ema(high-low, p["smoothing"])
        line(label, divide(spread, ema(spread, p["smoothing"])).rolling(length).sum()); guide(27); guide(26.5)
    elif key == "coppock":
        result = ((divide(close, close.shift(p["fast"])) - 1) + (divide(close, close.shift(p["slow"])) - 1)) * 100
        line(label, wma(result, p["smoothing"])); guide(0)
    elif key == "kst":
        result = sum(weight * ((divide(close, close.shift(p["roc" + str(weight)])) - 1) * 100).rolling(p["smooth" + str(weight)]).mean() for weight in range(1, 5))
        line("KST", result); line("Signal", result.rolling(p["signal"]).mean(), "#f5b84b"); guide(0)
    elif key in ("ppo", "priceosc"):
        if key == "ppo":
            result = divide(ema(close, p["fast"]) - ema(close, p["slow"]), ema(close, p["slow"])) * 100
            line(label, result); line("Signal", ema(result, p["signal"]), "#f5b84b")
        else: line(label, close.rolling(p["fast"]).mean() - close.rolling(p["slow"]).mean())
        guide(0)
    elif key == "connors":
        streak = pd.Series(0.0, index=df.index)
        for i in range(1, len(df)):
            change = close.iloc[i] - close.iloc[i-1]
            streak.iloc[i] = max(0, streak.iloc[i-1]) + 1 if change > 0 else min(0, streak.iloc[i-1]) - 1 if change < 0 else 0
        change = close.pct_change() * 100
        rank = change.rolling(p["rank"] + 1).apply(lambda x: (x[:-1] < x[-1]).mean() * 100, raw=True)
        line(label, (wilder_rsi(close, length) + wilder_rsi(streak, p["streak"]) + rank) / 3); guide(90); guide(10)
    elif key in ("nvi", "pvi"):
        active = volume < volume.shift() if key == "nvi" else volume > volume.shift()
        result = (1 + close.pct_change().where(active, 0).fillna(0)).cumprod() * 1000
        line(label, result); line("Signal", ema(result, length), "#f5b84b")
    elif key in ("netvolume", "updownvolume", "volume24h", "relativevoltime"):
        if key == "netvolume": line(label, np.sign(close.diff()).fillna(0) * volume, kind="histogram")
        elif key == "updownvolume":
            line("Up volume (candle estimate)", volume.where(close > df.open, 0) + volume.where(close == df.open, 0) / 2, "#12b886", "histogram")
            line("Down volume (candle estimate)", -volume.where(close < df.open, 0) - volume.where(close == df.open, 0) / 2, "#ff5c7a", "histogram")
        elif key == "volume24h":
            timed_volume = pd.Series(volume.values, index=pd.to_datetime(df.time, unit="s", utc=True))
            result = timed_volume.rolling("24h").sum(); result.index = df.index
            result = result.where(df.time - df.time.iloc[0] >= 86400)
            line(label, result)
        else:
            clock = (df.time % 86400).astype(int)
            average = volume.groupby(clock).transform(lambda s: s.shift().rolling(length).mean())
            line(label, divide(volume, average)); guide(1)
    elif key in ("performance", "median", "twap", "dpo"):
        if key == "performance": result = (divide(close, close.shift(length)) - 1) * 100
        elif key == "median": result = close.rolling(length).median()
        elif key == "twap":
            days = (df.time // 86400).astype(int); result = typical.groupby(days).cumsum() / (df.groupby(days).cumcount() + 1)
        else: result = close.shift(length // 2 + 1) - close.rolling(length).mean()
        line(label, result)
    elif key in ("smi", "smiergodic", "smiosc", "pmo"):
        if key == "smi":
            h, l = high.rolling(length).max(), low.rolling(length).min()
            result = 200 * divide(ema(ema(close-(h+l)/2, p["smoothing"]), p["smoothing"]), ema(ema(h-l, p["smoothing"]), p["smoothing"]))
        elif key == "pmo":
            result = (close.pct_change() * 100).ewm(alpha=2/length, adjust=False, min_periods=length).mean().ewm(alpha=2/p["smoothing"], adjust=False, min_periods=p["smoothing"]).mean() * 10
        else:
            change = close.diff(); result = divide(ema(ema(change, p["fast"]), p["slow"]), ema(ema(change.abs(), p["fast"]), p["slow"]))
        signal = ema(result, p["signal"])
        if key == "smiosc": line(label, result-signal, kind="histogram")
        else: line(label, result); line("Signal", signal, "#f5b84b")
        guide(0)
        if key == "smi": guide(40); guide(-40)
    elif key in ("rankcorr", "rciribbon", "trendstrength"):
        if key == "rciribbon":
            for name, color in [("short", "#4dabf7"), ("medium", "#f5b84b"), ("long", "#12b886")]: line(name.title(), rank_correlation(close, p[name]), color)
        elif key == "rankcorr": line(label, rank_correlation(close, length))
        else:
            line(label, close.rolling(length).apply(lambda x: 0 if np.std(x) == 0 else np.corrcoef(np.arange(length), x)[0, 1], raw=True))
        guide(0)
    elif key == "alligator":
        for name, color in [("jaw", "#4dabf7"), ("teeth", "#ff5c7a"), ("lips", "#12b886")]:
            line(name.title(), rma(hl2, p[name]), color, offset=p[name + "_shift"])
    elif key in ("fractals", "pivothighlow"):
        left, right = (length, length) if key == "fractals" else (p["left"], p["right"])
        hi, lo = confirmed_pivots(high, low, left, right)
        for name, values, color, position in [("Confirmed high", hi, "#ff5c7a", "aboveBar"), ("Confirmed low", lo, "#12b886", "belowBar")]:
            line(name, values.ffill(), color)
            lines[-1]["markers"] = [{"time": int(df.time.iloc[i]), "text": name, "shape": "circle", "position": position, "color": color} for i in range(len(df)) if np.isfinite(values.iloc[i])]
    elif key in ("maribbon", "macross"):
        if key == "maribbon":
            for name, color in [("length1", "#4dabf7"), ("length2", "#f5b84b"), ("length3", "#12b886"), ("length4", "#b197fc")]: line("SMA " + str(p[name]), close.rolling(p[name]).mean(), color)
        else:
            fast, slow = close.rolling(p["fast"]).mean(), close.rolling(p["slow"]).mean()
            line("Fast SMA", fast); line("Slow SMA", slow, "#f5b84b")
            difference = fast - slow; up = (difference > 0) & (difference.shift() <= 0); down = (difference < 0) & (difference.shift() >= 0)
            lines[0]["markers"] = [{"time": int(df.time.iloc[i]), "text": "Bull cross" if up.iloc[i] else "Bear cross", "shape": "arrowUp" if up.iloc[i] else "arrowDown", "position": "belowBar" if up.iloc[i] else "aboveBar", "color": "#12b886" if up.iloc[i] else "#ff5c7a"} for i in range(len(df)) if up.iloc[i] or down.iloc[i]]
    elif key == "rvi":
        def weighted(s): return (s + 2*s.shift() + 2*s.shift(2) + s.shift(3)) / 6
        result = divide(weighted(close-df.open).rolling(length).mean(), weighted(high-low).rolling(length).mean())
        line(label, result); line("Signal", weighted(result), "#f5b84b"); guide(0)
    elif key == "relativevolatility":
        deviation = close.rolling(length).std()
        up = rma(deviation.where(close.diff() > 0, 0).where(deviation.notna()), p["smoothing"])
        down = rma(deviation.where(close.diff() < 0, 0).where(deviation.notna()), p["smoothing"])
        line(label, (100 * divide(up, up+down)).mask(up+down == 0, 50)); guide(50)
    elif key == "bbtrend":
        short_basis, long_basis = close.rolling(p["short"]).mean(), close.rolling(p["long"]).mean()
        short_width, long_width = close.rolling(p["short"]).std(ddof=0)*p["deviation"], close.rolling(p["long"]).std(ddof=0)*p["deviation"]
        lower = (short_basis-short_width-long_basis+long_width).abs()
        upper = (short_basis+short_width-long_basis-long_width).abs()
        line(label, divide(lower-upper, short_basis)*100, kind="histogram"); guide(0)
    elif key == "klinger":
        trend = pd.Series(np.where((high+low+close).diff() > 0, 1.0, -1.0), index=df.index)
        dm = high-low; cumulative = pd.Series(np.nan, index=df.index); cumulative.iloc[0] = dm.iloc[0]
        for i in range(1, len(df)):
            cumulative.iloc[i] = cumulative.iloc[i-1]+dm.iloc[i] if trend.iloc[i] == trend.iloc[i-1] else dm.iloc[i-1]+dm.iloc[i]
        force = volume * (2 * (divide(dm, cumulative) - 1)).fillna(0) * trend * 100
        result = ema(force, p["fast"]) - ema(force, p["slow"])
        line(label, result); line("Signal", ema(result, p["signal"]), "#f5b84b"); guide(0)
    else:
        raise ValueError("Unsupported indicator: " + key)
    return lines
