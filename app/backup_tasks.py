"""
Fonctions de sauvegarde pour l'application "Sauvegarde PC".

Chaque fonction prend un callback `log(str)` pour remonter sa progression
vers l'interface graphique, et renvoie un court statut texte.
Toutes les fonctions sont conçues pour être appelées depuis un thread
d'arrière-plan (elles ne touchent jamais l'UI directement).
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

USERPROFILE = Path(os.environ.get("USERPROFILE", str(Path.home())))
APPDATA = Path(os.environ.get("APPDATA", USERPROFILE / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", USERPROFILE / "AppData" / "Local"))

CONFIG_DIR = APPDATA / "SauvegardePC"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Dossiers/fichiers à ne jamais copier (cache, volumineux et régénérés seuls)
IGNORE_PATTERNS = shutil.ignore_patterns(
    "Cache", "Cache2", "Code Cache", "GPUCache", "Service Worker",
    "*.tmp", "*.log", "ShaderCache", "GrShaderCache", "component_crx_cache",
)


# --------------------------------------------------------------------------- #
# Utilitaires généraux
# --------------------------------------------------------------------------- #

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Relance le script actuel avec élévation UAC, puis termine immédiatement le process courant."""
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    # os._exit (et non sys.exit) : termine le process sans dépendre du bon déroulement de la
    # boucle d'événements Tkinter, pour ne jamais laisser deux instances tourner en même temps.
    os._exit(0)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_command(args, log) -> tuple[bool, str]:
    """Exécute une commande et journalise stdout/stderr. Renvoie (succès, sortie)."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if output.strip():
            log(output.strip())
        return result.returncode == 0, output
    except FileNotFoundError:
        msg = f"Commande introuvable : {args[0]}"
        log(f"⚠ {msg}")
        return False, msg
    except Exception as exc:
        log(f"⚠ Erreur lors de l'exécution de {args[0]} : {exc}")
        return False, str(exc)


def _is_within(path: Path, potential_parent: Path) -> bool:
    """True si `path` est égal ou contenu dans `potential_parent`."""
    try:
        path.relative_to(potential_parent)
        return True
    except ValueError:
        return False


def _winlong(path) -> str:
    """
    Préfixe \\\\?\\ pour contourner la limite Windows de 260 caractères par chemin
    (fréquent avec des dossiers de pages web enregistrées, ex. « ..._fichiers\\embed_fichiers »).
    """
    s = str(path)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _mkdir_long(path: Path) -> None:
    os.makedirs(_winlong(path), exist_ok=True)


def _count_files(src: Path, ignore=None) -> int:
    """Compte rapidement le nombre de fichiers à copier (pour la barre de progression)."""
    total = 0
    for root, dirs, files in os.walk(src):
        if ignore:
            excluded = ignore(root, dirs + files)
            dirs[:] = [d for d in dirs if d not in excluded]
            files = [f for f in files if f not in excluded]
        total += len(files)
    return total


def copy_tree_safe(src: Path, dst: Path, log, ignore=None, progress=None) -> int:
    """
    Copie récursive tolérante aux erreurs : un fichier verrouillé ou
    inaccessible (ex. navigateur ouvert, antivirus) ne bloque pas le reste.
    `progress`, si fourni, est appelé progress(copiés, total) après chaque fichier.
    Renvoie le nombre de fichiers copiés.
    """
    if not src.exists():
        log(f"— Ignoré (introuvable) : {src}")
        return 0

    src_resolved = src.resolve()
    dst_resolved = dst.resolve()
    if dst_resolved == src_resolved or _is_within(dst_resolved, src_resolved):
        log(
            f"⚠ Copie annulée : la destination ({dst}) est identique ou se trouve à l'intérieur du "
            f"dossier source ({src}). Cela créerait une copie infinie imbriquée dans elle-même — "
            "choisissez un dossier de destination extérieur au(x) dossier(s) sauvegardé(s)."
        )
        return 0

    total = _count_files(src, ignore) if progress else 0
    if progress:
        progress(0, total)

    copied = 0
    try:
        _mkdir_long(dst)
    except OSError as exc:
        log(f"⚠ Impossible de créer le dossier de destination {dst} : {exc}")
        return 0

    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_root = dst / rel

        if ignore:
            excluded = ignore(root, dirs + files)
            dirs[:] = [d for d in dirs if d not in excluded]
            files = [f for f in files if f not in excluded]

        try:
            _mkdir_long(target_root)
        except OSError as exc:
            log(f"  ⚠ Dossier ignoré (chemin trop long ou invalide) : {target_root} ({exc.__class__.__name__})")
            dirs[:] = []  # inutile de descendre plus profond, ce sera pire
            continue

        for name in files:
            src_file = Path(root) / name
            dst_file = target_root / name
            try:
                shutil.copy2(_winlong(src_file), _winlong(dst_file))
                copied += 1
            except (PermissionError, OSError) as exc:
                log(
                    f"  ⚠ Fichier ignoré (verrouillé/inaccessible/chemin trop long) : "
                    f"{src_file.name} ({exc.__class__.__name__})"
                )
            finally:
                if progress:
                    progress(copied, total)
    return copied


# --------------------------------------------------------------------------- #
# 1. Fichiers personnels (dossiers connus Windows, via GUID pour gérer OneDrive)
# --------------------------------------------------------------------------- #

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_FOLDERID = {
    "Bureau": "b4bfcc3a-db2c-424c-b029-7fe99a87c641",
    "Documents": "fdd39ad0-238f-46af-adb4-6c85480369c7",
    "Images": "33e28130-4e1e-4676-835a-98395c3bc3bb",
    "Téléchargements": "374de290-123f-4565-9164-39c4925e467b",
    "Vidéos": "18989b1d-99b5-455b-841c-ab7c74e4ddfc",
    "Musique": "4bd8d571-6d19-48d3-be97-422220080e43",
}


def _guid_from_str(guid_str: str) -> _GUID:
    guid_str = "{" + guid_str + "}"
    guid = _GUID()
    ctypes.windll.ole32.CLSIDFromString(guid_str, ctypes.byref(guid))
    return guid


def get_known_folder(name: str) -> Path:
    """Récupère le vrai chemin d'un dossier connu (gère la redirection OneDrive)."""
    fallback = USERPROFILE / name.replace("é", "e").replace("è", "e")
    fallback_map = {
        "Bureau": USERPROFILE / "Desktop",
        "Documents": USERPROFILE / "Documents",
        "Images": USERPROFILE / "Pictures",
        "Téléchargements": USERPROFILE / "Downloads",
        "Vidéos": USERPROFILE / "Videos",
        "Musique": USERPROFILE / "Music",
    }
    guid_str = _FOLDERID.get(name)
    if not guid_str:
        return fallback_map.get(name, fallback)
    try:
        rfid = _guid_from_str(guid_str)
        path_ptr = ctypes.c_wchar_p()
        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        result = SHGetKnownFolderPath(ctypes.byref(rfid), 0, None, ctypes.byref(path_ptr))
        if result == 0 and path_ptr.value:
            return Path(path_ptr.value)
    except Exception:
        pass
    return fallback_map.get(name, fallback)


