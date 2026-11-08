# Chartiq Free 📈

Chartiq Free adalah aplikasi market replay dan backtesting untuk aset forex, komoditas, indeks, dan kripto. Aplikasi ini memanfaatkan FastAPI, SQLite, dan Yahoo Finance sebagai sumber data awal, serta frontend static dengan TradingView Lightweight Charts.

## Fitur utama

- Replay pasar dari data candle Yahoo Finance atau hasil upload CSV
- Simulasi sesi trading dengan saldo awal, trade journal, dan statistik akun
- Dukungan symbol forex, emas, indeks, dan crypto
- Upload CSV MetaTrader 4/5 atau TradingView dengan parsing otomatis
- Endpoint API yang tetap kompatibel dengan frontend yang sudah ada

## Jalankan aplikasi

Dari folder proyek:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python run.py
```

Atau gunakan helper:

```bash
chmod +x run.sh
./run.sh
```

Setelah server aktif, buka:

```text
http://127.0.0.1:8000
```

Untuk menjalankan dengan reload otomatis saat mode pengembangan:

```bash
DEV=1 ./venv/bin/python run.py
```

Jika port atau host perlu diubah:

```bash
HOST=0.0.0.0 PORT=9000 ./venv/bin/python run.py
```

## Batasan data

- Data futures (misalnya XAUUSD, NASDAQ, US30, US500) tidak sama dengan harga spot broker; beberapa symbol memakai kontrak futures Yahoo Finance.
- Yahoo Finance membatasi range data per interval; periode yang terlalu panjang akan dipangkas sesuai aturan yfinance yang terpasang.
- Resampling 4H mengikuti aturan `origin='start_day'` dan hasilnya dapat berbeda dari broker yang memakai 4H standar.
- Data forex biasanya tidak memiliki volume yang dapat diandalkan seperti aset komoditas atau saham; pada beberapa endpoint volume dapat terisi 0 atau tidak relevan.

## Struktur proyek

- `app.py`: endpoint API FastAPI
- `data_fetcher.py`: fetch dan parsing candle dari Yahoo Finance/CSV
- `session_db.py`: persisten trade dan session
- `run.py`: entry point aplikasi
- `static/`: frontend dan asset statis

## Catatan

- Data SQLite yang sudah ada tidak dihapus; migrasi dibuat aman untuk data lama.
- Semua file database lokal seperti `sessions.db` dan `market_cache.db` disimpan di root proyek dan diabaikan oleh Git lewat `.gitignore`.

