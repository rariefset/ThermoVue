"""Read ThermoVue HTTP snapshots and forward PT100 telemetry; never open RS485."""
import argparse
import csv
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

STOP = threading.Event()
LOG = logging.getLogger('pt100-api')
NETWORK_FIELDS = ['network_checked_at_wib', 'interface', 'ssid', 'rssi_dbm',
                  'wifi_quality_pct', 'ip_address', 'gateway', 'ping_gateway_ms',
                  'ping_gateway_status', 'ping_gateway_loss_pct', 'uptime_s',
                  'cpu_temperature_c', 'memory_available_mb', 'network_error']


def append_csv(path, fields, row):
    new = not path.exists()
    with path.open('a', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        if new:
            writer.writeheader()
        writer.writerow(row)


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=4,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise OSError(result.stderr.strip() or f'{args[0]} exit={result.returncode}')
    return result.stdout.strip()


def network_snapshot():
    data = {key: None for key in NETWORK_FIELDS}
    data['network_checked_at_wib'] = datetime.now(ZoneInfo('Asia/Jakarta')).isoformat()
    errors = []
    try:
        routes = json.loads(command(['ip', '-j', 'route', 'show', 'default']))
        route = min(routes, key=lambda row: row.get('metric', 0)) if routes else {}
        interface = route.get('dev')
        data.update(interface=interface, gateway=route.get('gateway'))
        if interface:
            addresses = json.loads(command(['ip', '-j', '-4', 'address', 'show', 'dev', interface]))
            data['ip_address'] = next((a['local'] for r in addresses for a in r['addr_info']
                                       if a.get('scope') == 'global'), None)
            if (Path('/sys/class/net')/interface/'wireless').exists():
                uuid = command(['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', interface])
                data['ssid'] = command(['nmcli', '-g', '802-11-wireless.ssid',
                                        'connection', 'show', 'uuid', uuid])
                for line in Path('/proc/net/wireless').read_text().splitlines():
                    if line.split(':')[0].strip() == interface:
                        parts = line.split(':', 1)[1].split()
                        rssi = float(parts[2].rstrip('.'))
                        data['rssi_dbm'] = rssi if -127 <= rssi < 0 else None
                        data['wifi_quality_pct'] = round(min(100, max(0, float(parts[1].rstrip('.'))/70*100)), 1)
    except Exception as exc:
        errors.append(str(exc))
    if data['gateway']:
        try:
            output = command(['ping', '-n', '-c', '1', '-W', '1', data['gateway']])
            match = re.search(r'time([=<])\s*([\d.]+)\s*ms', output)
            data.update(ping_gateway_ms=float(match[2]) if match else None,
                        ping_gateway_status='ok', ping_gateway_loss_pct=0)
        except Exception as exc:
            data.update(ping_gateway_status='failed', ping_gateway_loss_pct=100)
            errors.append('gateway ping: '+str(exc))
    else:
        data['ping_gateway_status'] = 'no_gateway'
    try:
        data['uptime_s'] = float(Path('/proc/uptime').read_text().split()[0])
        data['cpu_temperature_c'] = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000
        available = re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)
        data['memory_available_mb'] = round(int(available[1])/1024, 1) if available else None
    except Exception as exc:
        errors.append(str(exc))
    data['network_error'] = ' | '.join(errors)
    return data


class NetworkMonitor:
    def __init__(self, directory, interval):
        self.directory, self.interval = directory, interval
        self.lock = threading.Lock()
        self.latest = {}

    def snapshot(self):
        with self.lock:
            return dict(self.latest)

    def run(self):
        while not STOP.is_set():
            data = network_snapshot()
            with self.lock:
                self.latest = data
            date = datetime.now(ZoneInfo('Asia/Jakarta')).strftime('%Y%m%d')
            append_csv(self.directory/f'network_{date}.csv', NETWORK_FIELDS, data)
            STOP.wait(self.interval)


def get_json(url):
    with urlopen(url, timeout=8) as response:
        return json.load(response)