STANDARD_FOLDERS = ["Bureau", "Documents", "Images", "Téléchargements", "Vidéos", "Musique"]


def backup_standard_folders(dest: Path, selected: list[str], log, progress=None) -> None:
    target_base = ensure_dir(dest / "Fichiers personnels")
    for name in selected:
        try:
            folder = get_known_folder(name)
            log(f"→ Copie de « {name} » depuis {folder} ...")
            cb = (lambda done, total, name=name: progress(name, done, total)) if progress else None
            n = copy_tree_safe(folder, target_base / name, log, progress=cb)
            log(f"  ✓ {n} fichier(s) copié(s) pour « {name} ».")
        except Exception as exc:
            log(f"  ⚠ Erreur inattendue sur « {name} », dossier ignoré, suite de la sauvegarde : {exc}")


def backup_saved_games(dest: Path, log, progress=None) -> None:
    src = USERPROFILE / "Saved Games"
    if not src.exists():
        log("— Aucun dossier « Saved Games » trouvé.")
        return
    log("→ Copie du dossier « Saved Games » (sauvegardes de jeux) ...")
    cb = (lambda done, total: progress("Saved Games", done, total)) if progress else None
    n = copy_tree_safe(src, dest / "Fichiers personnels" / "Saved Games", log, progress=cb)
    log(f"  ✓ {n} fichier(s) copié(s).")


