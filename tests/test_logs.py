import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from thermovue_history import HistoryStore
from thermovue_logs import build_log_zip, csv_tail, network_status


class LogsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = HistoryStore(self.directory/'readings.sqlite3')
        self.store.append([(100, 1, 1, 30.1, 10, 75, 'Normal')])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        with (self.directory/name).open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_current_network_and_api_join_with_csv_quoting(self):
        stamp = '2026-11-18T08:32:00+07:00'
        self.write('network_20261118.csv', [{'network_checked_at_wib': stamp, 'ssid': 'Test, WiFi', 'rssi_dbm': '-55'}])
        self.write('api_network_20261118.csv', [{'network_checked_at_wib': stamp, 'sent_at_wib': stamp,
                    'http_status': '200', 'response_time_ms': '100', 'error': ''}])
        result = network_status(self.directory)
        self.assertEqual(result['network']['ssid'], 'Test, WiFi')
        self.assertEqual(result['rows'][0]['response_time_ms'], '100')
        with (self.directory/'network_20261118.csv').open('a') as file:
            file.write('incomplete,')
        self.assertEqual(len(csv_tail(self.directory/'network_20261118.csv')), 1)

    def test_zip_includes_every_date_and_excludes_credentials(self):
        for name in ('network_20261117.csv', 'network_20261118.csv', 'api_network_20261118.csv', 'api_20261117.csv'):
            (self.directory/name).write_text('header\nvalue\n')
        (self.directory/'config.json').write_text('secret')
        output = build_log_zip(self.store, 'all', self.directory)
        with output, zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 6)
            self.assertFalse(any('config' in name or 'sqlite' in name for name in names))
            rows = list(csv.DictReader(io.StringIO(archive.read('sensor/thermovue_sensor_semua.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['temperature_c'], '30.1')
        output = build_log_zip(self.store, 'network', self.directory)
        with output, zipfile.ZipFile(output) as archive:
            self.assertNotIn('sensor/thermovue_sensor_semua.csv', archive.namelist())
            self.assertIn('api_dan_sensor/api_network_20261118.csv', archive.namelist())
        with self.assertRaises(ValueError):
            build_log_zip(self.store, '../config', self.directory)

    def test_full_sensor_export_has_no_50000_row_limit(self):
        self.store.append([(200+i, 1, 1, 30, 10, 75, 'Normal') for i in range(50001)])
        output = build_log_zip(self.store, 'sensor', self.directory)
        with output, zipfile.ZipFile(output) as archive:
            rows = list(csv.reader(io.StringIO(archive.read('sensor/thermovue_sensor_semua.csv').decode('utf-8-sig'))))
            self.assertEqual(len(rows), 50003)  # Header + initial row + 50,001 rows.

    def test_no_logs_returns_explicit_empty_state(self):
        result = network_status(self.directory)
        self.assertIsNone(result['network'])
        self.assertIsNone(result['api'])
        self.assertEqual(result['rows'], [])


if __name__ == '__main__':
    unittest.main()