def connect_db(path):
    db = sqlite3.connect(path, timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    return db


def make_sample(state, config, seq, pressure):
    temperatures = []
    for i in range(2):
        value = state.get(f's{i+1}')
        timestamp = state['timestamps'][i]
        if (not state.get(f's{i+1}_connected') or type(value) not in (int, float)
                or not math.isfinite(value) or not 0 <= time.time()-timestamp <= 4):
            raise ValueError(f'PT100 {i+1} tidak valid atau data lebih lama dari 4 detik')
        corrected = state.get(f'calibrated_s{i+1}', value + state['config'][f'sensor{i+1}'].get('offset', 0))
        if type(corrected) not in (int, float) or not math.isfinite(corrected):
            raise ValueError(f'Suhu terkoreksi {i+1} tidak valid')
        temperatures.append(corrected)
    # Use the service's correction model, including multipoint calibration.
    return {'seq': seq, 'ts': round(min(state['timestamps'])*1000), 'ts_ok': True,
            'daq': {'ch1_c': temperatures[0], 'ch2_ma': 20.0},
            'env': {'room': {'t': temperatures[1]}},
            'baro_hpa': pressure['hpa'], 'io': {'sol1': config['solenoid_on']}}


def sampler(config, path, boot, network):
    db = connect_db(path)
    seq, pressure, last_pressure = 0, None, float('-inf')
    weather = ('https://api.open-meteo.com/v1/forecast?latitude=-7.98&longitude=112.63'
               '&current=surface_pressure&timezone=Asia%2FJakarta')
    due = time.monotonic()
    while not STOP.is_set():
        try:
            if time.monotonic()-last_pressure >= 900 or pressure is None:
                # Attempt no more often than once a minute when weather is unavailable.
                if time.monotonic()-last_pressure >= 60:
                    last_pressure = time.monotonic()
                    try:
                        data = get_json(weather)
                        value = data['current']['surface_pressure']
                        if not 300 <= value <= 1100:
                            raise ValueError('Tekanan cuaca di luar rentang')
                        pressure = {'hpa': value, 'weather_time': data['current']['time'],
                                    'url': weather, 'fetched_at': time.time(),
                                    'estimated': True}
                    except Exception:
                        LOG.exception('Pembaruan estimasi tekanan gagal')
            if pressure is None or time.time()-pressure['fetched_at'] > 7200:
                raise ValueError('Estimasi tekanan belum tersedia atau kedaluwarsa')
            state = get_json(config['thermovue_url']+'/api/state')
            sample = make_sample(state, config, seq, pressure)
            metadata = network.snapshot()
            metadata['sampled_at_wib'] = datetime.now(ZoneInfo('Asia/Jakarta')).isoformat()
            if type(metadata.get('rssi_dbm')) in (float, int):
                sample['rssi'] = round(metadata['rssi_dbm'])
            with db:
                db.execute('INSERT INTO outbox(boot,seq,sample,pressure,network) VALUES(?,?,?,?,?)',
                           (boot, seq, json.dumps(sample), json.dumps(pressure), json.dumps(metadata)))
            seq += 1
        except Exception as exc:
            LOG.warning('Sampel dilewati: %s', exc)
        due += config['interval_s']
        if due < time.monotonic():
            due = time.monotonic()+config['interval_s']
        STOP.wait(max(0, due-time.monotonic()))
    db.close()


def sender(config, path, directory):
    db = connect_db(path)
    backoff = 2
    fields = ['sent_at_wib', 'boot', 'seq', 'temperature1_c', 'temperature2_c',
              'pressure_kpa_estimated', 'solenoid_on', 'http_status',
              'response_time_ms', 'accepted', 'duplicates', 'rejected', 'error',
              'sampled_at_wib'] + NETWORK_FIELDS
    while not STOP.is_set():
        rows = db.execute('SELECT boot,seq,sample,network FROM outbox WHERE status="pending" '
                          'AND boot=(SELECT min(boot) FROM outbox WHERE status="pending") '
                          'ORDER BY seq LIMIT 300').fetchall()
        if not rows:
            STOP.wait(.2)
            continue
        samples = [json.loads(row[2]) for row in rows]
        payload = {'v': 1, 'node': config['node_id'], 'boot': rows[0][0],
                   'fw': 'raspi-pt100-bridge-1', 'buf': 0, 'samples': samples}
        body = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
        status, elapsed, result, error, acknowledged = 0, None, {}, '', False
        try:
            # Timestamp the signature from server time; retain original sensor sample timestamps.
            clock = get_json(config['base_url']+'/ingest/time')
            timestamp = str(clock['server_time'])
            signature = hmac.new(config['device_secret'].encode(),
                                 timestamp.encode()+b'\n'+body, hashlib.sha256).hexdigest()
            request = Request(config['base_url']+'/ingest/telemetry', body,
                              {'Content-Type': 'application/json',
                               'X-Device-Id': config['node_id'], 'X-Timestamp': timestamp,
                               'X-Signature': signature}, method='POST')
            started = time.perf_counter()
            try:
                with urlopen(request, timeout=10) as response:
                    status = response.status
                    result = json.load(response)
            finally:
                elapsed = round((time.perf_counter()-started)*1000, 2)
            ack = result.get('ack', {})
            last = ack.get('last_seq')
            if (status == 200 and result.get('ok') is True and ack.get('boot') == rows[0][0]
                    and type(last) is int and rows[0][1] <= last <= rows[-1][1]):
                rejected = {item['index'] for item in result.get('rejected', [])}
                with db:
                    for index, row in enumerate(rows):
                        if row[1] <= last:
                            db.execute('UPDATE outbox SET status=?,response=? WHERE boot=? AND seq=?',
                                       ('rejected' if index in rejected else 'acked',
                                        json.dumps(result), row[0], row[1]))
                acknowledged = True
            else:
                raise ValueError('Respons tidak memiliki ACK yang sesuai')
        except Exception as exc:
            status = getattr(exc, 'code', status)
            error = str(exc)
            LOG.warning('Kirim gagal: %s', error)
        now = datetime.now(ZoneInfo('Asia/Jakarta'))
        csv_path = directory/('api_network_'+now.strftime('%Y%m%d')+'.csv')
        for row, sample in zip(rows, samples):
                log_row = dict(sent_at_wib=now.isoformat(), boot=row[0], seq=row[1],
                                     temperature1_c=sample['daq']['ch1_c'],
                                     temperature2_c=sample['env']['room']['t'],
                                     pressure_kpa_estimated=round(sample['baro_hpa']/10, 3),
                                     solenoid_on=int(sample['io']['sol1']), http_status=status,
                                     response_time_ms=elapsed, accepted=result.get('accepted'),
                                     duplicates=result.get('duplicates'),
                                     rejected=json.dumps(result.get('rejected', [])), error=error)
                log_row.update(json.loads(row[3] or '{}'))
                append_csv(csv_path, fields, log_row)
        if acknowledged:
            LOG.info('HTTP %s accepted=%s boot=%s seq=%s..%s API=%s ms',
                     status, result.get('accepted'), rows[0][0], rows[0][1], rows[-1][1], elapsed)
            backoff = 2
            STOP.wait(.2)
        else:
            STOP.wait(backoff)
            backoff = min(60, backoff*2)
    db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = json.loads(args.config.read_text())
    if type(config['interval_s']) is not int or not 1 <= config['interval_s'] <= 3600:
        raise ValueError('interval_s harus 1–3600 detik')
    if type(config['solenoid_on']) is not bool:
        raise ValueError('solenoid_on harus boolean')
    if config['base_url'] != 'https://dev-ai.ub.ac.id/vacumfr/api/v1':
        raise ValueError('Tujuan API tidak sesuai')
    directory = args.config.parent/'data'
    directory.mkdir(exist_ok=True)
    lock = (directory/'bridge.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = directory/'outbox.sqlite3'
    db = connect_db(path)
    db.execute('CREATE TABLE IF NOT EXISTS outbox(boot INTEGER,seq INTEGER,sample TEXT,'
               'pressure TEXT,status TEXT DEFAULT "pending",response TEXT,PRIMARY KEY(boot,seq))')
    db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value INTEGER)')
    if 'network' not in {row[1] for row in db.execute('PRAGMA table_info(outbox)')}:
        db.execute('ALTER TABLE outbox ADD COLUMN network TEXT')
    db.commit()
    with db:
        previous = db.execute('SELECT value FROM settings WHERE key="boot"').fetchone()
        boot = max(int(time.time()), previous[0]+1 if previous else 0)
        db.execute('INSERT OR REPLACE INTO settings VALUES("boot",?)', (boot,))
    db.close()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    network_interval = config.get('network_interval_s', 5)
    if type(network_interval) is not int or not 1 <= network_interval <= 3600:
        raise ValueError('network_interval_s harus 1–3600 detik')
    network = NetworkMonitor(directory, network_interval)
    workers = [threading.Thread(target=network.run),
               threading.Thread(target=sampler, args=(config, path, boot, network)),
               threading.Thread(target=sender, args=(config, path, directory))]
    for worker in workers:
        worker.start()
    while not STOP.wait(1):
        if any(not worker.is_alive() for worker in workers):
            STOP.set()
            raise RuntimeError('Worker berhenti; service perlu restart')
    for worker in workers:
        worker.join()
    lock.close()


if __name__ == '__main__':
    main()
