from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import data_source as data

def stamp(value): return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
def daily(start,end):
    return [{'time':time,'open':i+1,'high':i+3,'low':i,'close':i+2,'volume':10} for i,time in enumerate(range(stamp(start),stamp(end),86400))]

class CalendarTests(unittest.TestCase):
    def test_months_include_leap_day_and_partial_current_month(self):
        rows=daily('2024-01-01','2024-03-16')
        with patch.object(data.time,'time',return_value=stamp('2024-03-15T12:00:00')):
            result=data.aggregate_calendar_candles(rows,'1M')
        self.assertEqual([r['time'] for r in result],[stamp('2024-01-01'),stamp('2024-02-01'),stamp('2024-03-01')])
        self.assertEqual([r['volume'] for r in result],[310,290,150])
        self.assertEqual(result[1]['open'],32);self.assertEqual(result[1]['close'],61)

    def test_years_use_calendar_boundaries_not_365_day_buckets(self):
        with patch.object(data.time,'time',return_value=stamp('2025-01-01')):
            result=data.aggregate_calendar_candles(daily('2023-01-01','2025-01-01'),'1y')
        self.assertEqual([r['volume'] for r in result],[3650,3660])
        self.assertEqual(result[1]['time'],stamp('2024-01-01'))

    def test_missing_history_is_not_fabricated(self):
        rows=daily('2024-01-01','2024-03-01');rows=[r for r in rows if r['time']!=stamp('2024-02-10')]
        with patch.object(data.time,'time',return_value=stamp('2024-03-01')):
            result=data.aggregate_calendar_candles(rows,'1M')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['time'],stamp('2024-01-01'))

    def test_fetch_uses_daily_and_does_not_send_calendar_interval_to_provider(self):
        rows=daily('2024-01-01','2024-03-01')
        with patch.object(data.time,'time',return_value=stamp('2024-03-01')),patch.object(data,'fetch_bybit_candles_with_diagnostics',return_value=(rows,{})) as fetch:
            result=data.fetch_candles('bybit','BTCUSDT','1M',2)
        self.assertEqual(fetch.call_args[0][1],'1d');self.assertEqual(len(result['candles']),2)
        self.assertTrue(result['diagnostics']['calendarAggregation'])

    def test_closed_months_exclude_current_calendar_month(self):
        rows=daily('2024-01-01','2024-03-16')
        def batch(symbol,interval,end): return [data.bybit_candle_to_row(r) for r in rows if r['time']*1000<=end]
        with patch.object(data,'request_bybit_kline_batch',side_effect=batch):
            result=data.fetch_closed_candles('bybit','BTCUSDT','1M',stamp('2024-03-15'),2)
        self.assertEqual([r['time'] for r in result],[stamp('2024-01-01'),stamp('2024-02-01')])

if __name__=='__main__':unittest.main()
