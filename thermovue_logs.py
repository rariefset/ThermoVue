"""Read existing bridge diagnostics and export complete sensor/network logs."""
import csv
from datetime import datetime
import io
from pathlib import Path
import tempfile
import zipfile

DATA = Path('/home/admin/pt100-api-bridge/data')


def csv_tail(path, limit=100):
    """Bounded read of a growing CSV. Ignore a partially written final record."""
    with path.open('rb') as file:
        header = file.readline().decode('utf-8-sig')
        start = file.tell()
        file.seek(0, 2)
        size = file.tell()
        offset = max(start, size-256*1024)
        file.seek(offset)
        if offset > start:
            file.readline()
        raw = file.read(size-file.tell()).decode('utf-8', errors='replace')
    names = next(csv.reader([header]))
    rows = []
    for values in csv.reader(io.StringIO(raw, newline='')):
        if len(values) == len(names):
            row = dict(zip(names, values))
            timestamp = row.get('network_checked_at_wib') or row.get('sent_at_wib')
            try:
                datetime.fromisoformat(timestamp)
            except (TypeError, ValueError):
                continue
            rows.append(row)
    return rows[-limit:]


def recent_rows(directory, prefix, limit=100):
    rows = []
    for path in sorted(directory.glob(prefix+'????????.csv'), reverse=True):
        rows = csv_tail(path, limit-len(rows)) + rows
        if len(rows) >= limit:
            break
    return rows[-limit:]


def network_status(directory=DATA):
    network = recent_rows(directory, 'network_', 50)
    api = recent_rows(directory, 'api_network_', 250)
    # An API row contains the network snapshot used when that sample was taken.
    by_stamp = {row.get('network_checked_at_wib'): row for row in api}
    rows = []
    for row in network:
        item = dict(row)
        request = by_stamp.get(row['network_checked_at_wib'], {})
        for name in ('sent_at_wib', 'http_status', 'response_time_ms', 'error'):
            item[name] = request.get(name, '')
        rows.append(item)
    return {'network': network[-1] if network else None,
            'api': api[-1] if api else None, 'rows': list(reversed(rows)),
            'server_time': datetime.now().timestamp(), 'timezone': 'Asia/Jakarta'}


def sensor_csv(store, stream):
    """Export the entire durable archive, without the paginated-view row limit."""
    writer = csv.writer(stream)
    writer.writerow(['timestamp_wib', 'sensor', 'modbus_id', 'temperature_c',
                     'low_alarm_c', 'high_alarm_c', 'status'])
    from zoneinfo import ZoneInfo
    with store.connection() as db:
        for row in db.execute('SELECT * FROM readings ORDER BY epoch,id'):
            writer.writerow([datetime.fromtimestamp(row['epoch'], ZoneInfo('Asia/Jakarta')).isoformat(),
                             row['sensor'], row['device_id'], row['temperature'],
                             row['low_alarm'], row['high_alarm'], row['status']])


def build_log_zip(store, kind='all', directory=DATA):
    if kind not in ('all', 'sensor', 'network'):
        raise ValueError('Pilih all, sensor, atau network.')
    output = tempfile.SpooledTemporaryFile(max_size=8*1024*1024, mode='w+b')
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            if kind in ('all', 'sensor'):
                with archive.open('sensor/thermovue_sensor_semua.csv', 'w') as binary:
                    with io.TextIOWrapper(binary, encoding='utf-8-sig', newline='') as text:
                        sensor_csv(store, text)
            for path in sorted(directory.glob('*.csv')):
                sensor_log = path.name.startswith('api_')
                network_log = path.name.startswith(('network_', 'api_network_'))
                if not (kind == 'all' and (sensor_log or network_log)
                        or kind == 'sensor' and sensor_log
                        or kind == 'network' and network_log):
                    continue
                category = 'jaringan' if path.name.startswith('network_') else 'api_dan_sensor'
                # Fixed byte-length snapshot: appending can continue during download.
                length = path.stat().st_size
                with path.open('rb') as source, archive.open(category+'/'+path.name, 'w') as target:
                    while length > 0:
                        chunk = source.read(min(1024*1024, length))
                        if not chunk:
                            break
                        target.write(chunk)
                        length -= len(chunk)
            archive.writestr('BACA_SAYA.txt',
                'ThermoVue - salinan seluruh log tersimpan saat unduhan dibuat.\n'
                'Waktu dalam WIB. Arsip sensor mengikuti interval pencatatan ThermoVue.\n'
                'Log API mencatat percobaan kirim, sehingga seq dapat berulang saat retry.\n'
                'network: SSID, RSSI, IP, gateway, ping, kesehatan Raspberry Pi.\n'
                'api_network: suhu, estimasi tekanan, solenoid, HTTP, response_time_ms dan jaringan.\n'
                'Tekanan adalah estimasi model cuaca, bukan sensor tekanan fisik.\n'
                'Response time mencakup jaringan/TLS sampai respons POST selesai diterima.\n'
                'Ping loss 0/100 adalah hasil satu probe, bukan PDR aplikasi.\n'
                'File API/jaringan yang masih bertambah disalin sampai ukuran saat pembacaan dimulai.\n')
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
