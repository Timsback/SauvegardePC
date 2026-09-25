"""Evidence-based Windows application discovery and portable, versioned snapshots.

No program-specific rule is required. Recipes only supplement evidence discovery.
Installers recreate services; service registry trees are never blindly imported.
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

import software_backup as sb

GENERIC = {'application', 'apps', 'bin', 'client', 'common', 'data', 'desktop', 'files',
           'microsoft', 'program', 'programs', 'software', 'system', 'update', 'windows'}
NOTES = [
    'Analyse par indices : les dépendances partagées, données distantes et emplacements sans lien identifiable peuvent manquer.',
    'Licences, activation, connexion et secrets liés à l’ancien Windows peuvent nécessiter une intervention.',
    'Services et composants : recréés par l’installeur lorsqu’il est disponible ; pilotes et comptes de service restent à vérifier.',
    'Les chemins inscrits à l’intérieur des fichiers de configuration ne sont pas réécrits automatiquement.',
    'Les sauvegardes contiennent des données sensibles et des exécutables. Utilisez un support protégé et une source de confiance.',
]


def run(args, timeout=45):
    """Never execute registry-supplied command strings or invoke a shell."""
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def powershell(script, timeout=35):
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], timeout)
    if result.returncode:
        raise OSError(result.stderr.strip() or 'Interrogation Windows impossible')
    return json.loads(result.stdout.lstrip('\ufeff').strip() or 'null')


def normalize(name):
    name = re.sub(r'\([^)]*\)|\[[^]]*\]', ' ', str(name).casefold())
    name = re.sub(r'\b(?:x64|x86|amd64|win64|win32|64[ -]?bit|32[ -]?bit|version|user|setup)\b', ' ', name)
    name = re.sub(r'\b\d+(?:\.\d+)+\b', ' ', name)
    return re.sub(r'[^\w+]', '', name)


def aliases(program):
    value = normalize(program.get('name', ''))
    return {value} if len(value) >= 3 and value not in GENERIC else set()


def matches(name, names):
    return normalize(name.lstrip('.')) in names


def all_roots():
    result = dict(sb.roots())
    result.update(programfiles=Path(os.environ.get('ProgramW6432', os.environ.get('ProgramFiles', 'C:/Program Files'))),
                  programfiles32=Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')))
    return result


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def encode_path(path):
    """Use current-user and system tokens instead of restoring an old user's home."""
    path = Path(path).absolute()
    for base, root in sorted(all_roots().items(), key=lambda pair: len(str(pair[1])), reverse=True):
        if path.is_relative_to(root.absolute()) and path != root.absolute():
            return base, str(path.relative_to(root.absolute()))
    return 'external', str(path)


def safe_source(path):
    path = Path(path).absolute()
    blocked = [*all_roots().values(), Path(os.environ.get('WINDIR', 'C:/Windows'))]
    if any(path == root.absolute() for root in blocked) or path == Path(path.anchor):
        return False
    windows = Path(os.environ.get('WINDIR', 'C:/Windows')).absolute()
    if path.is_relative_to(windows):
        return False
    return not any(linked(p) for p in (path, *path.parents))


def executable_path(value):
    value = os.path.expandvars(str(value)).strip()
    match = re.match(r'^"([^"]+\.(?:exe|dll))"', value, re.I) or re.match(r'^(.+?\.(?:exe|dll))(?=\s|,|$)', value, re.I)
    if not match:
        return None
    path = Path(match[1])
    return path if path.is_absolute() and path.exists() else None


def directories(path, errors):
    try:
        return [p for p in path.iterdir() if p.is_dir() and not linked(p)]
    except FileNotFoundError:
        return []
    except OSError as exc:
        errors.append(f'Emplacement non analysé : {path} ({exc})')
        return []


def registry_children(hive, key, view, errors):
    if sb.winreg is None:
        return []
    wr = sb.winreg
    try:
        with wr.OpenKey(getattr(wr, hive), key, 0, wr.KEY_READ | view_flag(view)) as handle:
            return [wr.EnumKey(handle, i) for i in range(wr.QueryInfoKey(handle)[0])]
    except FileNotFoundError:
        return []
    except OSError as exc:
        errors.append(f'Registre non analysé : {hive}\\{key} ({exc})')
        return []