def backup_custom_folders(dest: Path, folders: list[str], log, progress=None) -> None:
    target_base = ensure_dir(dest / "Divers (dossiers personnalisés)")
    for folder_str in folders:
        try:
            src = Path(folder_str)
            if not src.exists():
                log(f"— Dossier personnalisé introuvable, ignoré : {src}")
                continue
            log(f"→ Copie du dossier personnalisé : {src} ...")
            cb = (lambda done, total, n=src.name: progress(n, done, total)) if progress else None
            n = copy_tree_safe(src, target_base / src.name, log, progress=cb)
            log(f"  ✓ {n} fichier(s) copié(s) pour « {src.name} ».")
        except Exception as exc:
            log(f"  ⚠ Erreur inattendue sur « {folder_str} », dossier ignoré, suite de la sauvegarde : {exc}")


# --------------------------------------------------------------------------- #
# 2. Navigateurs
# --------------------------------------------------------------------------- #

BROWSER_PROFILE_PATHS = {
    "Google Chrome": LOCALAPPDATA / "Google" / "Chrome" / "User Data",
    "Microsoft Edge": LOCALAPPDATA / "Microsoft" / "Edge" / "User Data",
}


def detect_browsers() -> dict[str, bool]:
    detected = {name: path.exists() for name, path in BROWSER_PROFILE_PATHS.items()}
    detected["Mozilla Firefox"] = (APPDATA / "Mozilla" / "Firefox" / "Profiles").exists()
    return detected


def backup_chromium_browser(browser_name: str, dest: Path, log, progress=None) -> None:
    src = BROWSER_PROFILE_PATHS.get(browser_name)
    if not src or not src.exists():
        log(f"— {browser_name} : profil introuvable, ignoré.")
        return
    log(f"→ Copie du profil {browser_name} (favoris, mots de passe chiffrés, historique) ...")
    log("  ⚠ Fermez le navigateur avant cette opération pour éviter les fichiers verrouillés.")
    cb = (lambda done, total: progress(browser_name, done, total)) if progress else None
    n = copy_tree_safe(src, dest / "Navigateurs" / browser_name, log, ignore=IGNORE_PATTERNS, progress=cb)
    log(f"  ✓ {n} fichier(s) copié(s) pour {browser_name}.")
    log("  ℹ Les mots de passe restent chiffrés par Windows (DPAPI) : la restauration")
    log("    fiable passe surtout par la synchronisation de compte Google/Microsoft.")


def backup_firefox(dest: Path, log, progress=None) -> None:
    profiles_dir = APPDATA / "Mozilla" / "Firefox" / "Profiles"
    if not profiles_dir.exists():
        log("— Firefox : aucun profil trouvé, ignoré.")
        return
    log("→ Copie du/des profil(s) Firefox (favoris, mots de passe, historique) ...")
    log("  ⚠ Fermez Firefox avant cette opération pour éviter les fichiers verrouillés.")
    target = ensure_dir(dest / "Navigateurs" / "Mozilla Firefox")
    total = 0
    for profile in profiles_dir.iterdir():
        if profile.is_dir():
            cb = (lambda done, t, n=profile.name: progress(f"Firefox ({n})", done, t)) if progress else None
            total += copy_tree_safe(profile, target / profile.name, log, ignore=IGNORE_PATTERNS, progress=cb)
    log(f"  ✓ {total} fichier(s) copié(s) pour Firefox.")


def detect_browser_backups(backup_root: Path) -> dict[str, bool]:
    """Détecte, dans un dossier de sauvegarde choisi par l'utilisateur, quels navigateurs
    ont un profil disponible pour restauration."""
    nav_dir = backup_root / "Navigateurs"
    detected = {name: (nav_dir / name).exists() for name in BROWSER_PROFILE_PATHS}
    detected["Mozilla Firefox"] = (nav_dir / "Mozilla Firefox").exists()
    return detected


def restore_chromium_browser(browser_name: str, backup_root: Path, log, progress=None) -> None:
    dst = BROWSER_PROFILE_PATHS.get(browser_name)
    if not dst:
        log(f"⚠ Navigateur inconnu : {browser_name}")
        return
    src = backup_root / "Navigateurs" / browser_name
    if not src.exists():
        log(f"— {browser_name} : aucune sauvegarde trouvée dans {src}, ignoré.")
        return
    log(f"→ Restauration du profil {browser_name} vers {dst} ...")
    log("  ⚠ Fermez complètement le navigateur avant cette opération (y compris depuis la zone de notification).")
    cb = (lambda done, total: progress(browser_name, done, total)) if progress else None
    n = copy_tree_safe(src, dst, log, ignore=IGNORE_PATTERNS, progress=cb)
    log(f"  ✓ {n} fichier(s) restauré(s) pour {browser_name}.")
    log("  ℹ Favoris et historique sont restaurés. Les mots de passe enregistrés restent chiffrés par")
    log("    Windows (DPAPI) : ils ne se déchiffreront que sur la session Windows d'origine. Après une")
    log("    réinstallation de Windows, préférez la synchronisation de compte Google/Microsoft.")


