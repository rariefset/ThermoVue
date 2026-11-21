# ThermoVue

Dashboard Raspberry Pi untuk sistem vacuum frying: dua thermocouple tipe K melalui transmitter RS485, estimasi pressure atmosfer, dan status kiriman solenoid.

## Fitur

- Dashboard desktop untuk layar Raspberry Pi 800×480 dan website ThermoVue lengkap.
- Suhu 1, Suhu 2, grafik langsung, alarm, riwayat SQLite, dan ekspor log.
- Kalibrasi satu titik atau tabel **3–20 titik** per sensor; draft tabel disimpan lokal di browser.
- Interpolasi linear antar titik. Di luar rentang kalibrasi, gunakan offset titik ujung terdekat.
- Koreksi berlaku pada tampilan, grafik, arsip baru, dan sampel API baru. Arsip dan antrean lama tidak dihitung ulang.
- Bridge HMAC-SHA256 dengan antrean SQLite, percobaan ulang, dan diagnostik jaringan.

Pressure berasal dari estimasi Open-Meteo, bukan sensor fisik. Solenoid merupakan status kiriman; aplikasi tidak mengontrol relay atau membaca umpan balik solenoid fisik.

## Struktur

| Lokasi | Isi |
| --- | --- |
| `thermovue_service.py` | HTTP service dan pemilik tunggal port RS485 |
| `jambu/jambu/modbus_reader.py` | Pembaca Modbus RTU |
| `thermovue_calibration.py` | Validasi dan koreksi kalibrasi |
| `thermovue_history.py`, `thermovue_trend.py`, `thermovue_logs.py` | Arsip, grafik, dan log |
| `thermovue-web/static/` | Website lengkap dan dashboard layar Raspberry Pi |
| `pt100-api-bridge/bridge.py` | Bridge telemetri; nama folder dipertahankan untuk kompatibilitas |
| `desktop/` | Launcher Chromium dan shortcut desktop |
| `tests/` | Pengujian kalibrasi, grafik, arsip/log, dan bridge |

## Konfigurasi Raspberry Pi

Parameter transmitter yang digunakan: Modbus ID **1** dan **5**, holding register **0**, satu register signed int16 dibagi **10**, **9600 baud 8N1**. Zona waktu tampilan Asia/Jakarta.

Kode ini mengikuti tata letak pemasangan `/home/admin`. Backend menggunakan konfigurasi `/home/admin/jambu/jambu/config.json`, data arsip `/home/admin/.local/state/thermovue/`, dan log bridge `/home/admin/pt100-api-bridge/data/`. Saat memasang pada lokasi atau pengguna berbeda, sesuaikan path backend, launcher, dan shortcut.

1. Siapkan Python 3.11+ dan environment Linux/Raspberry Pi, lalu pasang `requirements.txt`.
2. Letakkan berkas backend di `/home/admin`, frontend di `/home/admin/thermovue-web/static`, pembaca/config di `/home/admin/jambu/jambu`, dan bridge di `/home/admin/pt100-api-bridge`.
3. Untuk pemasangan baru, salin `config.example.json` menjadi `config.json` di masing-masing folder. Tentukan port serial, node, dan device secret secara lokal. **Jangan menimpa konfigurasi perangkat yang sudah digunakan.** Batasi izin konfigurasi bridge menjadi `600`.
4. Pastikan `thermovue-data.service` menjadi satu-satunya pemilik serial. Dashboard dan bridge memakai API lokal `http://127.0.0.1:5000`.
5. Salin `desktop/thermovue-dashboard` ke `~/.local/bin/`, lalu shortcut ke `~/Desktop/`. Beri izin eksekusi pada keduanya. Pasang Chromium, curl, dan zenity di Raspberry Pi.

Jangan menjalankan instance service pembaca kedua pada port yang sama. Repo ini tidak berisi kredensial produksi, konfigurasi aktual, database pembacaan, antrean, atau log.

## Kalibrasi tabel

Buka **Kalibrasi → Tabel**. Isi minimal tiga pasangan suhu mentah dan suhu alat referensi saat stabil. Tombol **Ambil** memasukkan pembacaan mentah terbaru. Suhu mentah harus berbeda, dan referensi harus meningkat mengikuti suhu mentah. Klik **Simpan kalibrasi** untuk menerapkan tabel. **Muat tabel tersimpan** mengembalikan tabel aktif ke draft; **Reset koreksi** menonaktifkan tabel dan offset.

API `/api/state` tetap menyediakan `s1`, `s2`, dan `history` sebagai nilai mentah. `calibrated_s1` dan `calibrated_s2` berisi suhu terkoreksi. Konfigurasi kalibrasi disimpan melalui `POST /api/calibration`.

## Validasi

Pada lingkungan Linux dengan dependensi terpasang:

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
node tests/test_calibration_math.cjs
```

Bridge menggunakan modul Linux `fcntl`. Pengujian bridge memerlukan Linux. Snapshot ini memuat subset pengujian yang tersedia secara lokal; rangkaian 35 pengujian backend dan pemeriksaan dashboard 800×412 sebelumnya lulus pada Raspberry Pi saat pemasangan fitur kalibrasi tabel.

## Asal snapshot

Commit awal disusun dari salinan pengembangan terakhir yang dipakai dalam pemasangan dashboard dan kalibrasi tabel. Raspberry Pi tidak terjangkau saat pengarsipan repo, sehingga perubahan perangkat setelah verifikasi terakhir belum disinkronkan. Pembaca Modbus dipulihkan dari sumber yang telah dibaca pada sesi sebelumnya. Repo tidak menerapkan ulang perubahan ke perangkat.
