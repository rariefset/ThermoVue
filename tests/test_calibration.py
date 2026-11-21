import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from thermovue_service import Settings, create_app


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'config.json'
        self.original = {'sensor1': {'offset': 1.5, 'low_alarm': 10, 'high_alarm': 75},
                         'sensor2': {'offset': 0, 'low_alarm': 10, 'high_alarm': 75},
                         'modbus': {'slave_id': 1, 'sensor2_slave_id': 5}}
        self.path.write_text(json.dumps(self.original))
        self.settings = Settings(self.path)
        self.state = {'s1': 24.0, 's2': 30.0, 's1_connected': True, 's2_connected': True,
                      'timestamps': [time.time(), time.time()]}
        owner = self
        class Monitor:
            def state(self): return copy.deepcopy(owner.state)
        self.client = create_app(self.settings, Monitor()).test_client()

    def tearDown(self): self.temp.cleanup()

    def test_reference_uses_raw_and_persists_without_modbus_changes(self):
        r = self.client.post('/api/calibration', json={'sensor': 1, 'reference_c': 25.0})
        self.assertEqual(r.status_code, 200)
        saved = Settings(self.path).get()
        self.assertEqual(saved['sensor1']['offset'], 1.0)
        self.assertEqual(saved['sensor1']['calibration']['raw_c'], 24.0)
        self.assertEqual(saved['modbus'], self.original['modbus'])
        self.assertEqual(saved['sensor2'], self.original['sensor2'])

    def test_reset_and_alarm_update_retain_calibration(self):
        self.settings.calibrate({'sensor': 2, 'reference_c': 32.5}, self.state)
        self.settings.update({'sensor2': {'low_alarm': 0, 'high_alarm': 80}})
        self.assertEqual(self.settings.get()['sensor2']['offset'], 2.5)
        self.settings.calibrate({'sensor': 2, 'reset': True}, {})
        self.assertEqual(Settings(self.path).get()['sensor2']['offset'], 0)

    def test_invalid_payload_does_not_change_disk(self):
        for payload in [{}, [], {'sensor': True, 'reference_c': 25},
                        {'sensor': 3, 'reference_c': 25}, {'sensor': 1, 'reference_c': True},
                        {'sensor': 1, 'reference_c': None}, {'sensor': 1, 'reference_c': 'NaN'},
                        {'sensor': 1, 'reference_c': 'inf'}, {'sensor': 1, 'reset': False},
                        {'sensor': 1, 'reference_c': 25, 'offset': 5}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/calibration', json=payload).status_code, 400)
                self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_stale_disconnected_and_future_readings_rejected(self):
        for timestamp, connected, raw in [(time.time()-10, True, 24),
                                           (time.time(), False, 24),
                                           (time.time(), True, None),
                                           (time.time()+10, True, 24)]:
            self.state.update(s1=raw, s1_connected=connected, timestamps=[timestamp, time.time()])
            self.assertEqual(self.client.post('/api/calibration', json={'sensor': 1, 'reference_c': 25}).status_code, 400)
            self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_origin_and_content_type(self):
        self.assertEqual(self.client.post('/api/calibration', json={'sensor': 1, 'reference_c': 25}, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/calibration', data='bad').status_code, 415)


if __name__ == '__main__': unittest.main()
