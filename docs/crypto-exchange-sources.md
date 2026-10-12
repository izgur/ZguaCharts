# Crypto Exchange Sources

The chart now supports ten major crypto exchange brands, chosen for market relevance and usable public candle APIs, not as a precise or permanent popularity ranking:

Binance, Bybit, OKX, Gate.io, KuCoin, Kraken, Coinbase, Hyperliquid, Bitget and MEXC.

All sources are read-only. No private API keys, account access, order placement or leverage are enabled. Instrument catalogs are retrieved from each provider; symbol IDs and exchange identity are preserved instead of combining unrelated markets.

Bitget includes spot, USDT perpetuals, coin-margined perpetuals and USDC perpetuals. MEXC includes its public API-supported active spot instruments. Existing providers retain their supported spot, perpetual, dated-futures and options adapters. Availability varies by provider and region; API catalogs can contain fewer instruments than the exchange website.

Bitget uses the public v3 instruments and candle APIs. Recent pages use the candle endpoint; older pages use the history endpoint. History pages are capped at 100 rows. Native intervals are mapped explicitly. MEXC uses public spot v3 exchange information and klines, including its 60m and 1W names. Unsupported 3m MEXC and weekly Bitget bars are not advertised as native capabilities.

Pagination, caching, closed-bar filtering and polling remain shared with the existing chart adapters. Provider history caps still apply: for example, Kraken spot REST and Hyperliquid do not offer unlimited history.

Live MEXC checks returned only 500 rows for a documented 1,000-row request. The adapter therefore uses conservative 500-row windows so responses reach each requested end boundary; a 2,000-row live test confirmed current-hour coverage. Bitget daily pages are also capped to 90 days per request.

References:
- https://www.bitget.com/docs/catalog/market/market-data
- https://mexcdevelop.github.io/apidocs/spot_v3_en/
- https://www.coingecko.com/en/exchanges
