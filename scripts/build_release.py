"""Reproducible Windows package build for developer machines and GitHub Actions.

Code signing is optional: set SIGN_PFX (path to a .pfx certificate) and
SIGN_PFX_PASSWORD to sign the executable and the installer with signtool.
Without them, the build is unsigned (SmartScreen may warn on first install).
"""
from pathlib import Path
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TIMESTAMP_URL = 'http://timestamp.digicert.com'


def find_signtool():
    found = shutil.which('signtool.exe')
    if found:
        return found
    kits = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))/'Windows Kits/10/bin'
    candidates = sorted(glob.glob(str(kits/'*'/'x64'/'signtool.exe')))
    return candidates[-1] if candidates else None


def sign(path):
    certificate, password = os.environ.get('SIGN_PFX'), os.environ.get('SIGN_PFX_PASSWORD', '')
    if not certificate:
        print(f'Signature ignorée (SIGN_PFX non défini) : {Path(path).name}')
        return
    signtool = find_signtool()
    if not signtool:
        raise FileNotFoundError('signtool.exe introuvable : installez le SDK Windows pour signer.')
    subprocess.run([signtool, 'sign', '/fd', 'SHA256', '/tr', TIMESTAMP_URL, '/td', 'SHA256',
                    '/f', certificate, '/p', password, str(path)], cwd=ROOT, check=True)
    print(f'Signé : {Path(path).name}')


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
    sign(ROOT/'dist_release'/'SauvegardePC.exe')
    subprocess.run([iscc, '/DMyAppVersion='+version, str(ROOT/'installer.iss')], cwd=ROOT, check=True)
    installer = ROOT/'SauvegardePC-Setup.exe'
    sign(installer)
    with installer.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    (ROOT/'SauvegardePC-Setup.exe.sha256').write_text(checksum+'  SauvegardePC-Setup.exe\n', encoding='utf-8')
    print(f'Version {version} prête : {installer.name}, SHA-256 {checksum}')


if __name__ == '__main__':
    main()
