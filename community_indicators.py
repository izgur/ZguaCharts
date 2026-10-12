# This Source Code Form is subject to the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, obtain one at
# https://mozilla.org/MPL/2.0/.
# Python adaptations of public formulas by QuantNomad (UT Bot; credits HPotter
# and Yo_adriiiiaan), LazyBear, ChrisMoody / Larry Williams, KivancOzbilgic.
# Original publication URLs, versions and differences: docs/community-indicators.md.
"""Source-reviewed ports. No Pine execution, trading, or hidden-script replicas."""
import numpy as np
import pandas as pd


def entry(label, defaults, parameters, slug, author, overlay=False):
    return {"label": label, "defaults": defaults, "parameters": parameters,
            "placement": "overlay" if overlay else "pane", "author": author,
            "sourceUrl": "https://www.tradingview.com/script/" + slug + "/",
            "licence": "MPL-2.0 (TradingView default for public source; Python adaptation)",
            "origin": "Source-reviewed Python formula port; chart styling differs; no bit-for-bit parity claim."}


CATALOG = {
    "utbot": entry("UT Bot (QuantNomad) - Python", {"length": 10, "sensitivity": 1.0, "heikin_ashi": 0},
                   "Wilder ATR trailing stop; crossover markers (live candle can change); Heikin Ashi uses OHLC4; no candle recolouring",
                   "n8ss8BID-UT-Bot-Alerts", "QuantNomad; HPotter; Yo_adriiiiaan", True),
    "squeeze_lb": entry("Squeeze Momentum (LazyBear, corrected) - Python", {"length": 20, "deviation": 2.0, "kc_length": 20, "multiplier": 1.5, "true_range": 1, "legacy_bb": 0},
                        "SMA BB/KC; regression momentum; author-corrected BB multiplier (legacy_bb=1 reproduces published typo); squeeze state +1/on, -1/released, 0/neither",
                        "nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear", "LazyBear"),
    "wavetrend_lb": entry("WaveTrend (LazyBear) - Python", {"channel": 10, "average": 21, "signal": 4},
                          "HLC3; recursive EMA; deviation scale 0.015; SMA signal; WT difference and +/-53/60 guides",
                          "2KE8wTuF-Indicator-WaveTrend-Oscillator-WT", "LazyBear"),
    "vixfix": entry("Williams Vix Fix (ChrisMoody) - Python", {"length": 22, "bb_length": 20, "deviation": 2.0, "lookback": 50, "high_factor": 0.85, "low_factor": 1.01},
                    "Rolling close-high/low distance percent; BB upper; rolling extreme factors (not statistical percentiles); guides always shown; not actual VIX",
                    "og7JPrRA-CM-Williams-Vix-Fix-Finds-Market-Bottoms", "ChrisMoody; Larry Williams"),
    "supertrend_kivanc": entry("Supertrend (KivancOzbilgic) - Python", {"length": 10, "multiplier": 3.0, "atr_sma": 0, "source_close": 0},
                               "HL2 or close; Wilder ATR or SMA true range; original trend recurrence and reversal markers; chart timeframe only; no background fills",
                               "r6dAP7yi", "KivancOzbilgic; everget / Alex Orekhov", True),
}


def ema(series, length):
    return series.ewm(span=length, adjust=False, ignore_na=True).mean().where(series.notna())


def rma(series, length):
    seed = series.rolling(length).mean()
    first = seed.first_valid_index()
    if first is None:
        return series * np.nan
    values = series.copy()
    values.iloc[:first] = np.nan
    values.iloc[first] = seed.iloc[first]
    return values.ewm(alpha=1 / length, adjust=False, ignore_na=True).mean()


def regression(series, length):
    weights = np.arange(length) - (length - 1) / 2
    slope = series.rolling(length).apply(lambda values: np.dot(values, weights) / np.dot(weights, weights), raw=True)
    return series.rolling(length).mean() + slope * (length - 1) / 2


