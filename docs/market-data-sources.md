# Public chart feeds (2026-10-04)

Chart data is read-only. These providers do not change paper execution, enable
live trading, set leverage, or use trading credentials. CoinAPI is disabled at
the user's request; there are no CoinAPI calls or paid fallback requests.

## Search and contracts

One picker searches active instruments from Binance, Bybit, OKX, KuCoin,
Gate.io, Hyperliquid, Kraken and Coinbase, plus the existing Yahoo search.
Catalogs are cached for ten minutes and loaded independently, so one failed
provider does not prevent the other results from appearing.

Perpetuals display a `.P` suffix, for example `BTCUSDT.P`. Native identifiers
remain unchanged for API requests: OKX uses `BTC-USDT-SWAP`, KuCoin uses
`XBTUSDTM`, and Binance Coin-M uses `BTCUSD_PERP`. Hyperliquid's collateral
and native DEX prefix are retained; not every perpetual is USDT-denominated.

Default All/Crypto results omit dated futures and options. Futures/Options tabs
or explicit type filters reveal them. Dated futures are separate contracts,
not duplicate date slices. No synthetic continuous contract or rollover is
silently manufactured. Sources do not silently substitute for each other.

## Candle Requests

| Provider | Implemented markets | Native page size | Important limits |
| --- | --- | --- | --- |
| Binance | Spot, USD-M, Coin-M, options | Spot 1,000; derivatives 1,500 | Coin-M pages stay within a 200-day window |
| Bybit | Spot, linear USDT/USDC perpetuals and futures, inverse | 1,000 | Existing research and paper adapters unchanged |
| OKX | Spot, swaps, futures, options | 300 | Historical endpoint, backward cursor |
| KuCoin | Spot, futures | Spot 1,500; futures 200 | Unified v2; sub-8h futures history starts 2025-01-01 |
| Gate.io | Spot, USDT/BTC/USD1 perpetuals, delivery futures | Spot 1,000; derivatives 2,000 | Time-window pagination; empty settlement catalogs are allowed |
| Hyperliquid | All perpetual DEX catalogs and spot | 5,000 | Public candles retain only the latest 5,000 |
| Kraken | Spot, futures | Spot 720; futures batches of 1,000 | Spot REST cannot retrieve older than its recent 720-bar window; futures batch size is an application choice |
| Coinbase | Spot | 300 | 30m/4h/1w aggregate supported native intervals with pandas |
| CoinAPI | Disabled | None | No key, credits or paid requests used |

Charts start with up to 2,000 bars, fetch older pages at the left edge, and stop
at provider exhaustion or the application's 50,000-bar chart safety limit.
This is not a promise of unlimited history. Pagination cannot defeat exchange
retention limits, recover nonexistent trades, or access delisted contracts that
are absent from active catalogs. Kraken's free downloadable OHLCVT archives are
not automatically downloaded/imported by this change.

Live Bybit/Hyperliquid perpetual feeds retain their existing WebSockets.
Other feeds poll public REST every ten seconds after the previous refresh
finishes; refreshes merge into history rather than removing older loaded bars.
Indicators calculate from the displayed OHLCV array, keeping studies aligned
with history even when provider retention would make a second fetch shorter.
Historical signal snapshots continue to exclude unfinished candles.

## Verification

Public catalog/candle smoke tests succeeded for every newly added exchange.
Example active catalog sizes observed: OKX spot 1,143/swaps 500/options 2,884;
Gate spot 2,202/USDT perpetuals 1,027; KuCoin spot 997/futures 690;
Kraken spot 1,361/futures 226; Coinbase 513; Hyperliquid spot 330;
Binance options 1,602. Counts are observations, not stable constants.
Multi-page checks returned 1,501 candles from OKX spot, Gate spot/perpetuals,
KuCoin spot/futures, Kraken futures and Coinbase. Gate page boundaries are
aligned to candle buckets so inclusive endpoints do not exceed its page cap.
Some listed OKX options had no recent hourly candles. Gate inverse delivery
currently returned an empty catalog. Neither case is filled with fabricated bars.

Run `python scripts/probe_public_markets.py` for an explicit read-only check.

## Official References

- [Binance Spot](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Binance derivatives market data](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Binance options](https://developers.binance.info/docs/derivatives/options-trading/market-data/Test-Connectivity)
- [Bybit klines](https://bybit-exchange.github.io/docs/v5/market/kline)
- [Bybit instruments](https://bybit-exchange.github.io/docs/v5/market/instrument)
- [OKX V5](https://app.okx.com/docs-v5/en/)
- [KuCoin unified v2 klines](https://www.kucoin.com/docs-new/v2/rest/ua/get-klines)
- [Gate V4](https://www.gate.com/docs/developers/apiv4/en/)
- [Gate futures](https://www.gate.com/docs/developers/apiv4/en/futures/)
- [Hyperliquid info](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Hyperliquid perpetual DEX metadata](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
- [Kraken Spot OHLC](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)
- [Kraken futures candles](https://docs.kraken.com/api/docs/futures-api/charts/candles)
- [Kraken free OHLCVT archives](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data)
- [Coinbase Exchange candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)
- [CoinAPI REST](https://www.coinapi.io/products/market-data-api/docs/rest-api)
