"""Avvio del solo archivio locale; non genera video né chiama servizi esterni."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
os.environ.setdefault('ARCHIVIO_AMBIENTE', 'sviluppo')
subprocess.run([sys.executable, str(root / 'manage.py'), 'migrate', '--noinput'], cwd=root, check=True)
subprocess.run([sys.executable, str(root / 'manage.py'), 'runserver', '127.0.0.1:58540', '--noreload', '--insecure'], cwd=root, check=True)