def view_flag(view):
    return sb.winreg.KEY_WOW64_32KEY if view == 32 else sb.winreg.KEY_WOW64_64KEY


def registry_values(hive, path, view, errors, recursive=True, depth=0):
    wr = sb.winreg
    result = []
    if wr is None:
        return result
    if depth > 32:
        errors.append(f'Profondeur du registre limitée : {path}')
        return result
    try:
        with wr.OpenKey(getattr(wr, hive), path, 0, wr.KEY_READ | view_flag(view)) as key:
            subs, count, _ = wr.QueryInfoKey(key)
            for i in range(count):
                name, value, kind = wr.EnumValue(key, i)
                result.append(dict(hive=hive, key=path, view=view, name=name, type=kind,
                                   value=value.hex() if isinstance(value, bytes) else value))
            if recursive:
                for i in range(subs):
                    result.extend(registry_values(hive, path + '\\' + wr.EnumKey(key, i), view, errors, True, depth+1))
    except FileNotFoundError:
        pass
    except OSError as exc:
        errors.append(f'Clé non lue : {hive}\\{path} ({exc})')
    return result


def registry_allowed(path):
    parts = path.casefold().split('\\')
    forbidden = {'classes', 'policies', 'windows', 'windows nt', 'currentversion', 'run', 'runonce',
                 'installer', 'uninstall', 'services', 'environment', 'winlogon', 'image file execution options'}
    return len(parts) >= 2 and parts[0] == 'software' and not any(p in forbidden or p in ('', '.', '..') for p in parts[1:])


def discover_registry(names, publisher, errors):
    keys = []
    for hive in ('HKEY_CURRENT_USER', 'HKEY_LOCAL_MACHINE'):
        for view in (64, 32):
            for vendor in registry_children(hive, 'Software', view, errors):
                path = 'Software\\' + vendor
                if matches(vendor, names) and registry_allowed(path):
                    keys.append(dict(hive=hive, key=path, view=view, reason='Nom du produit'))
                elif normalize(vendor) == normalize(publisher) or vendor.casefold() in ('microsoft', 'mozilla', 'google'):
                    for product in registry_children(hive, path, view, errors):
                        key = path + '\\' + product
                        if matches(product, names) and registry_allowed(key):
                            keys.append(dict(hive=hive, key=key, view=view, reason='Produit sous la clé éditeur'))
    return keys


def discover_services(install_paths, errors):
    services = []
    parent = r'SYSTEM\CurrentControlSet\Services'
    for name in registry_children('HKEY_LOCAL_MACHINE', parent, 64, errors):
        values = registry_values('HKEY_LOCAL_MACHINE', parent+'\\'+name, 64, errors, False)
        info = {v['name']: v['value'] for v in values}
        paths = [executable_path(info.get('ImagePath', ''))]
        if paths[0] and paths[0].name.lower() == 'svchost.exe':
            parameters = registry_values('HKEY_LOCAL_MACHINE', parent+'\\'+name+r'\Parameters', 64, errors, False)
            paths.extend(executable_path(v['value']) for v in parameters if v['name'] == 'ServiceDll')
        if any(p and any(p.is_relative_to(Path(root)) for root in install_paths) for p in paths):
            services.append(dict(name=name, display_name=info.get('DisplayName', name), image=info.get('ImagePath', ''),
                                 start=info.get('Start'), account=info.get('ObjectName', ''),
                                 type=info.get('Type'), restoration='installer',
                                 dependencies=info.get('DependOnService', [])))
    return services


def msi_info(path):
    # Only read database metadata; never invoke Win32_Product (which can repair apps).
    literal = str(path).replace("'", "''")
    script = """$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8;
    $w=New-Object -ComObject WindowsInstaller.Installer
    $db=$w.OpenDatabase('%s',0)
    $r=@{}
    foreach($n in @('ProductCode','ProductName','ProductVersion','Manufacturer')) {
      $v=$db.OpenView("SELECT `Value` FROM `Property` WHERE `Property`='$n'")
      $v.Execute(); $row=$v.Fetch(); if($row){$r[$n]=$row.StringData(1)}; $v.Close()
    }
    $v=$db.OpenView('SELECT `Cabinet` FROM `Media`'); $v.Execute(); $c=@()
    while($row=$v.Fetch()){ $c += $row.StringData(1) }; $v.Close()
    $r['cabinets']=$c; $r | ConvertTo-Json -Compress -Depth 5
    """ % literal
    return powershell(script)