def restore_firefox(backup_root: Path, log, progress=None) -> None:
    src_root = backup_root / "Navigateurs" / "Mozilla Firefox"
    if not src_root.exists():
        log("— Firefox : aucune sauvegarde trouvée, ignoré.")
        return
    log("→ Restauration du/des profil(s) Firefox ...")
    log("  ⚠ Fermez complètement Firefox avant cette opération.")
    target = ensure_dir(APPDATA / "Mozilla" / "Firefox" / "Profiles")
    total = 0
    for profile in src_root.iterdir():
        if profile.is_dir():
            cb = (lambda done, t, n=profile.name: progress(f"Firefox ({n})", done, t)) if progress else None
            total += copy_tree_safe(profile, target / profile.name, log, ignore=IGNORE_PATTERNS, progress=cb)
    log(f"  ✓ {total} fichier(s) restauré(s) pour Firefox.")
    log("  ℹ Si Firefox démarre sur un profil vide, ouvrez about:profiles pour rattacher le profil restauré.")


# --------------------------------------------------------------------------- #
# 3. Clés Windows & licences
# --------------------------------------------------------------------------- #

def get_windows_key(log) -> str:
    log("→ Lecture de la clé Windows OEM (nécessite les droits administrateur) ...")
    ok, output = run_command(
        [
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance -Query \"select * from SoftwareLicensingService\").OA3xOriginalProductKey",
        ],
        log,
    )
    key = output.strip()
    if ok and key:
        log(f"  ✓ Clé trouvée : {key}")
        return key
    log("  — Aucune clé OEM trouvée (normal pour certains PC : la licence est numérique, liée au compte Microsoft).")
    return ""


