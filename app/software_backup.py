"""Per-application snapshots. Discovery is deliberately conservative, never exhaustive."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime
from uuid import uuid4

try:
    import winreg
except ImportError:
    winreg = None

# Explicit application paths: never infer ownership from a publisher alone.
RECIPES = {
    'vscode': (r'Visual Studio Code', [('roaming', 'Code'), ('home', '.vscode')], []),
    'firefox': (r'Mozilla Firefox', [('roaming', 'Mozilla/Firefox')], []),
    'chrome': (r'Google Chrome', [('local', 'Google/Chrome/User Data')], []),
    'edge': (r'Microsoft Edge$', [('local', 'Microsoft/Edge/User Data')], []),
    'vlc': (r'VLC media player', [('roaming', 'vlc')], []),
    'notepadpp': (r'Notepad\+\+', [('roaming', 'Notepad++')], []),
    '7zip': (r'7-Zip', [], [r'Software\7-Zip']),
    'obs': (r'OBS Studio', [('roaming', 'obs-studio')], []),
    'thunderbird': (r'Mozilla Thunderbird', [('roaming', 'Thunderbird')], []),
}
NOTES = [
    'Réinstallez le logiciel (même version si possible), puis fermez-le avant de restaurer.',
    'Activation, connexion au compte et secrets chiffrés par Windows peuvent nécessiter une nouvelle saisie.',
    'Analyse indicative : les données externes, services, pilotes et composants système ne sont pas recréés.',
    'Les profils peuvent contenir des données sensibles. Conservez la sauvegarde sur un support protégé.',
]


def roots():
    home = Path(os.environ.get('USERPROFILE', Path.home()))
    return {'home': home, 'roaming': Path(os.environ.get('APPDATA', home / 'AppData/Roaming')),
            'local': Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')),
            'programdata': Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData'))}


def child(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if not relative or rel.is_absolute() or rel.drive or any(p in ('..', '.') or ':' in p for p in rel.parts):
        raise ValueError('Chemin de sauvegarde invalide')
    target = root / rel
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ValueError('Chemin hors du dossier autorisé')
    for p in [target, *target.parents]:
        if p == root:
            break
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('Les liens et jonctions ne sont pas pris en charge')
    return target


def installed():
    if winreg is None:
        return []
    result = {}
    for hive, scope in [(winreg.HKEY_CURRENT_USER, 'user'), (winreg.HKEY_LOCAL_MACHINE, 'machine')]:
        for view in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view) as parent:
                    for i in range(winreg.QueryInfoKey(parent)[0]):
                        keyname = winreg.EnumKey(parent, i)
                        try:
                            with winreg.OpenKey(parent, keyname) as key:
                                def value(name):
                                    try:
                                        return str(winreg.QueryValueEx(key, name)[0])
                                    except OSError:
                                        return ''
                                name = value('DisplayName')
                                if not name:
                                    continue
                                identity = hashlib.sha256((scope + str(view) + keyname).encode()).hexdigest()[:20]
                                result[identity] = dict(id=identity, name=name, version=value('DisplayVersion'), publisher=value('Publisher'), install_location=value('InstallLocation'), website=value('URLInfoAbout'),
                                    scope=scope, view=32 if view == winreg.KEY_WOW64_32KEY else 64, product_key=keyname,
                                    display_icon=value('DisplayIcon'), uninstall=value('UninstallString'),
                                    install_source=value('InstallSource'), bundle_cache=value('BundleCachePath'))
                        except OSError:
                            continue
            except OSError:
                continue
    return sorted(result.values(), key=lambda p: p['name'].casefold())


def analyze_legacy(program):
    recipe = next((key for key, data in RECIPES.items() if re.search(data[0], program['name'], re.I)), None)
    paths = list(RECIPES[recipe][1]) if recipe else []
    # Unknown software: exact directory-name matches only, shown for review.
    if not recipe:
        name = program['name']
        if name not in ('.', '..') and not re.search(r'[\\/:*?"<>|]', name):
            paths = [(base, name) for base in ('roaming', 'local', 'programdata')]
    files = []
    for base, rel in paths:
        path = child(roots()[base], rel)
        if path.is_dir():
            files.append(dict(base=base, relative=rel, source=str(path)))
    registry = []
    if winreg:
        for key in (RECIPES[recipe][2] if recipe else []):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                    registry.append(key)
            except OSError:
                pass
    return dict(program=program, recipe=recipe, files=files, registry=registry, notes=NOTES,
                confidence='Emplacements connus' if recipe else 'Correspondances de noms à vérifier — couverture partielle')


def registry_read(path):
    entries = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        subs, values, _ = winreg.QueryInfoKey(key)
        for i in range(values):
            name, value, kind = winreg.EnumValue(key, i)
            entries.append(dict(key=path, name=name, value=value.hex() if isinstance(value, bytes) else value, type=kind))
        for i in range(subs):
            entries.extend(registry_read(path + '\\' + winreg.EnumKey(key, i)))
    return entries


def snapshot_folder_name(program):
    """Keep the application recognizable in a bounded, Windows-safe folder name."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', program.get('name') or '')
    name = name.strip(' .')[:80].rstrip(' .') or 'Logiciel'
    return name + '-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]


