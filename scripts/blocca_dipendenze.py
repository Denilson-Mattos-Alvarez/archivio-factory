"""Blocca versioni e hash PyPI dei componenti runtime già installati e collaudati."""
import importlib.metadata
import json
import re
import urllib.request
from pathlib import Path

PACKAGES = ['Django', 'djangorestframework', 'psycopg', 'psycopg-binary', 'asgiref', 'sqlparse', 'typing-extensions', 'tzdata']


def main():
    lines = ['# Versioni runtime collaudate; tutti gli hash provengono da PyPI via HTTPS.',
             '# Installazione: python -m pip install --require-hashes --only-binary=:all: -r requirements-lock.txt', '']
    for package in PACKAGES:
        version = importlib.metadata.version(package)
        if not re.fullmatch(r'[0-9A-Za-z.+_-]+', version):
            raise ValueError('Versione non valida.')
        with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{version}/json', timeout=30) as response:
            metadata = json.load(response)
        hashes = sorted({item['digests']['sha256'] for item in metadata['urls'] if item['packagetype'] == 'bdist_wheel'})
        if not hashes or any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in hashes):
            raise ValueError('Nessuna distribuzione binaria verificabile.')
        lines.append(package + '==' + version + ' \\')
        for index, digest in enumerate(hashes):
            lines.append('    --hash=sha256:' + digest + (' \\' if index + 1 < len(hashes) else ''))
        lines.append('')
    output = Path(__file__).resolve().parents[1] / 'requirements-lock.txt'
    output.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Bloccati {len(PACKAGES)} componenti runtime con hash ufficiali.')


if __name__ == '__main__':
    main()
