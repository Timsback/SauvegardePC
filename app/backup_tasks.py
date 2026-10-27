"""
Briques Windows de l'application "Sauvegarde PC" : emplacements connus,
commandes système, navigateurs, messagerie, licences et configuration.

La copie des fichiers, les versions et la restauration sont dans
`backup_engine.py`. Chaque fonction prend un callback `log(str)` et est
conçue pour être appelée depuis un thread d'arrière-plan.
"""

import configparser
import ctypes
import fnmatch
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

USERPROFILE = Path(os.environ.get("USERPROFILE", str(Path.home())))
APPDATA = Path(os.environ.get("APPDATA", USERPROFILE / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", USERPROFILE / "AppData" / "Local"))

CONFIG_DIR = APPDATA / "SauvegardePC"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Caches des navigateurs et messageries : volumineux et régénérés seuls.
CACHE_PATTERNS = (
    "Cache", "Cache2", "Code Cache", "GPUCache", "Service Worker",
    "*.tmp", "*.log", "ShaderCache", "GrShaderCache", "component_crx_cache",
)

# Exclusions proposées par défaut pour les fichiers personnels (modifiables).
DEFAULT_EXCLUSIONS = ["node_modules", "__pycache__", "*.tmp", "~$*", "Thumbs.db"]


# --------------------------------------------------------------------------- #
# Annulation et commandes
# --------------------------------------------------------------------------- #

class Cancelled(Exception):
    """Opération interrompue à la demande de l'utilisateur."""


CANCEL = threading.Event()


def check_cancel() -> None:
    if CANCEL.is_set():
        raise Cancelled("Opération annulée.")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """
    Relance l'application avec élévation UAC. Renvoie False si l'utilisateur
    refuse ; sinon l'appelant doit fermer cette instance.
    """
    # Version compilée : sys.argv[0] est l'exécutable lui-même, à ne pas repasser en argument.
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    params = subprocess.list2cmdline(args)
    if getattr(sys, "frozen", False):
        from app_updates import reset_frozen_environment
        reset_frozen_environment()
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return result > 32


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_command(args, log, timeout=600, log_output=True) -> tuple[bool, str]:
    """
    Exécute une commande et journalise stdout/stderr. Renvoie (succès, sortie).
    Aucune entrée n'est fournie (une invite interactive échoue au lieu de bloquer),
    la commande est arrêtée après `timeout` secondes ou dès qu'une annulation est demandée.
    `log_output=False` évite de recopier une sortie sensible (clé de licence) dans le journal.
    """
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except FileNotFoundError:
        msg = f"Commande introuvable : {args[0]}"
        log(f"⚠ {msg}")
        return False, msg
    except Exception as exc:
        log(f"⚠ Erreur lors de l'exécution de {args[0]} : {exc}")
        return False, str(exc)

    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if CANCEL.is_set() or time.monotonic() > deadline:
                process.kill()
                process.communicate()
                if CANCEL.is_set():
                    raise Cancelled("Opération annulée.")
                log(f"⚠ Commande interrompue après {timeout // 60 or 1} min sans réponse : {args[0]}")
                return False, ""
    output = (stdout or "") + (stderr or "")
    if log_output and output.strip():
        log(output.strip())
    return process.returncode == 0, output


def running_processes(names) -> list[str]:
    """Noms d'exécutables (parmi `names`) actuellement lancés, via tasklist."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
            stdin=subprocess.DEVNULL, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    running = {line.split('","')[0].strip('"').casefold() for line in result.stdout.splitlines() if line}
    return sorted({n for n in names if n.casefold() in running})


# --------------------------------------------------------------------------- #
# Verrou partagé entre l'interface et la sauvegarde automatique
# --------------------------------------------------------------------------- #

class OperationLock:
    """
    Mutex Windows nommé : empêche la sauvegarde planifiée et l'interface de
    travailler en même temps. Doit être acquis et relâché dans le même thread.
    """
    NAME = "Local\\SauvegardePC-operation"

    def __init__(self):
        self.handle = None

    def acquire(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
        except AttributeError:
            return True
        handle = kernel32.CreateMutexW(None, False, self.NAME)
        if not handle:
            return True
        # WAIT_OBJECT_0 = 0, WAIT_ABANDONED = 0x80 (instance précédente plantée).
        if kernel32.WaitForSingleObject(handle, 0) in (0, 0x80):
            self.handle = handle
            return True
        kernel32.CloseHandle(handle)
        return False

    def release(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


# --------------------------------------------------------------------------- #
# Disques et volumes
# --------------------------------------------------------------------------- #

def _drive_letter(path) -> str:
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
    return drive.upper() if len(drive) == 2 and drive[1] == ":" else ""


def physical_disk_number(path):
    """Numéro du disque physique portant `path` (None pour un partage réseau ou en cas d'échec)."""
    drive = _drive_letter(path)
    if not drive:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return None
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(f"\\\\.\\{drive}", 0, 3, None, 3, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        return None
    try:
        buffer = (ctypes.c_ulong * 3)()  # DeviceType, DeviceNumber, PartitionNumber
        returned = ctypes.c_ulong()
        ok = kernel32.DeviceIoControl(ctypes.c_void_p(handle), 0x2D1080, None, 0, buffer,
                                      ctypes.sizeof(buffer), ctypes.byref(returned), None)
        return buffer[1] if ok else None
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def same_disk_as_system(path) -> bool:
    """True si la destination est sur le même disque physique que Windows."""
    system = os.environ.get("SystemDrive", "C:")
    if _drive_letter(path) == system.upper():
        return True
    disk = physical_disk_number(path)
    return disk is not None and disk == physical_disk_number(system + "\\")


def filesystem_name(path) -> str:
    """Système de fichiers du volume (« NTFS », « exFAT », « FAT32 »…), vide si inconnu."""
    drive = _drive_letter(path)
    root = drive + "\\" if drive else os.path.join(*Path(os.path.abspath(str(path))).parts[:2]) + "\\"
    name = ctypes.create_unicode_buffer(64)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64)
    except AttributeError:
        return ""
    return name.value if ok else ""


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


class FileFilter:
    """Exclusions de l'utilisateur (noms ou motifs) et taille maximale par fichier."""

    def __init__(self, patterns=(), max_size_mb=0, download_cloud=False):
        self.patterns = [p.casefold() for p in patterns if p and p.strip()]
        self.max_size = int(max_size_mb or 0) * 1024 * 1024
        self.download_cloud = bool(download_cloud)

    @classmethod
    def from_config(cls, config):
        return cls(config.get("exclusions", DEFAULT_EXCLUSIONS), config.get("max_file_size_mb", 0),
                   config.get("onedrive_cloud") == "download")

    def excluded(self, name: str) -> bool:
        name = name.casefold()
        return any(fnmatch.fnmatchcase(name, p) for p in self.patterns)


CACHE_FILTER = FileFilter(CACHE_PATTERNS, download_cloud=True)


# --------------------------------------------------------------------------- #
# 2. Navigateurs
# --------------------------------------------------------------------------- #

def chromium_browsers() -> dict[str, tuple[Path, str]]:
    """Navigateurs Chromium : nom → (dossier de profils, exécutable)."""
    return {
        "Google Chrome": (LOCALAPPDATA / "Google" / "Chrome" / "User Data", "chrome.exe"),
        "Microsoft Edge": (LOCALAPPDATA / "Microsoft" / "Edge" / "User Data", "msedge.exe"),
        "Brave": (LOCALAPPDATA / "BraveSoftware" / "Brave-Browser" / "User Data", "brave.exe"),
        "Vivaldi": (LOCALAPPDATA / "Vivaldi" / "User Data", "vivaldi.exe"),
        "Opera": (APPDATA / "Opera Software" / "Opera Stable", "opera.exe"),
        "Opera GX": (APPDATA / "Opera Software" / "Opera GX Stable", "opera.exe"),
    }


def firefox_dir() -> Path:
    return APPDATA / "Mozilla" / "Firefox"


def thunderbird_dir() -> Path:
    return APPDATA / "Thunderbird"


BROWSER_PROCESSES = {name: exe for name, (_, exe) in chromium_browsers().items()}
BROWSER_PROCESSES["Mozilla Firefox"] = "firefox.exe"


def detect_browsers() -> dict[str, bool]:
    detected = {name: path.exists() for name, (path, _) in chromium_browsers().items()}
    detected["Mozilla Firefox"] = (firefox_dir() / "Profiles").exists()
    return detected


def mozilla_profiles(app_dir: Path) -> list[Path]:
    """Profils existants d'une application Mozilla, lus dans profiles.ini (relatifs ou
    absolus), avec repli sur le dossier « Profiles » si le fichier est absent ou illisible."""
    found: list[Path] = []
    parser = configparser.RawConfigParser()
    try:
        parser.read(app_dir / "profiles.ini", encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        parser = configparser.RawConfigParser()
    for section in parser.sections():
        if not section.startswith("Profile") or not parser.has_option(section, "Path"):
            continue
        raw = parser.get(section, "Path")
        path = app_dir / raw if parser.get(section, "IsRelative", fallback="1") == "1" else Path(raw)
        if path.is_dir() and path not in found:
            found.append(path)
    if not found and (app_dir / "Profiles").is_dir():
        found = sorted(p for p in (app_dir / "Profiles").iterdir() if p.is_dir())
    return found


def thunderbird_profiles() -> list[Path]:
    return mozilla_profiles(thunderbird_dir())


def mozilla_default_profile(ini_text: str) -> str:
    """Nom du dossier du profil par défaut décrit par un profiles.ini sauvegardé."""
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(ini_text)
    except configparser.Error:
        return ""
    for section in parser.sections():
        if section.startswith("Install") and parser.has_option(section, "Default"):
            return Path(parser.get(section, "Default").replace("\\", "/")).name
    for section in parser.sections():
        if section.startswith("Profile") and parser.get(section, "Default", fallback="0") == "1":
            return Path(parser.get(section, "Path", fallback="").replace("\\", "/")).name
    return ""


def register_mozilla_profiles(app_dir: Path, names: list[str], default: str = "") -> None:
    """
    Déclare des profils restaurés (dans `app_dir/Profiles`) dans profiles.ini et
    installs.ini, et en fait le profil par défaut, pour que Firefox/Thunderbird
    les ouvrent au lieu de créer un profil vide.
    """
    ini = app_dir / "profiles.ini"
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    if ini.exists():
        parser.read(ini, encoding="utf-8")
    if not parser.has_section("General"):
        parser.add_section("General")
    parser.set("General", "StartWithLastProfile", "1")
    if not parser.has_option("General", "Version"):
        parser.set("General", "Version", "2")

    sections = {}
    for section in parser.sections():
        if section.startswith("Profile") and parser.has_option(section, "Path"):
            sections[Path(parser.get(section, "Path").replace("\\", "/")).name.casefold()] = section
    index = 0
    for name in names:
        if name.casefold() in sections:
            continue
        while parser.has_section(f"Profile{index}"):
            index += 1
        section = f"Profile{index}"
        parser.add_section(section)
        parser.set(section, "Name", name.split(".", 1)[-1] or name)
        parser.set(section, "IsRelative", "1")
        parser.set(section, "Path", f"Profiles/{name}")
        sections[name.casefold()] = section

    default = default if default in names else (names[0] if names else "")
    if default:
        for section in sections.values():
            parser.remove_option(section, "Default")
        parser.set(sections[default.casefold()], "Default", "1")
        for section in parser.sections():
            if section.startswith("Install"):
                parser.set(section, "Default", f"Profiles/{default}")
                parser.set(section, "Locked", "1")
    ensure_dir(app_dir)
    _write_ini(parser, ini)

    installs = app_dir / "installs.ini"
    if default and installs.exists():
        other = configparser.RawConfigParser()
        other.optionxform = str
        other.read(installs, encoding="utf-8")
        for section in other.sections():
            other.set(section, "Default", f"Profiles/{default}")
            other.set(section, "Locked", "1")
        _write_ini(other, installs)


def _write_ini(parser, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        parser.write(stream, space_around_delimiters=False)
    os.replace(temporary, path)


# --------------------------------------------------------------------------- #
# 3. Clés Windows & licences
# --------------------------------------------------------------------------- #

def mask_key(key: str) -> str:
    """Ne laisse visibles que les 5 derniers caractères d'une clé (journal et rapport)."""
    return "".join(c if c == "-" else "•" for c in key[:-5]) + key[-5:] if len(key) > 5 else "•" * len(key)


def get_windows_key(log) -> str:
    log("→ Lecture de la clé Windows OEM (nécessite les droits administrateur) ...")
    ok, output = run_command(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "(Get-CimInstance -Query \"select * from SoftwareLicensingService\").OA3xOriginalProductKey",
        ],
        log, timeout=120, log_output=False,
    )
    key = output.strip()
    if ok and key:
        log(f"  ✓ Clé trouvée : {mask_key(key)} (complète dans Licences\\licences.txt)")
        return key
    if not ok and key:
        log(f"  ⚠ Lecture de la clé impossible : {key.splitlines()[0][:200]}")
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

# Sans ces options, winget attend une réponse « Y/N » aux conditions des sources
# lors de sa première utilisation et l'opération ne se termine jamais.
WINGET_NON_INTERACTIVE = ("--accept-source-agreements", "--disable-interactivity")


def export_installed_programs(dest: Path, log) -> None:
    target = ensure_dir(dest / "Programmes")
    json_path = target / "programmes.json"
    log("→ Export de la liste des programmes avec winget ...")
    ok, _ = run_command(["winget", "export", "-o", str(json_path), *WINGET_NON_INTERACTIVE], log, timeout=600)
    if ok:
        log(f"  ✓ Liste exportée : {json_path}")
    else:
        log("  ⚠ winget export a échoué ou winget est absent.")

    log("→ Récupération de la liste complète (y compris logiciels non-winget) ...")
    ok2, output = run_command(["winget", "list", *WINGET_NON_INTERACTIVE], log, timeout=600)
    if output.strip():
        (target / "programmes_liste_complete.txt").write_text(output, encoding="utf-8")
        log(f"  ✓ Liste complète enregistrée : {target / 'programmes_liste_complete.txt'}")
        log("  ℹ Les logiciels marqués « Installed package is not available from any source »")
        log("    ne seront PAS réinstallés automatiquement : notez-les pour une réinstallation manuelle.")


def import_installed_programs(source: Path, log) -> bool:
    """Réinstalle les programmes de programmes.json avec winget (Internet requis)."""
    json_path = source / "Programmes" / "programmes.json"
    if not json_path.is_file():
        log("— Programmes : aucune liste winget dans cette sauvegarde.")
        return False
    log("→ Réinstallation des programmes avec winget (peut durer longtemps) ...")
    ok, _ = run_command(["winget", "import", "-i", str(json_path), "--ignore-unavailable",
                         "--accept-package-agreements", *WINGET_NON_INTERACTIVE], log, timeout=3 * 3600)
    log("  ✓ Programmes réinstallés." if ok else
        "  ⚠ winget import a signalé des erreurs : certains programmes restent à installer à la main.")
    return ok


# --------------------------------------------------------------------------- #
# 5. Messagerie
# --------------------------------------------------------------------------- #

def outlook_data_files() -> list[Path]:
    """Fichiers .pst trouvés aux emplacements habituels d'Outlook (sans doublon)."""
    documents = get_known_folder("Documents")
    folders = [LOCALAPPDATA / "Microsoft" / "Outlook", APPDATA / "Microsoft" / "Outlook",
               documents / "Fichiers Outlook", documents / "Outlook Files"]
    found, seen = [], set()
    for folder in folders:
        try:
            candidates = sorted(folder.glob("*.pst")) if folder.is_dir() else []
        except OSError:
            continue
        for path in candidates:
            key = os.path.normcase(str(path.resolve()))
            if key not in seen and path.is_file():
                seen.add(key)
                found.append(path)
    return found


def outlook_signatures_dir() -> Path:
    return APPDATA / "Microsoft" / "Signatures"


# --------------------------------------------------------------------------- #
# 6. Profils Wi-Fi et pilotes (nécessitent les droits administrateur)
# --------------------------------------------------------------------------- #

def export_wifi_profiles(dest: Path, log) -> None:
    if not is_admin():
        log("⚠ Droits administrateur requis pour exporter les profils Wi-Fi. Relancez l'application en administrateur.")
        return
    target = ensure_dir(dest / "WiFi")
    log("→ Export des profils Wi-Fi enregistrés (avec mots de passe) ...")
    ok, _ = run_command(["netsh", "wlan", "export", "profile", "key=clear", f"folder={target}"], log, timeout=120)
    if ok:
        log(f"  ✓ Profils Wi-Fi exportés dans {target}")
    else:
        log("  ⚠ L'export des profils Wi-Fi a échoué.")


def import_wifi_profiles(source: Path, log) -> int:
    profiles = sorted((source / "WiFi").glob("*.xml")) if (source / "WiFi").is_dir() else []
    if not profiles:
        log("— Wi-Fi : aucun profil dans cette sauvegarde.")
        return 0
    scope = "all" if is_admin() else "current"
    log(f"→ Import de {len(profiles)} profil(s) Wi-Fi ...")
    imported = 0
    for path in profiles:
        check_cancel()
        ok, _ = run_command(["netsh", "wlan", "add", "profile", f"filename={path}", f"user={scope}"],
                            log, timeout=60, log_output=False)
        imported += ok
        log(f"  {'✓' if ok else '⚠'} {path.stem}")
    return imported


def export_drivers(dest: Path, log) -> None:
    if not is_admin():
        log("⚠ Droits administrateur requis pour exporter les pilotes. Relancez l'application en administrateur.")
        return
    target = ensure_dir(dest / "Pilotes")
    log("→ Export des pilotes installés (peut prendre plusieurs minutes) ...")
    ok, _ = run_command(["dism", "/online", "/export-driver", f"/destination:{target}"], log, timeout=1800)
    if ok:
        log(f"  ✓ Pilotes exportés dans {target}")
    else:
        log("  ⚠ L'export des pilotes a échoué.")


def import_drivers(source: Path, log) -> bool:
    folder = source / "Pilotes"
    if not folder.is_dir() or not any(folder.rglob("*.inf")):
        log("— Pilotes : aucun pilote dans cette sauvegarde.")
        return False
    if not is_admin():
        log("⚠ Droits administrateur requis pour installer les pilotes. Relancez l'application en administrateur.")
        return False
    log("→ Installation des pilotes sauvegardés (peut prendre plusieurs minutes) ...")
    ok, _ = run_command(["pnputil", "/add-driver", str(folder / "*.inf"), "/subdirs", "/install"], log, timeout=1800)
    log("  ✓ Pilotes ajoutés au magasin Windows." if ok else
        "  ⚠ Certains pilotes n'ont pas pu être installés (déjà présents ou incompatibles).")
    return ok


# --------------------------------------------------------------------------- #
# Configuration persistée
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "custom_folders": [],
    "checklist": {},
    "last_destination": "",
    "last_backup_at": "",
    "exclusions": list(DEFAULT_EXCLUSIONS),
    "max_file_size_mb": 0,
    "onedrive_cloud": "skip",
    "keep_versions": 3,
    "verify_after_copy": True,
    "auto_backup": {"enabled": False, "interval_days": 7},
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("configuration invalide")
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            merged.update(data)
            return merged
        except Exception:
            # Conserve le fichier illisible pour une récupération manuelle
            # au lieu de l'écraser à la prochaine sauvegarde des réglages.
            try:
                os.replace(CONFIG_FILE, CONFIG_FILE.with_name("config.corrompu.json"))
            except OSError:
                pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config: dict) -> None:
    """Écriture atomique : un arrêt brutal laisse l'ancienne configuration intacte."""
    ensure_dir(CONFIG_DIR)
    temporary = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(config, indent=2, ensure_ascii=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, CONFIG_FILE)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "o" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} To".replace(".", ",")


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


# --------------------------------------------------------------------------- #
# Rapport de session
# --------------------------------------------------------------------------- #

def write_report(dest: Path, lines: list[str]) -> Path:
    report_dir = ensure_dir(dest)
    report_path = report_dir / "rapport_sauvegarde.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