def find_installer(program, errors):
    source = Path(os.path.expandvars(program.get('install_source') or '.'))
    if program.get('install_source') and source.is_dir() and safe_source(source):
        try:
            candidates = list(source.glob('*.msi'))[:20]
        except OSError as exc:
            errors.append(f'Installeur non accessible : {exc}')
            candidates = []
        for path in candidates:
            try:
                if not safe_source(path):
                    continue
                info = msi_info(path)
                product_match = bool(info.get('ProductCode')) and info['ProductCode'].casefold() == program.get('product_key', '').casefold()
                name_match = normalize(info.get('ProductName', '')) in aliases(program)
                publisher_match = normalize(info.get('Manufacturer', '')) == normalize(program.get('publisher', ''))
                if not (product_match or name_match and publisher_match):
                    continue
                if program.get('version') and info.get('ProductVersion') != program['version']:
                    continue
                media = [str(path)]
                for cabinet in info.get('cabinets', []):
                    if cabinet and not cabinet.startswith('#'):
                        cab = sb.child(source, cabinet)
                        if not cab.is_file():
                            raise ValueError(f'Média MSI absent : {cab.name}')
                        media.append(str(cab))
                return dict(method='msi', source=str(path), media=media, product_code=info.get('ProductCode', ''),
                            version=info.get('ProductVersion', ''), label='Installeur MSI d’origine et médias détectés')
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                errors.append(f'Installeur non validé : {path.name} ({exc})')
    if shutil.which('winget'):
        try:
            result = run(['winget', 'list', '--name', program['name'], '--exact', '--source', 'winget',
                          '--accept-source-agreements', '--disable-interactivity'], 40)
            pattern = r'^\s*' + re.escape(program['name']) + r'\s{2,}([A-Za-z0-9][A-Za-z0-9.+_-]*\.[A-Za-z0-9.+_-]+)\s{2,}(\S+)'
            rows = re.findall(pattern, result.stdout, re.M)
            if result.returncode == 0 and len(set(rows)) == 1:
                package, version = rows[0]
                return dict(method='winget', id=package, version=version, label='Réinstallation Winget — Internet requis')
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f'Identification Winget indisponible : {exc}')
    return dict(method='manual', label='Installeur compatible non identifié — fichiers et données restent sauvegardés',
                install_source=program.get('install_source', ''), website=program.get('website', ''))


def supplied_installer(program, path):
    """Prepare installation media explicitly selected by the user, without running it."""
    path = Path(path).resolve()
    if not path.is_file() or not safe_source(path) or path.suffix.lower() not in ('.msi', '.exe'):
        raise ValueError('Choisissez un installeur MSI ou EXE du logiciel.')
    if path.suffix.lower() == '.exe':
        return dict(method='exe', source=str(path), media=[str(path)],
                    label='Installeur EXE inclus — assistant à terminer avant la restauration des paramètres')
    info = msi_info(path)
    product_match = bool(info.get('ProductCode')) and info['ProductCode'].casefold() == program.get('product_key', '').casefold()
    name_match = normalize(info.get('ProductName', '')) in aliases(program)
    publisher_match = bool(program.get('publisher')) and normalize(info.get('Manufacturer', '')) == normalize(program['publisher'])
    if not (product_match or name_match and publisher_match):
        raise ValueError('Cet installeur MSI ne correspond pas au logiciel sélectionné.')
    if program.get('version') and info.get('ProductVersion') != program['version']:
        raise ValueError('Choisissez un installeur de la même version que le logiciel sauvegardé.')
    media = [str(path)]
    for cabinet in info.get('cabinets', []):
        if cabinet and not cabinet.startswith('#'):
            cab = sb.child(path.parent, cabinet)
            if not cab.is_file() or cab.suffix.lower() != '.cab':
                raise ValueError('Média MSI absent ou incompatible : ' + cabinet)
            media.append(str(cab))
    return dict(method='msi', source=str(path), media=media, product_code=info['ProductCode'],
                version=info.get('ProductVersion', ''), label='Installeur MSI inclus — installation automatique puis paramètres')


