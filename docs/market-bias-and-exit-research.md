# Direction And Entry Quality

The header displays a fixed, rule-based directional score on 4h, 1h and 15m.
An arrow identifies the direction; the number is not a percentage probability.

| Score | Meaning |
| --- | --- |
| 0-40 | Bearish bias |
| 41-59 | Mixed / neutral |
| 60-100 | Bullish bias |

The signed component blocks are trend (45 points), momentum (25), price
structure (20), and volume flow (10). Their sum is mapped from -100..100 to
0..100. EMA relationships share one capped trend block, rather than being
counted as multiple independent votes. The canonical parameters are EMA
20/50/200, RSI 14, MACD 12/26/9, ATR/ADX 14, CMF 20 and a prior 20-bar range.
Changing the selected tactic does not redefine the directional model.

The 15m badge also displays ENTER, WAIT, LATE or UNAVAILABLE. This is a fresh
entry review, independent of historical simulated positions or account positions.
Click a badge for component values, data timestamps, selected tactic entry
checks, structure-based risk plan, modeled costs and blockers.

ENTER requires all of the following:

- A directional score outside the neutral band and supporting 4h/1h bias.
- At least 40 recent contiguous closed 5m candles, with a fresh final close.
- The last 5m close breaks the preceding 5m high/low and is on the correct
  side of its EMA20.
- ADX at least 18 and price within 1.5 signal ATR of EMA20; RSI cannot be
  above 75 for a long entry or below 25 for a short entry.
- Structure-based stop distance covers at least three modeled round-trip
  costs and is no more than three signal ATR away.
- An observed opposing structure objective leaves at least 1.5R.
- A separate entry-quality rubric reaches 75/100 (not probability).
- Outside replay, a current quote less than 30 seconds old still respects
  invalidation and 1.5R room, and has not drifted more than 0.5 ATR from the
  closed trigger. Live ticks recheck this guard without changing closed-bar bias.

A breakout without an observed opposing objective is WAIT for a retest,
not an invented 2R profit forecast. A strong directional score can be LATE.
Warmup/data gaps produce UNAVAILABLE, not neutral 50. Closed bars are used
on refresh and replay; live forming candles do not become settled evidence.
Short bias does not enable short positions in a long-only paper tactic.

The default modeled costs are taker 0.055% per side and 2 bps slippage per
side, enforced by the isolated tactic worker. Funding and spread are not
included. This is an experimental decision aid, not calibrated odds, a
guarantee, or an automatic trade instruction. Existing paper entry/exit
rules and candidate states are unchanged.

# Exit Research

Three opt-in RelativeStrengthV2 variants are available in the tactic selector:

| Variant | Exit Policy |
| --- | --- |
| Staged profits | Sell 25% of original size at 1R, another 25% at 2R, trail the remaining 50% |
| Adaptive trail | Cost-aware breakeven after 1R; 1.5 ATR trail, tightened to 0.75 ATR after 2R |
| Momentum exit | Profit-protecting trail plus two closed 5m EMA20 failures while profit exceeds 1R |

R is the original entry-to-stop price distance. All variants retain the
baseline entry rules and normal rule exits. They require complete real 5m
execution history after the 200-signal-bar warmup; missing bars are rejected.
On an ambiguous 5m candle the existing stop is checked before targets.
Trail updates use only information available at a closed execution candle
and apply to the next candle. Cost-aware breakeven includes entry/exit fees
and modeled exit slippage. There is no promise that a real stop fills at its
intended price; adverse opening gaps are modeled.

Partial exit cash, fees, slippage, daily realized PnL and remaining exposure
are accounted for separately, then aggregated into one position/trade record.
Partial fills remain visible in the result and have chart markers. Research
parameters are available in tactic source copies and editable personal tactics.
No variant is promoted or attached to an existing paper candidate automatically.

Run a frozen chronological comparison:

```powershell
node cli/research_exit_quality.js frozen-input.json reports/research-campaigns/exit-quality.json
```

Or inspect only the latest contiguous 5m segment, aggregated into real 15m bars:

```powershell
node cli/research_exit_quality.js frozen-input.json reports/research-campaigns/exit-quality-recent.json --recent-complete
```

Selection uses the first 60% for training, freezes one choice before checking
the next 20% for validation, and leaves the final 20% unevaluated. Eligibility
requires 150 closed training trades, 50 validation trades, positive returns
after costs in both, and validation improvement over baseline. These are
minimum research gates, not proof of statistical independence or profitability.
Future data must not change the frozen training or validation results.

Reports include average giveback in R and capture relative to a closed-price
MFE proxy. The latter can exceed 100% on intrabar target fills; small favorable
excursions can also make percentage ratios unstable. Compare giveback R and
net performance, not capture percentage alone. Earlier exits change occupancy
and can change which later entry opportunities are actually traded.

The 2026-10-08 full saved snapshot was blocked by insufficient execution
coverage. The recent complete segment is exploratory only: its training-selected
variant was negative in validation and sample counts were inadequate. No
winner, probability calibration, holdout release or paper promotion is claimed.

The recent subwindow also overlaps the earlier full snapshot's reserved
holdout period. Its results are explicitly flagged as exploratory reuse, not
new out-of-sample evidence for that earlier experiment. The new subwindow's
own final 20% was not evaluated, but a fresh forward holdout is required before
any promotion; re-splitting previously reserved data does not reset its provenance.
