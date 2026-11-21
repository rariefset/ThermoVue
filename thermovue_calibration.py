"""Temperature correction shared by acquisition, archives, and API consumers."""
import math


def validate_points(points):
    if not isinstance(points, list) or not 3 <= len(points) <= 20:
        raise ValueError('Isi minimal 3 dan maksimal 20 titik kalibrasi.')
    result = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {'raw_c', 'reference_c'}:
            raise ValueError('Setiap titik harus berisi suhu mentah dan suhu referensi.')
        values = {}
        for key in ('raw_c', 'reference_c'):
            value = point[key]
            if isinstance(value, bool) or value is None:
                raise ValueError('Semua suhu kalibrasi harus berupa angka yang valid.')
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError('Semua suhu kalibrasi harus berupa angka yang valid.')
            if not math.isfinite(number):
                raise ValueError('Semua suhu kalibrasi harus berupa angka yang valid.')
            values[key] = number
        result.append(values)
    result.sort(key=lambda p: p['raw_c'])
    for left, right in zip(result, result[1:]):
        if right['raw_c'] <= left['raw_c']:
            raise ValueError('Suhu mentah setiap titik harus berbeda.')
        if right['reference_c'] <= left['reference_c']:
            raise ValueError('Suhu referensi harus meningkat mengikuti suhu mentah.')
    return result


def apply_calibration(raw, sensor):
    if raw is None:
        return None
    calibration = sensor.get('calibration', {})
    points = calibration.get('points', [])
    if calibration.get('method') != 'piecewise_linear' or len(points) < 3:
        return raw + sensor.get('offset', 0)
    # Outside the measured range, retain the nearest endpoint's correction.
    if raw <= points[0]['raw_c']:
        return raw + points[0]['reference_c'] - points[0]['raw_c']
    if raw >= points[-1]['raw_c']:
        return raw + points[-1]['reference_c'] - points[-1]['raw_c']
    for left, right in zip(points, points[1:]):
        if raw <= right['raw_c']:
            ratio = (raw - left['raw_c']) / (right['raw_c'] - left['raw_c'])
            return left['reference_c'] * (1-ratio) + right['reference_c'] * ratio
