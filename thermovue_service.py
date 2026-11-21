"""Local ThermoVue HTTP service. Sole owner of the USB/Modbus connection."""
import copy
import json
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, jsonify, request, Response, send_file
from werkzeug.serving import make_server
from thermovue_history import HistoryStore, filter_args, make_xls, make_pdf, period_text, WIB
from thermovue_trend import chart_data
from thermovue_logs import network_status, build_log_zip
from thermovue_calibration import apply_calibration, validate_points

BASE = Path(__file__).resolve().parent
CONFIG = BASE / 'jambu/jambu/config.json'
sys.path.insert(0, str(CONFIG.parent))
from modbus_reader import ModbusTemperatureReader

class Settings:
    def __init__(self, path=CONFIG):
        self.path = path
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text())

    def get(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def calibrate(self, payload, state):
        if not isinstance(payload, dict) or type(payload.get('sensor')) is not int or payload['sensor'] not in (1, 2):
            raise ValueError('Pilih sensor suhu 1 atau 2.')
        reset = set(payload) == {'sensor', 'reset'} and payload['reset'] is True
        if set(payload) == {'sensor', 'points'}:
            points = validate_points(payload['points'])
            with self.lock:
                result = copy.deepcopy(self.data)
                sensor = result[f'sensor{payload["sensor"]}']
                sensor['offset'] = 0.0
                sensor['calibration'] = dict(method='piecewise_linear', points=points,
                                             outside_range='nearest_offset',
                                             calibrated_at=time.time(), sensor_type='K')
                self._commit(result)
                return self.get()
        if not reset and set(payload) != {'sensor', 'reference_c'}:
            raise ValueError('Isi tabel kalibrasi, suhu referensi, atau pilih reset.')
        index = payload['sensor']
        reference = raw = None
        if not reset:
            try:
                if isinstance(payload['reference_c'], bool):
                    raise ValueError()
                reference = float(payload['reference_c'])
                if not math.isfinite(reference):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('Suhu referensi harus berupa angka yang valid.')
            raw = state.get(f's{index}')
            timestamp = state.get('timestamps', [0, 0])[index-1]
            if (not state.get(f's{index}_connected') or type(raw) not in (int, float)
                    or not math.isfinite(raw) or not timestamp or not 0 <= time.time()-timestamp <= 4):
                raise ValueError('Sensor tidak terhubung atau data lebih lama dari 4 detik. Coba kembali.')
        offset = 0.0 if reset else reference - raw
        if not math.isfinite(offset):
            raise ValueError('Offset kalibrasi tidak valid.')
        with self.lock:
            result = copy.deepcopy(self.data)
            sensor = result[f'sensor{index}']
            sensor['offset'] = offset
            sensor['calibration'] = dict(method='reset' if reset else 'one_point_offset',
                                         raw_c=raw, reference_c=reference, offset_c=offset,
                                         calibrated_at=time.time(), sensor_type='K')
            self._commit(result)
            return self.get()

    def _commit(self, result):
        temporary = self.path.with_suffix('.json.tmp')
        try:
            with temporary.open('w') as file:
                json.dump(result, file, indent=4, allow_nan=False)
                file.write('\n')
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.data = result

    def update(self, payload):
        if not isinstance(payload, dict) or not payload or set(payload) - {'sensor1', 'sensor2', 'logging'}:
            raise ValueError('Pengaturan harus berisi sensor1, sensor2, atau logging.')
        with self.lock:
            result = copy.deepcopy(self.data)
            for key, values in payload.items():
                if key == 'logging':
                    if not isinstance(values,dict) or set(values) != {'interval_minutes'}:
                        raise ValueError('Isi interval penyimpanan dalam menit.')
                    value = values['interval_minutes']
                    try:
                        number = float(value)
                    except (TypeError,ValueError):
                        raise ValueError('Interval harus bilangan bulat 1–1440 menit.')
                    if isinstance(value,bool) or not math.isfinite(number) or not number.is_integer() or not 1 <= number <= 1440:
                        raise ValueError('Interval harus bilangan bulat 1–1440 menit.')
                    result['logging'] = {'interval_minutes':int(number)}
                    continue
                if not isinstance(values, dict) or set(values) != {'low_alarm', 'high_alarm'}:
                    raise ValueError('Isi batas bawah dan batas atas untuk setiap sensor.')
                for field, value in values.items():
                    if isinstance(value, bool):
                        raise ValueError('Batas suhu harus berupa angka.')
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        raise ValueError('Batas suhu harus berupa angka.')
                    if not math.isfinite(number):
                        raise ValueError('Batas suhu harus berupa angka yang valid.')
                    result[key][field] = number
                if result[key]['low_alarm'] >= result[key]['high_alarm']:
                    raise ValueError('Batas bawah harus lebih kecil dari batas atas.')
            # Commit both sensors together; retain all USB and calibration settings.
            self._commit(result)
            return self.get()

class Monitor:
    def __init__(self, settings):
        self.settings = settings
        self.store = HistoryStore()
        self.lock = threading.Lock()
        self.history = [deque(maxlen=3600), deque(maxlen=3600)]
        self.last = [0, 0]
        self.archived = self.store.latest_times()
        self.stop = threading.Event()
        cfg = settings.get()['modbus'].copy()
        cfg.pop('enabled', None)
        self.reader = ModbusTemperatureReader(**cfg)
        self.reader.connect()
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def collect_once(self, state, cfg):
        interval = cfg.get('logging',{}).get('interval_minutes',1) * 60
        pending, rows, saved = [], [], []
        for i in range(2):
            value, timestamp = state[f's{i+1}'], state['timestamps'][i]
            if value is None or timestamp == self.last[i]:
                continue
            pending.append((i,timestamp,value))
            # Schedule independently per sensor, relative to its last durable sample.
            if self.archived[i] and timestamp < self.archived[i] + interval:
                continue
            sensor = cfg[f'sensor{i+1}']
            temperature = apply_calibration(value, sensor)
            low, high = sensor['low_alarm'], sensor['high_alarm']
            status = 'Alarm rendah' if temperature <= low else 'Alarm tinggi' if temperature >= high else 'Normal'
            device = cfg['modbus']['slave_id' if i == 0 else 'sensor2_slave_id']
            rows.append((timestamp,i+1,device,temperature,low,high,status))
            saved.append((i,timestamp))
        self.store.append(rows)
        with self.lock:
            for i,timestamp,value in pending:
                self.last[i] = timestamp
                self.history[i].append([timestamp,value])
            for i,timestamp in saved:
                self.archived[i] = timestamp

    def collect(self):
        while not self.stop.wait(.3):
            try:
                self.collect_once(self.reader.snapshot(), self.settings.get())
            except Exception:
                import logging
                logging.exception('Penyimpanan riwayat gagal; mencoba lagi')

    def state(self):
        state = self.reader.snapshot()
        state['config'] = self.settings.get()
        for i in (1, 2):
            state[f'calibrated_s{i}'] = apply_calibration(state[f's{i}'], state['config'][f'sensor{i}'])
        state['server_time'] = time.time()
        with self.lock:
            state['history'] = [list(h) for h in self.history]
        return state

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.reader.disconnect()

def create_app(settings, monitor, store=None):
    store = store or getattr(monitor, "store", None)
    app = Flask(__name__, static_folder=str(BASE / 'thermovue-web/static'), static_url_path='/static')
    app.config['MAX_CONTENT_LENGTH'] = 8192

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store' if request.path.startswith('/api/') else 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.get('/api/state')
    def state():
        return jsonify(monitor.state())

    @app.get('/api/trend')
    def trend():
        try:
            return jsonify(chart_data(store, monitor, request.args))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/network')
    def network():
        return jsonify(network_status())

    @app.get('/api/logs/download')
    def download_logs():
        try:
            kind = request.args.get('kind', 'all')
            output = build_log_zip(store, kind)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        from datetime import datetime
        name = 'thermovue_log_'+kind+'_'+datetime.now(WIB).strftime('%Y%m%d_%H%M%S')+'.zip'
        response = send_file(output, mimetype='application/zip', as_attachment=True,
                             download_name=name, max_age=0)
        response.call_on_close(output.close)
        return response

    @app.route('/api/config', methods=['GET', 'POST'])
    def config():
        if request.method == 'GET':
            return jsonify(settings.get())
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(error='Permintaan harus berasal dari website ThermoVue.'), 403
        if not request.is_json:
            return jsonify(error='Gunakan data JSON.'), 415
        try:
            result = settings.update(request.get_json())
            return jsonify(config=result, message='Pengaturan berhasil disimpan.')
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            app.logger.exception('Settings write failed')
            return jsonify(error='Pengaturan gagal disimpan. Periksa penyimpanan Pi.'), 500

    @app.post('/api/calibration')
    def calibration():
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(error='Permintaan harus berasal dari website ThermoVue.'), 403
        if not request.is_json:
            return jsonify(error='Gunakan data JSON.'), 415
        try:
            result = settings.calibrate(request.get_json(), monitor.state())
            return jsonify(config=result, message='Kalibrasi tersimpan. Berlaku untuk data berikutnya.')
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except OSError:
            app.logger.exception('Calibration write failed')
            return jsonify(error='Kalibrasi gagal disimpan. Periksa penyimpanan Pi.'), 500

    @app.get('/api/readings')
    def readings():
        try:
            filters = filter_args(request.args)
            page = int(request.args.get('page','1'))
            size = int(request.args.get('page_size','10'))
            if page < 1 or size not in (10,25,50,100):
                raise ValueError('Halaman atau jumlah baris tidak valid.')
            return jsonify(store.query(**filters,page=page,page_size=size))
        except (ValueError,TypeError) as exc:
            return jsonify(error=str(exc) or 'Filter tidak valid.'),400

    @app.get('/api/export.<format>')
    def export(format):
        if format not in ('xls','pdf'):
            return jsonify(error='Pilih format XLS atau PDF.'),404
        try:
            filters = filter_args(request.args)
            result = store.query(**filters,export=True)
            period = period_text(filters['start'],filters['end'])
            data = make_xls(result['rows'],period) if format == 'xls' else make_pdf(result['rows'],period)
            from datetime import datetime
            name = 'thermovue_'+datetime.now(WIB).strftime('%Y%m%d_%H%M%S')+'.'+format
            return Response(data,mimetype='application/vnd.ms-excel' if format == 'xls' else 'application/pdf',
                            headers={'Content-Disposition': f'attachment; filename={name}'})
        except (ValueError,TypeError) as exc:
            return jsonify(error=str(exc)),400
    return app

def main():
    settings = Settings()
    monitor = Monitor(settings)
    server = make_server('0.0.0.0', 5000, create_app(settings, monitor), threaded=True)
    def shutdown(*args):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        monitor.close()
        server.server_close()

if __name__ == '__main__':
    main()