def calculate(df, key, p):
    close, high, low = (df[name] for name in ("close", "high", "low"))
    length = p.get("length", 14)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    lines = []
    def line(name, values, color="#4dabf7", kind="line", markers=None, colors=None):
        lines.append({"name": name, "values": values, "color": color, "type": kind, "markers": markers or [], "colors": colors})
    def guide(value):
        lines.append({"name": str(value), "values": pd.Series(value, index=df.index), "color": "#65737e", "type": "line", "guide": True})
    def marker(i, buy):
        return {"time": int(df["time"].iloc[i]), "position": "belowBar" if buy else "aboveBar",
                "shape": "arrowUp" if buy else "arrowDown", "color": "#12b886" if buy else "#ff5c7a",
                "text": "Buy" if buy else "Sell"}
    if key == "utbot":
        source = (df["open"] + high + low + close) / 4 if p["heikin_ashi"] else close
        loss = rma(tr, length) * p["sensitivity"]
        stop = pd.Series(np.nan, index=df.index); markers = []
        for i in range(len(df)):
            previous = stop.iloc[i - 1] if i and np.isfinite(stop.iloc[i - 1]) else 0.0
            price = source.iloc[i]; prior_price = source.iloc[i - 1] if i else np.nan
            if not np.isfinite(loss.iloc[i]) or not np.isfinite(price):
                continue
            if price > previous and prior_price > previous:
                value = max(previous, price - loss.iloc[i])
            elif price < previous and prior_price < previous:
                value = min(previous, price + loss.iloc[i])
            else:
                value = price - loss.iloc[i] if price > previous else price + loss.iloc[i]
            stop.iloc[i] = value
            if i and np.isfinite(stop.iloc[i - 1]):
                if price > value and prior_price <= stop.iloc[i - 1]: markers.append(marker(i, True))
                elif price < value and prior_price >= stop.iloc[i - 1]: markers.append(marker(i, False))
        line("UT ATR trailing stop", stop, "#f5b84b", markers=markers)
    elif key == "squeeze_lb":
        kc_length = p["kc_length"]
        basis = close.rolling(length).mean()
        width = close.rolling(length).std(ddof=0) * (p["multiplier"] if p["legacy_bb"] else p["deviation"])
        kc = close.rolling(kc_length).mean()
        span = tr.copy() if p["true_range"] else high - low
        if p["true_range"] and len(span): span.iloc[0] = np.nan
        kc_width = span.rolling(kc_length).mean() * p["multiplier"]
        valid = basis.notna() & width.notna() & kc.notna() & kc_width.notna()
        active = (basis - width > kc - kc_width) & (basis + width < kc + kc_width)
        released = (basis - width < kc - kc_width) & (basis + width > kc + kc_width)
        state = pd.Series(np.where(active, 1., np.where(released, -1., 0.)), index=df.index).where(valid)
        center = ((high.rolling(kc_length).max() + low.rolling(kc_length).min()) / 2 + kc) / 2
        momentum = regression(close - center, kc_length)
        previous = momentum.shift().fillna(0)
        colors = np.where(momentum > 0, np.where(momentum > previous, "#00ff00", "#008000"), np.where(momentum < previous, "#ff0000", "#800000"))
        line("Squeeze momentum", momentum, kind="histogram", colors=colors)
        line("Squeeze state", state, "#f5b84b"); guide(0)
    elif key == "wavetrend_lb":
        price = (high + low + close) / 3
        basis = ema(price, p["channel"])
        deviation = ema((price - basis).abs(), p["channel"])
        wt = ema((price - basis) / (0.015 * deviation.replace(0, np.nan)), p["average"])
        signal = wt.rolling(p["signal"]).mean()
        line("WT1", wt, "#12b886"); line("WT2", signal, "#ff5c7a"); line("WT difference", wt - signal)
        for value in (0, 60, 53, -53, -60): guide(value)
    elif key == "vixfix":
        highest = close.rolling(length).max()
        wvf = 100 * (highest - low) / highest.replace(0, np.nan)
        middle = wvf.rolling(p["bb_length"]).mean()
        upper = middle + wvf.rolling(p["bb_length"]).std(ddof=0) * p["deviation"]
        range_high = wvf.rolling(p["lookback"]).max() * p["high_factor"]
        colors = np.where((wvf >= upper) | (wvf >= range_high), "#00ff00", "#808080")
        line("Williams Vix Fix", wvf, kind="histogram", colors=colors)
        line("Upper band", upper, "#3bc9db")
        line("Range high factor", range_high, "#f5b84b")
        line("Range low factor", wvf.rolling(p["lookback"]).min() * p["low_factor"], "#f5b84b")
    elif key == "supertrend_kivanc":
        source = close if p["source_close"] else (high + low) / 2
        atr_values = tr.rolling(length).mean() if p["atr_sma"] else rma(tr, length)
        if p["atr_sma"]:
            raw_tr = tr.copy(); raw_tr.iloc[0] = np.nan
            atr_values = raw_tr.rolling(length).mean()
        up, down = source - atr_values * p["multiplier"], source + atr_values * p["multiplier"]
        trend = 1; upwards = pd.Series(np.nan, index=df.index); downwards = upwards.copy(); markers = []
        for i in range(len(df)):
            if not np.isfinite(up.iloc[i]) or not np.isfinite(down.iloc[i]): continue
            prior_up = up.iloc[i - 1] if i and np.isfinite(up.iloc[i - 1]) else up.iloc[i]
            prior_down = down.iloc[i - 1] if i and np.isfinite(down.iloc[i - 1]) else down.iloc[i]
            if i and close.iloc[i - 1] > prior_up: up.iloc[i] = max(up.iloc[i], prior_up)
            if i and close.iloc[i - 1] < prior_down: down.iloc[i] = min(down.iloc[i], prior_down)
            previous = trend
            if trend == -1 and close.iloc[i] > prior_down: trend = 1
            elif trend == 1 and close.iloc[i] < prior_up: trend = -1
            if trend == 1: upwards.iloc[i] = up.iloc[i]
            else: downwards.iloc[i] = down.iloc[i]
            if trend != previous: markers.append(marker(i, trend == 1))
        line("Up Trend", upwards, "#12b886", markers=markers)
        line("Down Trend", downwards, "#ff5c7a")
    else:
        raise ValueError("Unknown community indicator: " + key)
    return lines
