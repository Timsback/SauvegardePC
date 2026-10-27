"""Public GitHub Releases updates. No GitHub credential is shipped to client PCs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
RELEASE = json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
VERSION = RELEASE['version']
REPOSITORY = RELEASE['repository']
ASSET = 'SauvegardePC-Setup.exe'
MAX_INSTALLER_SIZE = 500 * 1024 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Numéro de version stable invalide.')
    return tuple(map(int, value.removeprefix('v').split('.')))


def valid_repository(value):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+', value or ''))


def safe_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.hostname not in {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}):
        raise ValueError('Adresse de mise à jour non autorisée.')
    return url


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = Request(safe_url(url), headers={'User-Agent': 'SauvegardePC/'+VERSION,
        'Accept': 'application/vnd.github+json' if urlsplit(url).hostname == 'api.github.com' else 'application/octet-stream'})
    return build_opener(GitHubRedirect()).open(request, timeout=20)


def read_url(url, maximum=2 * 1024 * 1024):
    with open_url(url) as response:
        data = response.read(maximum+1)
    if len(data) > maximum:
        raise ValueError('Réponse de mise à jour trop volumineuse.')
    return data


def find_release(repository, current, fetch=read_url):
    if not valid_repository(repository):
        raise ValueError('Le dépôt GitHub de publication n’est pas encore configuré.')
    data = json.loads(fetch(f'https://api.github.com/repos/{repository}/releases/latest'))
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data.get('tag_name')
    if version_tuple(tag) <= version_tuple(current):
        return None
    version = tag.removeprefix('v')
    prefix = f'https://github.com/{repository}/releases/download/{quote(tag, safe="")}/'
    assets = [a for a in data.get('assets', []) if a.get('name') == ASSET and a.get('state') == 'uploaded']
    if len(assets) != 1:
        raise ValueError('La nouvelle version ne contient pas encore un installeur complet.')
    asset = assets[0]
    url = asset.get('browser_download_url', '')
    if url != prefix + ASSET:
        raise ValueError('L’installeur ne provient pas du dépôt configuré.')
    size = asset.get('size')
    if type(size) is not int or not 0 < size <= MAX_INSTALLER_SIZE:
        raise ValueError('Taille de l’installeur invalide.')
    digest = asset.get('digest') or ''
    if re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
        checksum = digest.split(':', 1)[1].lower()
    else:
        checksums = [a for a in data.get('assets', []) if a.get('name') == ASSET+'.sha256' and a.get('state') == 'uploaded']
        if len(checksums) != 1 or checksums[0].get('browser_download_url') != prefix+ASSET+'.sha256':
            raise ValueError('Empreinte de vérification absente. Mise à jour indisponible.')
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?SauvegardePC-Setup\.exe\s*', fetch(prefix+ASSET+'.sha256').decode('utf-8'))
        if not match:
            raise ValueError('Empreinte de vérification invalide.')
        checksum = match[1].lower()
    return dict(version=version, url=url, size=size, sha256=checksum,
                notes=str(data.get('body') or '')[:6000], page=f'https://github.com/{repository}/releases/tag/{quote(tag, safe="")}')


def download_release(release, directory, progress=lambda value: None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Unique staging folder; failed or interrupted downloads are never executable targets.
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix='update-', dir=directory))
    staging = folder/(ASSET+'.part')
    target = folder/ASSET
    digest, total, started = hashlib.sha256(), 0, time.monotonic()
    try:
        with open_url(release['url']) as response, staging.open('xb') as output:
            while chunk := response.read(128 * 1024):
                total += len(chunk)
                if total > release['size'] or total > MAX_INSTALLER_SIZE or time.monotonic()-started > 900:
                    raise ValueError('Téléchargement incomplet, trop volumineux ou trop lent.')
                output.write(chunk)
                digest.update(chunk)
                progress(round(total * 100 / release['size']))
        if total != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('Vérification de l’installeur échouée. Aucun programme n’a été lancé.')
        staging.replace(target)
        return target
    except Exception:
        staging.unlink(missing_ok=True)
        raise


def reset_frozen_environment():
    """Let programs started from here (installer, elevated copy) launch SauvegardePC.exe cleanly.

    The PyInstaller onefile bootloader stores its own _PYI_* variables in the environment.
    A new SauvegardePC.exe inheriting them, even through the installer, believes it is the
    unpacked child of this process and stops with "Security validation failure".
    """
    for name in [n for n in os.environ if n.upper().startswith('_PYI_')]:
        del os.environ[name]
    os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = '1'


def launch_installer(path, checksum):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Installez la version compilée pour utiliser la mise à jour intégrée.')
    with Path(path).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
            raise ValueError('L’installeur a été modifié après le téléchargement.')
    # Keep the wizard visible, preserve the installation directory, never force-close work.
    args = ['/NORESTART', '/NOCLOSEAPPLICATIONS', '/DIR='+str(Path(sys.executable).parent)]
    reset_frozen_environment()
    os.startfile(str(path), 'open', subprocess.list2cmdline(args))


class UpdateManager:
    def __init__(self, repository=REPOSITORY, current=VERSION):
        self.repository, self.current = repository, current
        self.lock = threading.RLock()
        self.release = None
        self.state = dict(status='idle' if valid_repository(repository) else 'unconfigured', current=current,
                          latest='', message='', progress=0, checked_at='', notes='')

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def set_state(self, **values):
        with self.lock:
            self.state.update(values)

    def check(self):
        with self.lock:
            if self.state['status'] in ('checking', 'downloading', 'installing'):
                return False
            self.state.update(status='checking', message='Recherche d’une nouvelle version…')
        def worker():
            try:
                release = find_release(self.repository, self.current)
                with self.lock:
                    self.release = release
                    self.state.update(status='available' if release else 'current', latest=release['version'] if release else '',
                        notes=release['notes'] if release else '', message='Une nouvelle version est disponible.' if release else 'Vous utilisez la dernière version publiée.')
            except HTTPError as exc:
                self.set_state(status='error', message='Aucune version publique accessible pour le moment.' if exc.code == 404 else 'GitHub est temporairement indisponible. Réessayez plus tard.')
            except Exception as exc:
                self.set_state(status='error', message='Vérification impossible : '+str(exc))
            finally:
                self.set_state(checked_at=datetime.now(timezone.utc).isoformat())
        threading.Thread(target=worker, daemon=True).start()
        return True

    def download(self, directory):
        with self.lock:
            if self.state['status'] != 'available' or not self.release:
                raise ValueError('Recherchez une mise à jour disponible avant de l’installer.')
            release = dict(self.release)
            self.state.update(status='downloading', progress=0, message='Téléchargement et vérification de la mise à jour…')
        try:
            path = download_release(release, directory, lambda value: self.set_state(progress=value))
            return path, release['sha256']
        except Exception as exc:
            self.set_state(status='available', message=str(exc), progress=0)
            raise
