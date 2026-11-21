#!/usr/bin/env python3
"""Single-worker Modbus RTU temperature reader; missing values remain None."""
import inspect
import json
import logging
import threading
import time
from pathlib import Path
from pymodbus.client import ModbusSerialClient

log = logging.getLogger(__name__)


class ModbusTemperatureReader:
    def __init__(self, port='AUTO', baudrate=9600, slave_id=1, callback=None,
                 register_address=0, parity='N', stopbits=1, scale=10.0,
                 sensor2_enabled=False, sensor2_slave_id=2, sensor2_register_address=0):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.callback = callback
        self.register_address = register_address
        self.parity = parity
        self.stopbits = stopbits
        self.scale = float(scale)
        if self.scale <= 0:
            raise ValueError('Temperature scale must be positive')
        self.sensor2_enabled = sensor2_enabled
        self.sensor2_slave_id = sensor2_slave_id
        self.sensor2_register_address = sensor2_register_address
        self.client = None
        self.thread = None
        self.is_running = False
        self.connected = False
        self.error_message = ''
        self.last_s1 = self.last_s2 = None
        self.last_receive_time = 0
        self.receive_times = [0, 0]
        self.errors = ['', '']
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._auto = not port or port == 'AUTO'
        params = inspect.signature(ModbusSerialClient.read_holding_registers).parameters
        self._id_key = 'device_id' if 'device_id' in params else 'slave'

    @staticmethod
    def find_modbus_port():
        ports = sorted(Path('/dev/serial/by-id').glob('*'))
        ports += sorted(Path('/dev').glob('ttyUSB*'))
        ports += sorted(Path('/dev').glob('ttyACM*'))
        return next((str(p) for p in ports if p.exists()), None)

    def _open(self):
        if self.client:
            self.client.close()
            self.client = None
        if self._auto:
            self.port = self.find_modbus_port() or 'AUTO'
        if self.port == 'AUTO':
            raise OSError('USB Modbus tidak ditemukan. Periksa kabel USB.')
        self.client = ModbusSerialClient(port=self.port, baudrate=self.baudrate,
                                        bytesize=8, parity=self.parity,
                                        stopbits=self.stopbits, timeout=1, retries=0)
        if not self.client.connect():
            raise OSError('Port USB gagal dibuka. Periksa kabel dan izin serial.')

    def connect(self, port=None, baudrate=None):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return True, 'Reader sudah berjalan'
            if port:
                self.port = port
                self._auto = port == 'AUTO'
            if baudrate:
                self.baudrate = baudrate
            self._stop.clear()
            self.is_running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True,
                                           name='pt100-modbus')
            self.thread.start()
        return True, 'Reader aktif; menunggu respons sensor'

    def disconnect(self):
        self.is_running = False
        self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=4)
        with self.lock:
            if self.client:
                self.client.close()
                self.client = None
            self.connected = False
            self.last_s1 = self.last_s2 = None

    def read_temperatures(self):
        with self.lock:
            now = time.time()
            if not self.connected or now - self.last_receive_time > 4:
                return None, None
            return (self.last_s1 if now - self.receive_times[0] <= 4 else None,
                    self.last_s2 if now - self.receive_times[1] <= 4 else None)

    def snapshot(self):
        with self.lock:
            s1, s2 = self.read_temperatures()
            return dict(s1=s1, s2=s2, connected=s1 is not None or s2 is not None,
                        s1_connected=s1 is not None, s2_connected=s2 is not None,
                        timestamps=list(self.receive_times), errors=list(self.errors),
                        timestamp=self.last_receive_time, error=self.error_message,
                        port=self.port)

    def _read_temperature(self, slave_id, address):
        result = self.client.read_holding_registers(
            address=address, count=1, **{self._id_key: slave_id})
        if result.isError() or not getattr(result, 'registers', None):
            raise OSError(f'Sensor ID {slave_id} tidak merespons: {result}')
        raw = result.registers[0]
        if raw & 0x8000:
            raw -= 0x10000
        return raw / self.scale

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                if self.client is None:
                    self._open()
                channels = [(self.slave_id, self.register_address)]
                if self.sensor2_enabled:
                    channels.append((self.sensor2_slave_id, self.sensor2_register_address))
                # Publish each channel independently. Serial I/O never holds the UI lock.
                for index, (device, address) in enumerate(channels):
                    if self._stop.is_set():
                        break
                    try:
                        value = self._read_temperature(device, address)
                        received = time.time()
                        error = ''
                    except Exception as exc:
                        value, received, error = None, 0, str(exc)
                    with self.lock:
                        if index == 0:
                            self.last_s1 = value
                        else:
                            self.last_s2 = value
                        self.receive_times[index] = received
                        self.errors[index] = error
                        if received:
                            self.last_receive_time = received
                        self.connected = self.last_s1 is not None or self.last_s2 is not None
                        self.error_message = ' | '.join(e for e in self.errors if e)
                    self._stop.wait(0.1)
                if self._stop.is_set():
                    break
                s1, s2 = self.read_temperatures()
                if s1 is None and s2 is None:
                    raise OSError(self.error_message or 'Sensor tidak merespons')
                if self.callback:
                    try:
                        self.callback(s1, s2, json.dumps(dict(s1=s1, s2=s2)))
                    except Exception:
                        log.exception('Temperature callback failed')
                self._stop.wait(1)
            except Exception as exc:
                with self.lock:
                    self.error_message = str(exc)
                    self.connected = False
                    self.last_s1 = self.last_s2 = None
                if self.client:
                    self.client.close()
                    self.client = None
                log.warning('Modbus: %s', exc)
                self._stop.wait(2)
        self.is_running = False