def load_licenses(dest: Path) -> list[dict]:
    lic_file = dest / "Licences" / "licences.json"
    if lic_file.exists():
        try:
            return json.loads(lic_file.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_licenses(dest: Path, licenses: list[dict], log) -> None:
    lic_dir = ensure_dir(dest / "Licences")
    lic_file = lic_dir / "licences.json"
    lic_file.write_text(json.dumps(licenses, indent=2, ensure_ascii=False), encoding="utf-8")

    txt_file = lic_dir / "licences.txt"
    lines = ["Clés et licences enregistrées", "=" * 32, ""]
    for item in licenses:
        lines.append(f"{item.get('logiciel', '?')} : {item.get('cle', '')}")
    txt_file.write_text("\n".join(lines), encoding="utf-8")
    log(f"  ✓ {len(licenses)} licence(s) enregistrée(s) dans {txt_file}")


# --------------------------------------------------------------------------- #
# 4. Programmes installés (winget)
# --------------------------------------------------------------------------- #

def export_installed_programs(dest: Path, log) -> None:
    target = ensure_dir(dest / "Programmes")
    json_path = target / "programmes.json"
    log("→ Export de la liste des programmes avec winget ...")
    ok, _ = run_command(["winget", "export", "-o", str(json_path)], log)
    if ok:
        log(f"  ✓ Liste exportée : {json_path}")
    else:
        log("  ⚠ winget export a échoué ou winget est absent.")

    log("→ Récupération de la liste complète (y compris logiciels non-winget) ...")
    ok2, output = run_command(["winget", "list"], log)
    if output.strip():
        (target / "programmes_liste_complete.txt").write_text(output, encoding="utf-8")
        log(f"  ✓ Liste complète enregistrée : {target / 'programmes_liste_complete.txt'}")
        log("  ℹ Les logiciels marqués « Installed package is not available from any source »")
        log("    ne seront PAS réinstallés automatiquement : notez-les pour une réinstallation manuelle.")


# --------------------------------------------------------------------------- #
# 5. Courrier électronique
# --------------------------------------------------------------------------- #

def backup_email(dest: Path, log, progress=None) -> None:
    target = ensure_dir(dest / "Email")

    outlook_dir = LOCALAPPDATA / "Microsoft" / "Outlook"
    if outlook_dir.exists():
        log("→ Recherche de fichiers Outlook (.pst / .ost) ...")
        found = list(outlook_dir.glob("*.pst")) + list(outlook_dir.glob("*.ost"))
        if found:
            out_target = ensure_dir(target / "Outlook")
            for f in found:
                try:
                    shutil.copy2(f, out_target / f.name)
                    log(f"  ✓ Copié : {f.name}")
                except (PermissionError, OSError) as exc:
                    log(f"  ⚠ Impossible de copier {f.name} (probablement ouvert dans Outlook) : {exc.__class__.__name__}")
        else:
            log("  — Aucun fichier .pst/.ost trouvé dans le dossier Outlook par défaut.")
    else:
        log("— Outlook : dossier de données non trouvé, ignoré.")

    tb_ini = APPDATA / "Thunderbird" / "profiles.ini"
    tb_profiles_dir = APPDATA / "Thunderbird"
    if tb_ini.exists():
        log("→ Copie du/des profil(s) Thunderbird ...")
        log("  ⚠ Fermez Thunderbird avant cette opération.")
        tb_target = ensure_dir(target / "Thunderbird")
        total = 0
        for item in tb_profiles_dir.glob("*.default*"):
            if item.is_dir():
                cb = (lambda done, t, n=item.name: progress(f"Thunderbird ({n})", done, t)) if progress else None
                total += copy_tree_safe(item, tb_target / item.name, log, ignore=IGNORE_PATTERNS, progress=cb)
        log(f"  ✓ {total} fichier(s) copié(s) pour Thunderbird.")
    else:
        log("— Thunderbird : non installé ou aucun profil trouvé, ignoré.")

    log("ℹ Pour un webmail (Gmail, Outlook.com...) : rien à sauvegarder, vos messages restent en ligne.")


# --------------------------------------------------------------------------- #
# 6. Profils Wi-Fi (nécessite les droits administrateur)
# --------------------------------------------------------------------------- #

def export_wifi_profiles(dest: Path, log) -> None:
    if not is_admin():
        log("⚠ Droits administrateur requis pour exporter les profils Wi-Fi. Relancez l'application en administrateur.")
        return
    target = ensure_dir(dest / "WiFi")
    log("→ Export des profils Wi-Fi enregistrés (avec mots de passe) ...")
    ok, _ = run_command(["netsh", "wlan", "export", "profile", "key=clear", f"folder={target}"], log)
    if ok:
        log(f"  ✓ Profils Wi-Fi exportés dans {target}")
    else:
        log("  ⚠ L'export des profils Wi-Fi a échoué.")


# --------------------------------------------------------------------------- #
# 7. Pilotes (nécessite les droits administrateur)
# --------------------------------------------------------------------------- #

def export_drivers(dest: Path, log) -> None:
    if not is_admin():
        log("⚠ Droits administrateur requis pour exporter les pilotes. Relancez l'application en administrateur.")
        return
    target = ensure_dir(dest / "Pilotes")
    log("→ Export des pilotes installés (peut prendre plusieurs minutes) ...")
    ok, _ = run_command(["dism", "/online", "/export-driver", f"/destination:{target}"], log)
    if ok:
        log(f"  ✓ Pilotes exportés dans {target}")
    else:
        log("  ⚠ L'export des pilotes a échoué.")


# --------------------------------------------------------------------------- #
# Configuration persistée (dossiers personnalisés, checklist, licences UI)
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "custom_folders": [],
    "checklist": {},
    "last_destination": "",
    "last_backup_at": "",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            merged = DEFAULT_CONFIG.copy()
            merged.update(data)
            return merged
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    ensure_dir(CONFIG_DIR)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Statistiques du dossier de sauvegarde (pour le tableau de bord)
# --------------------------------------------------------------------------- #

def compute_backup_stats(dest: Path) -> tuple[int, int]:
    """Parcourt le dossier de sauvegarde et renvoie (nombre de fichiers, taille
    totale en octets). Purement informatif (aucune écriture) ; peut être lent sur
    de gros volumes, à appeler depuis un thread d'arrière-plan."""
    if not dest.exists():
        return 0, 0
    count = 0
    total = 0
    for root, _dirs, files in os.walk(dest):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} To"


# --------------------------------------------------------------------------- #
# Rapport final
# --------------------------------------------------------------------------- #

def write_report(dest: Path, lines: list[str]) -> Path:
    report_dir = ensure_dir(dest)
    report_path = report_dir / "rapport_sauvegarde.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
