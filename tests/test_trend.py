import tempfile
import unittest
from pathlib import Path
from thermovue_history import HistoryStore
from thermovue_trend import chart_data, reduce_points


class Monitor:
    def state(self):
        return {'history': [[[150, 31], [200, 32]], [[160, 28]]],
                'config': {'sensor1': {'offset': .5}, 'sensor2': {'offset': -.5},
                           'logging': {'interval_minutes': 10}}}


class TrendTests(unittest.TestCase):
    def test_merges_archived_and_live_with_correct_calibration(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder)/'readings.sqlite3')
            store.append([(100, 1, 1, 30.5, 10, 75, 'Normal'),
                          (150, 1, 1, 31.5, 10, 75, 'Normal'),
                          (120, 2, 5, 27.5, 10, 75, 'Normal')])
            result = chart_data(store, Monitor(), {'start': '100', 'end': '180'})
            self.assertEqual(result['counts'], [2, 2])
            self.assertEqual(result['series'][0], [[100, 30.5, 'archive'], [150, 31.5, 'live']])
            self.assertEqual(result['series'][1][-1][1], 27.5)
            self.assertEqual(result['archive_interval_minutes'], 10)

    def test_rejects_invalid_or_excessive_ranges(self):
        for args in ({}, {'start': 'nan', 'end': '2'}, {'start': '2', 'end': '1'},
                     {'start': '0', 'end': str(32*86400)},
                     {'start': '1', 'end': '2', 'max_points': '99999'}):
            with self.assertRaises(ValueError):
                chart_data(None, None, args)

    def test_downsampling_preserves_spikes_order_and_endpoints(self):
        points = [[i, 1000 if i == 4321 else -1000 if i == 6789 else 30, 'archive']
                  for i in range(10000)]
        result = reduce_points(points, 1800)
        self.assertLessEqual(len(result), 1800)
        self.assertEqual(result[0], points[0])
        self.assertEqual(result[-1], points[-1])
        self.assertEqual([p[0] for p in result], sorted(set(p[0] for p in result)))
        self.assertIn(points[4321], result)
        self.assertIn(points[6789], result)


if __name__ == '__main__':
    unittest.main()