def copy_installer(installer, folder):
    result = dict(installer)
    if result['method'] not in ('msi', 'exe'):
        return result
    result['payloads'] = []
    # Use a fresh directory so replacing an installer cannot damage existing media.
    prefix = 'installer/' + uuid4().hex[:8]
    for value in installer['media']:
        source = Path(value)
        relative = source.relative_to(Path(installer['source']).parent)
        payload = prefix + '/' + relative.as_posix()
        target = sb.child(folder, payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        checksum = sb.digest(target)
        if checksum != sb.digest(source):
            raise OSError('Installeur modifié pendant la copie : ' + source.name)
        result['payloads'].append(dict(payload=payload, sha256=checksum))
    return result


def attach_installer(folder, path):
    folder = Path(folder)
    data = check_manifest(folder)
    installer = supplied_installer(data['program'], path)
    data['installer'] = copy_installer(installer, folder)
    write_json(folder/'manifest.json', data)
    return dict(message='Installeur ajouté à la sauvegarde.', folder=str(folder))


def discover(program, log=lambda message: None):
    errors, files = [], []
    names = aliases(program)
    log(f"Analyse automatique de {program['name']} : dossiers et traces d’installation…")

    def add(path, reason, kind='data'):
        path = Path(path).absolute()
        if not path.is_dir() or not safe_source(path):
            return
        base, rel = encode_path(path)
        if base == 'home' and normalize(path.name) in {'documents', 'desktop', 'downloads', 'pictures', 'appdata'}:
            return
        # Collapse overlapping roots and avoid copying the same subtree twice.
        if any(path.is_relative_to(Path(item['source'])) for item in files):
            return
        files[:] = [item for item in files if not Path(item['source']).is_relative_to(path)]
        files.append(dict(base=base, relative=rel, source=str(path), kind=kind, reason=reason,
                          automatic=base != 'external'))

    location = program.get('install_location', '').strip().strip('"')
    if location:
        add(os.path.expandvars(location), 'Dossier déclaré par Windows', 'program')
    for field in ('display_icon', 'uninstall'):
        exe = executable_path(program.get(field, ''))
        if exe and safe_source(exe.parent):
            add(exe.parent, 'Chemin de ' + ('l’icône' if field == 'display_icon' else 'désinstallation'), 'program')
    for item in files:
        leaf = normalize(Path(item['source']).name)
        if len(leaf) >= 3 and leaf not in GENERIC:
            names.add(leaf)
    for base, root in all_roots().items():
        for parent in directories(root, errors):
            if matches(parent.name, names):
                add(parent, 'Nom du produit sans suffixe de version / architecture', 'program' if base.startswith('programfiles') else 'data')
            # Two levels locate Vendor/Product; never copy all of a publisher's data.
            if base != 'home' or parent.name.casefold() in ('documents', 'saved games', 'games') or normalize(parent.name) == normalize(program.get('publisher', '')):
                for nested in directories(parent, errors):
                    if matches(nested.name, names):
                        add(nested, 'Dossier produit sous ' + parent.name, 'program' if base.startswith('programfiles') or parent.name.casefold() == 'programs' else 'data')
    recipe = next((r for r in sb.RECIPES.values() if re.search(r[0], program['name'], re.I)), None)
    if recipe:
        for base, rel in recipe[1]:
            add(sb.child(all_roots()[base], rel), 'Emplacement complémentaire connu')
    log('Recherche des préférences dans le registre utilisateur et machine…')
    registry = discover_registry(names, program.get('publisher', ''), errors)
    if recipe:
        for key in recipe[2]:
            candidate = dict(hive='HKEY_CURRENT_USER', key=key, view=64, reason='Emplacement complémentaire connu')
            if candidate not in registry:
                registry.append(candidate)
    # Application-owned registry keys may point at nonstandard data directories.
    for entry in registry:
        for value in registry_values(entry['hive'], entry['key'], entry['view'], errors):
            if value['type'] in (1, 2) and isinstance(value['value'], str):
                text = os.path.expandvars(value['value']).strip('"')
                try:
                    path = Path(text)
                    if path.is_absolute() and path.is_dir():
                        add(path, 'Dossier référencé par ' + entry['key'])
                except (OSError, ValueError):
                    pass
    log('Identification des services associés et de la méthode de réinstallation…')
    services = discover_services([f['source'] for f in files if f['kind'] == 'program'], errors)
    installer = find_installer(program, errors)
    if not files and not registry:
        errors.append('Aucun dossier ou réglage attribuable avec suffisamment de certitude. Seul l’inventaire sera sauvegardé.')
    return dict(engine=2, program=program, files=files, registry=registry, services=services,
                installer=installer, notes=NOTES, errors=list(dict.fromkeys(errors)),
                confidence='Analyse automatique par traces Windows — couverture non exhaustive')


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def walk_files(root, errors):
    def onerror(exc):
        errors.append(str(exc))
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        for name in list(dirs):
            if linked(Path(directory)/name):
                dirs.remove(name)
                errors.append(f'Jonction non copiée : {Path(directory)/name}')
        for name in names:
            path = Path(directory)/name
            if linked(path):
                errors.append(f'Lien non copié : {path}')
            else:
                yield path


def save_snapshot(plan, destination, log, progress=None):
    folder = Path(destination)/'Logiciels'/sb.snapshot_folder_name(plan['program'])
    for item in plan['files']:
        src = Path(item['source'])
        if not safe_source(src) or folder.resolve().is_relative_to(src.resolve()):
            raise ValueError('Destination imbriquée ou source trop large : ' + str(src))
    folder.mkdir(parents=True)
    data = dict(plan, schema=2, created=datetime.now().isoformat(), status='incomplete', payload=[], registry_values=[],
                errors=list(plan.get('errors', [])), original_roots={k: str(v) for k, v in all_roots().items()})
    data['installer'] = dict(plan['installer'])
    write_json(folder/'manifest.json', data)
    jobs = []
    log('Inventaire des fichiers à copier…')
    for index, item in enumerate(plan['files']):
        source = Path(item['source'])
        if not source.exists():
            data['errors'].append('Dossier disparu depuis l’analyse : ' + str(source))
            continue
        jobs.extend((index, path, str(path.relative_to(source))) for path in walk_files(source, data['errors']))
    total_bytes = sum(p.stat().st_size for _, p, _ in jobs if p.exists())
    if shutil.disk_usage(folder).free < total_bytes:
        data['errors'].append('Espace libre insuffisant pour la sauvegarde.')
        write_json(folder/'manifest.json', data)
        raise ValueError('Espace libre insuffisant sur le support choisi.')
    for n, (index, source, rel) in enumerate(jobs):
        try:
            source = sb.child(Path(plan['files'][index]['source']), rel)
            payload = f'data/{index}/{rel}'
            target = sb.child(folder, payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat()
            shutil.copy2(source, target)
            after = source.stat()
            checksum = sb.digest(target)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sb.digest(source) != checksum:
                raise OSError('Fichier modifié pendant la copie : ' + str(source))
            data['payload'].append(dict(root=index, relative=rel, payload=payload, sha256=checksum, size=after.st_size))
        except (OSError, ValueError) as exc:
            data['errors'].append(f'{source} : {exc}')
        if progress:
            progress(plan['program']['name'], n+1, len(jobs))
    for key in plan['registry']:
        data['registry_values'].extend(registry_values(key['hive'], key['key'], key['view'], data['errors']))
    try:
        data['installer'] = copy_installer(data['installer'], folder)
    except (OSError, ValueError) as exc:
        data['errors'].append('Copie installeur : ' + str(exc))
        data['installer'] = dict(method='manual', label='Installeur non copié — ajoutez un installeur avant la restauration sur un PC neuf')
    data['status'] = 'partial' if data['errors'] else 'complete'
    write_json(folder/'manifest.json', data)
    report = [plan['program']['name'], f"{len(data['payload'])} fichiers, {len(data['registry_values'])} valeurs de registre.",
              'Réinstallation : '+data['installer']['label'], *NOTES, '', 'Points à vérifier :', *data['errors']]
    (folder/'Rapport.txt').write_text('\n'.join(report), encoding='utf-8')
    log(f"Sauvegarde {data['status']} : {folder} — {len(data['payload'])} fichiers.")
    return dict(partial=bool(data['errors']), report=str(folder/'Rapport.txt'), folder=str(folder), message=f"{len(data['errors'])} point(s) à vérifier.")


def matching_installed(program):
    for item in sb.installed():
        same_key = program.get('product_key') and item.get('product_key') == program['product_key']
        same_name = normalize(item['name']) == normalize(program['name'])
        same_publisher = normalize(item.get('publisher', '')) == normalize(program.get('publisher', ''))
        if same_key or same_name and same_publisher:
            return item
    return None


def check_manifest(folder):
    """Validate all destinations and payload integrity before executing an installer."""
    data = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if data.get('schema') != 2 or data.get('status') == 'incomplete':
        raise ValueError('Sauvegarde incompatible ou interrompue avant finalisation.')
    if not isinstance(data.get('program', {}).get('name'), str):
        raise ValueError('Identité du programme absente')
    for item in data['files']:
        if item['base'] == 'external':
            if not Path(item['relative']).is_absolute():
                raise ValueError('Chemin externe invalide')
        elif item['base'] not in all_roots():
            raise ValueError('Racine de restauration inconnue')
        else:
            path = sb.child(all_roots()[item['base']], item['relative'])
            if not safe_source(path):
                raise ValueError('Destination de restauration interdite')
            parts = Path(item['relative']).parts
            if item['base'] == 'home' and parts[0].casefold() in ('appdata', 'windows', 'start menu'):
                raise ValueError('Destination utilisateur système interdite')
            if any(p.casefold() in ('startup', 'start menu', 'windowsapps', 'windowspowershell', 'powershell', 'common files') for p in parts):
                raise ValueError('Dossier de composants partagés interdit')
    seen = set()
    for entry in data['payload']:
        index = entry['root']
        if type(index) is not int or index < 0 or index >= len(data['files']):
            raise ValueError('Racine de fichier invalide')
        # External paths are not auto-applied, but file names must still be valid.
        sb.child(folder/'validation', entry['relative'])
        source = sb.child(folder, entry['payload'])
        if not Path(entry['payload']).parts or Path(entry['payload']).parts[0] != 'data':
            raise ValueError('Charge utile hors des données')
        identity = (index, entry['relative'].casefold())
        if identity in seen:
            raise ValueError('Fichier en double dans la sauvegarde')
        seen.add(identity)
        if not source.is_file() or sb.digest(source) != entry['sha256']:
            raise ValueError('Fichier absent ou altéré : ' + entry['payload'])
    for entry in data['registry_values']:
        if entry['hive'] not in ('HKEY_CURRENT_USER', 'HKEY_LOCAL_MACHINE') or entry['view'] not in (32, 64) or not registry_allowed(entry['key']):
            raise ValueError('Clé de registre système ou non autorisée')
        if not any(entry['hive'] == k['hive'] and entry['view'] == k['view'] and
                   (entry['key'] == k['key'] or entry['key'].startswith(k['key']+'\\')) for k in data['registry']):
            raise ValueError('Valeur hors du périmètre déclaré')
        decode_value(entry)
    installer = data['installer']
    if installer['method'] not in ('manual', 'msi', 'winget', 'exe'):
        raise ValueError('Méthode de réinstallation inconnue')
    if installer['method'] in ('msi', 'exe'):
        if not installer.get('payloads'):
            raise ValueError('Installeur absent')
        extension = '.' + installer['method']
        if sum(Path(e['payload']).suffix.lower() == extension for e in installer['payloads']) != 1:
            raise ValueError('Un seul installeur principal doit être présent')
        allowed = ('.msi', '.cab') if installer['method'] == 'msi' else ('.exe',)
        for entry in installer['payloads']:
            path = sb.child(folder, entry['payload'])
            if Path(entry['payload']).parts[0] != 'installer' or path.suffix.lower() not in allowed or sb.digest(path) != entry['sha256']:
                raise ValueError('Installeur absent ou altéré')
    if installer['method'] == 'winget':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{1,180}', installer.get('id', '')):
            raise ValueError('Identifiant Winget invalide')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,100}', installer.get('version', '')):
            raise ValueError('Version Winget invalide')
    return data


def decode_value(entry):
    value, kind = entry['value'], entry['type']
    if kind in (0, 3):
        return bytes.fromhex(value)
    if kind in (1, 2) and isinstance(value, str):
        return value
    if kind in (4, 11) and type(value) is int and 0 <= value < 2**(32 if kind == 4 else 64):
        return value
    if kind == 7 and isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    raise ValueError('Type de valeur de registre invalide')


def reinstall(data, folder, log, issues):
    present = matching_installed(data['program'])
    if present:
        log('Logiciel déjà installé : ' + present['name'])
        return present
    installer = data['installer']
    if installer['method'] == 'manual':
        raise RuntimeError('Logiciel absent : ajoutez son installeur MSI ou EXE à cette sauvegarde, puis relancez « Installer et restaurer ». Aucun paramètre n’a été restauré.')
    try:
        if installer['method'] == 'winget':
            if not shutil.which('winget'):
                raise RuntimeError('Winget est absent sur ce PC. Installez App Installer de Microsoft ou ajoutez un installeur MSI/EXE à la sauvegarde, puis relancez la restauration.')
            args = ['winget', 'install', '--id', installer['id'], '--exact', '--version', installer['version'],
                    '--source', 'winget', '--silent', '--accept-source-agreements', '--accept-package-agreements',
                    '--disable-interactivity']
        elif installer['method'] == 'msi':
            entry = next(e for e in installer['payloads'] if e['payload'].lower().endswith('.msi'))
            path = sb.child(folder, entry['payload'])
            info = msi_info(path)
            if info.get('ProductCode', '').casefold() != installer.get('product_code', '').casefold():
                raise ValueError('Identité de l’installeur MSI différente de la sauvegarde')
            args = ['msiexec.exe', '/i', str(path), '/qn', '/norestart', '/L*v', str(folder/'installation.log')]
        else:
            path = sb.child(folder, installer['payloads'][0]['payload'])
            args = [str(path)]
            log('Terminez l’assistant d’installation, sans lancer le logiciel à la fin. Ses paramètres seront restaurés après vérification de l’installation.')
        log('Réinstallation automatique du logiciel… Une élévation Windows peut être nécessaire.')
        result = run(args, 1200)
        # Preserve installer diagnostics without exposing settings to the UI.
        (folder/'installation-sortie.txt').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        if result.returncode in (3010, 1641):
            raise RuntimeError('Installation effectuée, mais un redémarrage est nécessaire. Redémarrez Windows puis relancez « Installer et restaurer » pour appliquer les paramètres.')
        if result.returncode != 0:
            raise RuntimeError(f'Installation échouée ou annulée (code {result.returncode}). Aucun paramètre restauré. Consultez installation-sortie.txt. Si des droits sont requis, relancez Sauvegarde PC en administrateur.')
        present = matching_installed(data['program'])
        if not present:
            raise RuntimeError('Installation non confirmée par Windows. Terminez l’assistant, fermez le logiciel puis relancez la restauration. Aucun paramètre restauré.')
        log('Installation vérifiée. Restauration des paramètres…')
        return present
    except (OSError, ValueError, StopIteration, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Installation non terminée : {exc}. Aucun paramètre restauré. Vérifiez l’installeur et les droits administrateur avant de réessayer.') from exc


def remap_registry(value, data, present):
    """Only substitute anchored path prefixes, never substrings in arbitrary settings."""
    mappings = [(old, str(all_roots()[base])) for base, old in data.get('original_roots', {}).items() if base in all_roots()]
    if present and present.get('install_location') and data['program'].get('install_location'):
        mappings.append((data['program']['install_location'], present['install_location']))
    def remap(text):
        for old, new in sorted(mappings, key=lambda p: len(p[0]), reverse=True):
            if text.casefold() == old.casefold() or text.casefold().startswith(old.rstrip('\\/').casefold()+'\\'):
                return new.rstrip('\\/') + text[len(old.rstrip('\\/')):]
        return text
    return remap(value) if isinstance(value, str) else [remap(v) for v in value] if isinstance(value, list) else value


def restore_snapshot(folder, log, progress=None):
    log('Vérification complète de la sauvegarde avant réinstallation…')
    data = check_manifest(folder)
    report = dict(created=datetime.now().isoformat(), program=data['program'], issues=[], restored_files=0, restored_registry=0, file_recovery=[], registry_recovery=[])
    issues = report['issues']
    recovery = folder/('Avant-restauration-'+uuid4().hex[:8])
    recovery.mkdir()
    report_path = recovery/'rapport-restauration.json'
    write_json(report_path, report)
    if data.get('errors'):
        issues.append(f"Sauvegarde d’origine partielle : {len(data['errors'])} avertissement(s), voir Rapport.txt.")
    try:
        present = reinstall(data, folder, log, issues)
    except RuntimeError as exc:
        report['issues'].append(str(exc))
        report['stage'] = 'installation_required'
        write_json(report_path, report)
        (recovery/'Rapport.txt').write_text(str(exc), encoding='utf-8')
        raise
    version_mismatch = present and present.get('version') != data['program'].get('version')
    if version_mismatch:
        issues.append('Version installée différente : les binaires ne seront pas remplacés. Compatibilité des préférences à vérifier.')
    for n, entry in enumerate(data['payload']):
        item = data['files'][entry['root']]
        try:
            if item['base'] == 'external':
                if not any('Emplacements externes' in i for i in issues):
                    issues.append('Emplacements externes conservés dans la sauvegarde : restauration manuelle nécessaire vers les disques appropriés.')
                continue
            if version_mismatch and item['kind'] == 'program':
                continue
            base = sb.child(all_roots()[item['base']], item['relative'])
            old_install = data['program'].get('install_location', '').strip().strip('"')
            if item['kind'] == 'program' and present and present.get('install_location') and old_install and Path(item['source']) == Path(old_install):
                base = Path(present['install_location'])
                if not safe_source(base):
                    raise ValueError('Nouveau dossier d’installation non sûr')
            target = sb.child(base, entry['relative'])
            if target.resolve().is_relative_to(folder.resolve()) or folder.resolve().is_relative_to(target.resolve()):
                raise ValueError('Destination confondue avec la sauvegarde')
            source = sb.child(folder, entry['payload'])
            saved = None
            if target.exists():
                saved = f'files/{n}'
                prior = sb.child(recovery, saved)
                prior.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, prior)
            report['file_recovery'].append(dict(target=str(target), previous=saved))
            write_json(report_path, report)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Stage beside the target so a locked file is not truncated on failure.
            temporary = target.with_name(target.name+'.restore-'+uuid4().hex[:8])
            try:
                shutil.copy2(source, temporary)
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            report['restored_files'] += 1
        except (OSError, ValueError) as exc:
            issues.append(f"Fichier non restauré ({entry['relative']}) : {exc}")
        finally:
            if progress:
                progress(data['program']['name'], n+1, len(data['payload']))
    wr = sb.winreg
    for entry in data['registry_values']:
        try:
            value = remap_registry(decode_value(entry), data, present)
            hive, view = getattr(wr, entry['hive']), view_flag(entry['view'])
            previous, existed = None, False
            try:
                with wr.OpenKey(hive, entry['key'], 0, wr.KEY_READ | view) as handle:
                    prior, kind = wr.QueryValueEx(handle, entry['name'])
                    previous = dict(type=kind, value=prior.hex() if isinstance(prior, bytes) else prior)
                    existed = True
            except FileNotFoundError:
                pass
            report['registry_recovery'].append(dict(entry=entry, existed=existed, previous=previous))
            write_json(report_path, report)
            with wr.CreateKeyEx(hive, entry['key'], 0, wr.KEY_SET_VALUE | view) as handle:
                wr.SetValueEx(handle, entry['name'], 0, entry['type'], value)
            report['restored_registry'] += 1
        except (OSError, ValueError, AttributeError) as exc:
            issues.append(f"Registre non restauré ({entry['key']}) : {exc}")
    for service in data.get('services', []):
        key = 'SYSTEM\\CurrentControlSet\\Services\\' + service['name']
        if not registry_values('HKEY_LOCAL_MACHINE', key, 64, [], False):
            issues.append('Service / pilote à recréer via l’installeur : ' + service['name'])
        elif service.get('account', '').casefold() not in ('', 'localsystem', 'nt authority\\localservice', 'nt authority\\networkservice'):
            issues.append('Compte de service à vérifier : ' + service['name'])
    report['notes'] = ['Activation, connexion et secrets protégés par l’ancien Windows : vérification utilisateur nécessaire.']
    report['issues'] = list(dict.fromkeys(issues))
    write_json(report_path, report)
    (recovery/'Rapport.txt').write_text('\n'.join([f"{report['restored_files']} fichiers et {report['restored_registry']} valeurs restaurés.", *report['issues'], *report['notes']]), encoding='utf-8')
    log(f"Restauration : {report['restored_files']} fichiers, {report['restored_registry']} valeurs. Rapport : {recovery/'Rapport.txt'}")
    return dict(partial=bool(report['issues']), report=str(recovery/'Rapport.txt'), message=f"{len(report['issues'])} point(s) à vérifier.")
