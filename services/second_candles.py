"""Limited recent-trade seed for Bybit's live one-second chart, not deep history."""
import re
import math
import time
import threading
import requests

_cache = {}
_lock = threading.Lock()

def aggregate_trades(trades):
    rows = {}
    seen = set()
    for trade in sorted(trades, key=lambda item: int(item['time'])):
        identity = str(trade['execId'])
        if identity in seen:
            continue
        seen.add(identity)
        price, size = float(trade['price']), float(trade['size'])
        if not (math.isfinite(price) and math.isfinite(size) and price > 0 and size >= 0):
            continue
        second = int(trade['time']) // 1000
        row = rows.setdefault(second, dict(time=second, open=price, high=price, low=price, close=price, volume=0))
        row.update(high=max(row['high'], price), low=min(row['low'], price), close=price, volume=row['volume'] + size)
    return [rows[t] for t in sorted(rows)]

def fetch_recent_seconds(source, symbol, limit):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', symbol):
        raise ValueError('Invalid Bybit symbol')
    category = {'bybit':'linear', 'bybit_spot':'spot', 'bybit_inverse':'inverse'}[source]
    key = (source, symbol, max(1,int(limit)))
    with _lock:
        saved = _cache.get(key)
        if saved and time.time() - saved[0] < 1:
            return saved[1]
    response = requests.get('https://api.bybit.com/v5/market/recent-trade', params={'category':category, 'symbol':symbol, 'limit':60 if category=='spot' else 1000}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if payload.get('retCode') != 0:
        raise RuntimeError(payload.get('retMsg', 'Bybit recent trades unavailable'))
    trades = payload['result']['list']
    result = {'source':source, 'symbol':symbol, 'timeframe':'1s', 'candles':aggregate_trades(trades)[-max(1,int(limit)):],
              'diagnostics':{'liveOnly':True, 'seedTradeIds':[str(t['execId']) for t in trades],
                             'warnings':['Trade-built 1s; limited recent seed, first/last seed bars may be partial. No deep history; empty seconds are not fabricated.']}}
    with _lock:
        if len(_cache) >= 32:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.time(), result)
    return result