def backup_legacy(plan, destination, log):
    folder = Path(destination) / 'Logiciels' / snapshot_folder_name(plan['program'])
    for item in plan['files']:
        src = child(roots()[item['base']], item['relative'])
        if folder.resolve().is_relative_to(src.resolve()):
            raise ValueError('Choisissez une destination extérieure aux données du logiciel.')
    folder.mkdir(parents=True)
    manifest = dict(schema=1, created=datetime.now().isoformat(), **plan, payload=[], registry_values=[], errors=[])
    for i, item in enumerate(plan['files']):
        src = child(roots()[item['base']], item['relative'])
        def error(exc):
            manifest['errors'].append(str(exc))
        for directory, dirs, names in os.walk(src, followlinks=False, onerror=error):
            for d in list(dirs):
                path = Path(directory)/d
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    dirs.remove(d)
                    error(f'Lien ou jonction ignoré : {path}')
            for name in names:
                try:
                    source = child(src, str((Path(directory)/name).relative_to(src)))
                    relative = str(source.relative_to(src))
                    payload = f'data/{i}/{relative}'
                    target = child(folder, payload)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    manifest['payload'].append(dict(base=item['base'], relative=str(Path(item['relative'])/relative), payload=payload, sha256=digest(target)))
                except (OSError, ValueError) as exc:
                    error(exc)
    for key in plan['registry']:
        try:
            manifest['registry_values'].extend(registry_read(key))
        except OSError as exc:
            manifest['errors'].append(str(exc))
    manifest['status'] = 'partial' if manifest['errors'] else 'complete'
    (folder/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f"{len(manifest['payload'])} fichiers sauvegardés : {folder}")
    if manifest['errors']:
        raise RuntimeError(f"Sauvegarde partielle : {len(manifest['errors'])} erreurs. Détails dans manifest.json.")


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshots(source):
    root = Path(source)
    paths = [root/'manifest.json'] if (root/'manifest.json').exists() else sorted((root/'Logiciels' if (root/'Logiciels').is_dir() else root).glob('*/manifest.json'))
    result = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data['schema'] in (1, 2):
                result.append(dict(id=str(path.parent), name=data['program']['name'], version=data['program'].get('version', ''), website=data['program'].get('website', ''), created=data['created'], status=data['status'], count=len(data['payload']),
                    schema=data['schema'], installer=data.get('installer', {}).get('method', 'manual'),
                    installer_label=data.get('installer', {}).get('label', 'Ancienne sauvegarde : réinstallez le logiciel avant ses données'),
                    issues=len(data.get('errors', []))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def restore_legacy(folder, log):
    folder = Path(folder)
    data = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Format de sauvegarde incompatible')
    # Recompute permitted destinations rather than trusting arbitrary manifest paths.
    plan = analyze_legacy(data['program'])
    recipe = plan['recipe']
    allowed = RECIPES[recipe][1] if recipe else [(b, data['program']['name']) for b in ('roaming', 'local', 'programdata')]
    operations = []
    for entry in data['payload']:
        base, rel = entry['base'], Path(entry['relative'])
        if not any(base == b and rel.is_relative_to(Path(r)) for b, r in allowed):
            raise ValueError('Destination non autorisée pour cette application')
        source = child(folder, entry['payload'])
        target = child(roots()[base], str(rel))
        if digest(source) != entry['sha256']:
            raise ValueError(f'Fichier manquant ou altéré : {source.name}')
        operations.append((source, target))
    allowed_keys = RECIPES[recipe][2] if recipe else []
    for entry in data['registry_values']:
        if not any(entry['key'] == key or entry['key'].startswith(key + '\\') for key in allowed_keys):
            raise ValueError('Clé de registre non autorisée')
    recovery = folder / ('Avant-restauration-' + uuid4().hex[:8])
    recovery.mkdir()
    # Preserve all existing files and registry values before the first mutation.
    for i, (_, target) in enumerate(operations):
        if target.exists():
            shutil.copy2(target, recovery/str(i))
    (recovery/'fichiers.json').write_text(json.dumps([str(t) for _, t in operations]), encoding='utf-8')
    prior = []
    for key in allowed_keys:
        try:
            prior.extend(registry_read(key))
        except FileNotFoundError:
            pass
    (recovery/'registre.json').write_text(json.dumps(prior), encoding='utf-8')
    log(f'État précédent conservé dans {recovery}. Application des données…')
    for source, target in operations:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for entry in data['registry_values']:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, entry['key']) as key:
            value = bytes.fromhex(entry['value']) if entry['type'] in (winreg.REG_BINARY, winreg.REG_NONE) else entry['value']
            winreg.SetValueEx(key, entry['name'], 0, entry['type'], value)
    log(f'Restauration effectuée : {len(operations)} fichiers. État précédent conservé dans {recovery}')


def analyze(program, log=lambda message: None):
    from software_engine import discover
    return discover(program, log)


def backup(plan, destination, log, progress=None):
    if plan.get('engine') != 2:
        return backup_legacy(plan, destination, log)
    from software_engine import save_snapshot
    return save_snapshot(plan, destination, log, progress)


def restore(folder, log, progress=None):
    data = json.loads((Path(folder)/'manifest.json').read_text(encoding='utf-8'))
    if data.get('schema') == 1:
        return restore_legacy(folder, log)
    from software_engine import restore_snapshot
    return restore_snapshot(Path(folder), log, progress)
