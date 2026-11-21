import copy
import importlib.util
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from collections import deque
from thermovue_calibration import apply_calibration, validate_points
from thermovue_service import Monitor, Settings, create_app

POINTS = [{'raw_c': 0, 'reference_c': 2}, {'raw_c': 50, 'reference_c': 55},
          {'raw_c': 100, 'reference_c': 101}]


class MultiPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'config.json'
        self.original = dict(sensor1=dict(offset=1, low_alarm=10, high_alarm=75),
                             sensor2=dict(offset=-2, low_alarm=10, high_alarm=75),
                             modbus=dict(slave_id=1, sensor2_slave_id=5))
        self.path.write_text(json.dumps(self.original))
        self.settings = Settings(self.path)
        self.state = dict(s1=25, s2=75, timestamps=[time.time(), time.time()],
                          s1_connected=True, s2_connected=True)
        owner = self
        class FakeMonitor:
            def state(self): return owner.state
        self.client = create_app(self.settings, FakeMonitor()).test_client()

    def tearDown(self): self.temp.cleanup()

    def test_interpolation_endpoints_and_nearest_offset_outside(self):
        sensor = dict(offset=999, calibration=dict(method='piecewise_linear', points=POINTS))
        for raw, expected in [(0, 2), (25, 28.5), (50, 55), (75, 78), (100, 101),
                              (-10, -8), (120, 121)]:
            self.assertAlmostEqual(apply_calibration(raw, sensor), expected)
        self.assertIsNone(apply_calibration(None, sensor))
        self.assertEqual(apply_calibration(25, {'offset': 1.5}), 26.5)

    def test_table_sorts_and_persists_independently_without_live_sensor(self):
        self.state = dict(s1=None, s2=None)
        r = self.client.post('/api/calibration', json=dict(sensor=2, points=list(reversed(POINTS))))
        self.assertEqual(r.status_code, 200)
        cfg = Settings(self.path).get()
        self.assertEqual(cfg['sensor2']['calibration']['points'], POINTS)
        self.assertEqual(cfg['sensor2']['offset'], 0)
        self.assertEqual(cfg['sensor1'], self.original['sensor1'])
        self.assertEqual(cfg['modbus'], self.original['modbus'])
        self.settings.update({'sensor2': dict(low_alarm=0, high_alarm=80)})
        self.assertEqual(self.settings.get()['sensor2']['calibration']['points'], POINTS)

    def test_invalid_tables_atomic(self):
        invalid = [[], POINTS[:2], POINTS*7, None,
                   [POINTS[0], POINTS[0], POINTS[2]],
                   [POINTS[0], dict(raw_c=50, reference_c=1), POINTS[2]],
                   [POINTS[0], dict(raw_c=True, reference_c=55), POINTS[2]],
                   [POINTS[0], dict(raw_c=None, reference_c=55), POINTS[2]],
                   [POINTS[0], dict(raw_c='NaN', reference_c=55), POINTS[2]],
                   [POINTS[0], dict(raw_c=50, reference_c='inf'), POINTS[2]],
                   [POINTS[0], dict(raw_c=50, reference_c=55, extra=1), POINTS[2]]]
        for points in invalid:
            with self.subTest(points=points):
                self.assertEqual(self.client.post('/api/calibration', json=dict(sensor=1, points=points)).status_code, 400)
                self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_reset_and_one_point_replace_table(self):
        self.settings.calibrate(dict(sensor=1, points=POINTS), self.state)
        self.settings.calibrate(dict(sensor=1, reference_c=27), self.state)
        cfg = self.settings.get()['sensor1']
        self.assertNotIn('points', cfg['calibration'])
        self.assertEqual(apply_calibration(25, cfg), 27)
        self.settings.calibrate(dict(sensor=1, points=POINTS), self.state)
        self.settings.calibrate(dict(sensor=1, reset=True), {})
        self.assertEqual(apply_calibration(25, self.settings.get()['sensor1']), 25)

    def test_archive_corrected_but_live_history_stays_raw(self):
        self.settings.calibrate(dict(sensor=1, points=POINTS), self.state)
        monitor = Monitor.__new__(Monitor)
        monitor.last = [0, 0]; monitor.archived = [0, 0]
        import threading
        monitor.lock = threading.Lock(); monitor.history = [deque(), deque()]
        captured = []
        class Store:
            def append(self, rows): captured.extend(rows)
        monitor.store = Store()
        Monitor.collect_once(monitor, self.state, self.settings.get())
        self.assertAlmostEqual(captured[0][3], 28.5)
        self.assertEqual(monitor.history[0][0][1], 25)

    def test_bridge_uses_calibrated_fields_and_rejects_nonfinite(self):
        path = Path(__file__).resolve().parents[1]/'bridge_review.py'
        if not path.exists(): path = Path(__file__).resolve().parents[1]/'pt100-api-bridge/bridge.py'
        if not path.exists(): path = Path('/home/admin/pt100-api-bridge/bridge.py')
        self.assertTrue(path.exists())
        spec = importlib.util.spec_from_file_location('bridge_for_test', path)
        bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
        state = copy.deepcopy(self.state)
        state.update(config=self.settings.get(), calibrated_s1=28.5, calibrated_s2=78)
        sample = bridge.make_sample(state, {'solenoid_on': False}, 1, {'hpa': 965})
        self.assertEqual(sample['daq']['ch1_c'], 28.5)
        self.assertEqual(sample['env']['room']['t'], 78)
        state['calibrated_s1'] = float('nan')
        with self.assertRaises(ValueError): bridge.make_sample(state, {'solenoid_on': False}, 1, {'hpa': 965})

    def test_state_exposes_corrected_values_without_changing_raw(self):
        self.settings.calibrate(dict(sensor=1, points=POINTS), self.state)
        monitor = Monitor.__new__(Monitor)
        owner = self
        class Reader:
            def snapshot(self): return copy.deepcopy(owner.state)
        import threading
        monitor.reader = Reader(); monitor.settings = self.settings
        monitor.lock = threading.Lock(); monitor.history = [[], []]
        state = monitor.state()
        self.assertEqual(state['s1'], 25)
        self.assertEqual(state['calibrated_s1'], 28.5)
        self.assertEqual(state['calibrated_s2'], 73)


if __name__ == '__main__': unittest.main()
