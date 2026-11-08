#!/usr/bin/env bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ ! -d "venv" ] || [ "requirements.txt" -nt "venv/pyvenv.cfg" ]; then
    echo "Membuat atau memperbarui virtual environment..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

echo "Memulai server Chartiq Free..."
./venv/bin/python run.py

