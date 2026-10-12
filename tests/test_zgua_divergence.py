import copy
import unittest
import numpy as np
import pandas as pd
from indicator_catalog import CATALOG, zgua_classify, zgua_events, zgua_pivots, confirmed_pivots
from indicators import build_indicator_payload, candles_to_frame, normalize_indicator_settings
from services.indicator_sources import source_payload


class ZguaTests(unittest.TestCase):
    def params(self):
        return dict(CATALOG['zgua']['defaults'], left=1, right=1, min_bars=2, max_bars=20, price_equal_pct=0, osc_equal_pct=0, min_osc_pct=0, max_events=200)

    def rows(self, n=160):
        return [{'time':1700000000+i*60,'open':100+5*np.sin(i/4), 'close':100+5*np.sin(i/4)+np.cos(i/13), 'high':102+5*np.sin(i/4), 'low':98+5*np.sin(i/4), 'volume':float(100+i%17)} for i in range(n)]

    def test_all_eight_geometric_classes(self):
        cases=[(True,100,90,20,30,'regular'),(False,100,110,80,70,'regular'),
               (True,100,110,30,20,'hidden'),(False,100,90,70,80,'hidden'),
               (True,100,100.1,20,30,'exaggerated'),(False,100,99.9,80,70,'exaggerated'),
               (True,100,90,20,20.1,'weak'),(False,100,110,80,80.1,'weak')]
        for bullish,a,b,c,d,expected in cases:
            self.assertEqual(zgua_classify(a,b,c,d,bullish,.2,.2,.5),expected)
        self.assertIsNone(zgua_classify(100,110,20,30,True,.2,.2,.5))
        self.assertIsNone(zgua_classify(100,100,20,20,True,.2,.2,.5))

    def test_confirmation_time_and_matching_pivot_samples(self):
        prices=[15,12,10,13,15,13,9,12,14,15]
        rows=[{'time':1700000000+i*60,'open':v,'close':v,'high':v+1,'low':v-1,'volume':100} for i,v in enumerate(prices)]
        df=candles_to_frame(rows); oscillator=pd.Series([50,40,20,40,50,40,30,40,50,50])
        events=zgua_events(df,{'Test':oscillator},self.params())
        match=next(event for event in events if event['kind']=='regular' and event['bullish'])
        self.assertEqual(match['firstTime'],rows[2]['time']);self.assertEqual(match['pivotTime'],rows[6]['time'])
        self.assertEqual(match['confirmedAt'],rows[8]['time'])
        self.assertEqual(zgua_events(df.iloc[:8],{'Test':oscillator.iloc[:8]},self.params()),[])

    def test_unfinished_last_bar_cannot_change_signals(self):
        rows=self.rows(); baseline=build_indicator_payload(rows,['zgua'])
        changed=copy.deepcopy(rows);changed[-1].update(open=500,high=1000,low=1,close=2,volume=1e9)
        updated=build_indicator_payload(changed,['zgua'])
        for a,b in zip(baseline['overlays'],updated['overlays']):
            self.assertEqual(a['markers'],b['markers']);self.assertEqual(a['segments'],b['segments'])

    def test_prefix_causality_and_individual_toggles(self):
        rows=self.rows(); full=build_indicator_payload(rows,['zgua'],settings={'zgua':{'max_events':200}})
        for length in (60,90,120):
            prefix=build_indicator_payload(rows[:length],['zgua'],settings={'zgua':{'max_events':200}})
            expected=[marker for series in full['overlays'] for marker in series['markers'] if marker['time']<=rows[length-1]['time']]
            actual=[marker for series in prefix['overlays'] for marker in series['markers']]
            self.assertEqual(sorted(actual,key=str),sorted(expected,key=str))
        off=build_indicator_payload(rows,['zgua'],settings={'zgua':{'regular_bullish':0}})
        self.assertFalse(any(marker['text'].startswith('RB ') for series in off['overlays'] for marker in series['markers']))

    def test_source_export_and_payload_metadata(self):
        rows=self.rows(); native=build_indicator_payload(rows,['zgua'])
        namespace={};exec(source_payload('zgua')['files'][0]['code'],namespace)
        exported=namespace['indicator'](rows)
        self.assertEqual([s['markers'] for s in native['overlays']],[s['markers'] for s in exported['overlays']])
        self.assertEqual([s['segments'] for s in native['overlays']],[s['segments'] for s in exported['overlays']])
        self.assertTrue(any(s['markers'] for s in native['overlays']))
        for series in native['overlays']:
            for event in series['events']: self.assertGreater(event['confirmedAt'],event['pivotTime'])
        with self.assertRaises(ValueError):normalize_indicator_settings({'zgua':{'min_bars':100,'max_bars':10}})

    def test_optional_patterns_are_closed_and_separate(self):
        rows=[{'time':1700000000+i*60,'open':v,'close':v,'high':v+1,'low':v-1,'volume':100} for i,v in enumerate([15,12,10,13,15,13,12,14,15])]
        rows[6].update(open=10,close=12,high=13,low=8)
        df=candles_to_frame(rows)
        events=zgua_events(df,{},dict(self.params(),sweep=1))
        sweep=next(event for event in events if event['kind']=='sweep' and event['bullish'])
        self.assertEqual(sweep['confirmedAt'],rows[7]['time'])
        self.assertEqual(sweep['studies'],[])
        self.assertFalse(any(event['confirmedAt']==rows[7]['time'] for event in zgua_events(df.iloc[:7],{},dict(self.params(),sweep=1))))
        rows[5].update(open=15,close=13,high=16,low=12)
        rows[6].update(open=12,close=16,high=17,low=8)
        engulf=zgua_events(candles_to_frame(rows),{},dict(self.params(),engulfing=1))
        self.assertTrue(any(event['kind']=='engulfing' and event['bullish'] and event['confirmedAt']==rows[7]['time'] for event in engulf))

    def test_ties_and_missing_oscillator_do_not_fabricate_divergence(self):
        rows=self.rows(30)
        for row in rows:row.update(open=100,high=101,low=99,close=100)
        self.assertEqual(zgua_events(candles_to_frame(rows),{'RSI':pd.Series(np.nan,index=range(30))},self.params()),[])

    def test_vectorized_pivots_match_reference(self):
        values=pd.Series(np.random.RandomState(123).normal(size=200))
        for left,right in ((1,1),(3,5),(10,2)):
            actual=zgua_pivots(values+1,values-1,left,right)
            expected=confirmed_pivots(values+1,values-1,left,right)
            for a,b in zip(actual,expected):np.testing.assert_allclose(a,b,equal_nan=True)


if __name__=='__main__':unittest.main()
