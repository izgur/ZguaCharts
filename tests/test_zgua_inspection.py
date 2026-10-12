import unittest
from flask import Flask
from services.zgua import blueprint, detection
from tests.test_zgua_divergence import ZguaTests

class InspectionTests(unittest.TestCase):
    def test_actual_study_values_and_confirmed_only(self):
        rows = ZguaTests().rows(2000)
        frame, oscillators, events = detection(rows)
        self.assertGreater(len(events), 80)
        app = Flask(__name__)
        app.register_blueprint(blueprint)
        client = app.test_client()
        event = next(item for item in events if item['studies'])
        response = client.post('/api/zgua/inspect', json={'candles': rows, 'event': event})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for study in data['studies']:
            index = frame.index[frame.time == event['pivotTime']][0]
            self.assertEqual(study['pivotValue'], oscillators[study['name']].iloc[index])
            connector = study['series'][1]['data']
            self.assertEqual(connector[0]['value'], study['firstValue'])
            self.assertEqual(connector[-1]['value'], study['pivotValue'])
        invalid = dict(event, confirmedAt=event['confirmedAt']+1)
        self.assertEqual(client.post('/api/zgua/inspect', json={'candles':rows,'event':invalid}).status_code, 400)

if __name__ == '__main__':
    unittest.main()
