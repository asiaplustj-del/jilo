#!/bin/bash
cd "$(dirname "$0")"
PY=""
for c in python3.12 python3.11 python3.13 python3; do
  if command -v $c >/dev/null 2>&1 && $c -c "import sys; assert sys.version_info>=(3,9)" 2>/dev/null; then PY=$c; break; fi
done
if [ -z "$PY" ]; then echo "Нужен Python 3: https://www.python.org/downloads/"; read -r; exit 1; fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Готовлю окружение (первый запуск — несколько минут)..."
  $PY -m venv .venv || { echo "Не удалось создать окружение"; read -r; exit 1; }
fi
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || { echo "Не удалось поставить библиотеки"; read -r; exit 1; }

.venv/bin/python run_jilo.py
