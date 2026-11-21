"""Bounded chart queries combining persisted readings and recent live samples."""
import math
import time
from thermovue_calibration import apply_calibration


def reduce_points(points, limit):
    """Keep endpoints and time-ordered min/max per bucket to preserve spikes."""
    if len(points) <= limit:
        return points
    buckets = max(1, (limit-2)//2)
    inner = points[1:-1]
    result = [points[0]]
    for i in range(buckets):
        part = inner[i*len(inner)//buckets:(i+1)*len(inner)//buckets]
        if part:
            extrema = {p[0]: p for p in (min(part, key=lambda p:p[1]),
                                       max(part, key=lambda p:p[1]))}
            result.extend(extrema[key] for key in sorted(extrema))
    result.append(points[-1])
    return result


def chart_data(store, monitor, args):
    try:
        start, end = float(args['start']), float(args['end'])
        limit = int(args.get('max_points', 1800))
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise ValueError()
        if end-start > 31*86400 or not 100 <= limit <= 4000:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('Pilih rentang awal–akhir yang valid, maksimal 31 hari.')
    series = [{}, {}]
    with store.connection() as db:
        for row in db.execute('SELECT epoch,sensor,temperature FROM readings '
                              'WHERE epoch>=? AND epoch<=? ORDER BY epoch', (start, end)):
            if row['sensor'] in (1, 2):
                series[row['sensor']-1][row['epoch']] = [row['epoch'], row['temperature'], 'archive']
    state = monitor.state()
    for i, history in enumerate(state.get('history', [])):
        sensor = state['config'][f'sensor{i+1}']
        for stamp, raw in history:
            if start <= stamp <= end:
                series[i][stamp] = [stamp, apply_calibration(raw, sensor), 'live']
    points = [sorted(channel.values(), key=lambda p:p[0]) for channel in series]
    return dict(start=start, end=end, series=[reduce_points(p, limit) for p in points],
                counts=[len(p) for p in points], timezone='Asia/Jakarta',
                archive_interval_minutes=state['config'].get('logging', {}).get('interval_minutes', 1),
                server_time=time.time())
