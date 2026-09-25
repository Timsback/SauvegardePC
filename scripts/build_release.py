"""Reproducible Windows package build for developer machines and GitHub Actions."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    metadata = json.loads((ROOT/'app/release.json').read_text(encoding='utf-8'))
    version, repository = metadata['version'], metadata['repository']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not repository:
        raise ValueError('Configurez app/release.json avant la compilation.')
    if os.environ.get('GITHUB_REF_TYPE') == 'tag' and os.environ.get('GITHUB_REF_NAME') != 'v'+version:
        raise ValueError('Le tag Git doit correspondre à la version dans app/release.json.')
    iscc = shutil.which('ISCC.exe') or str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))/'Inno Setup 6/ISCC.exe')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
        '--name', 'SauvegardePC', '--distpath', str(ROOT/'dist_release'), '--workpath', str(ROOT/'build'),
        '--specpath', str(ROOT/'build'), '--collect-all', 'webview', '--icon', str(ROOT/'app/assets/icon.ico'),
        '--add-data', f'{ROOT / "app/assets"};assets', '--add-data', f'{ROOT / "app/web"};web',
        '--add-data', f'{ROOT / "app/release.json"};.', str(ROOT/'app/web_main.py')], cwd=ROOT, check=True)
    subprocess.run([iscc, '/DMyAppVersion='+version, str(ROOT/'installer.iss')], cwd=ROOT, check=True)
    installer = ROOT/'SauvegardePC-Setup.exe'
    with installer.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    (ROOT/'SauvegardePC-Setup.exe.sha256').write_text(checksum+'  SauvegardePC-Setup.exe\n', encoding='utf-8')
    print(f'Version {version} prête : {installer.name}, SHA-256 {checksum}')


if __name__ == '__main__':
    main()
