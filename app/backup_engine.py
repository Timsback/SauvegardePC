"""
Sauvegardes versionnées et restauration complète.

Chaque sauvegarde crée un dossier daté `SauvegardePC_<PC>_<date>` dans la
destination. Les fichiers inchangés depuis la version précédente y sont
ajoutés par lien physique (NTFS) : chaque version est complète et lisible
seule, mais seules les nouveautés occupent de la place. Sur un support qui
ne gère pas les liens (exFAT, FAT32), chaque version est une copie complète.

`sauvegarde.json` (résumé) et `fichiers.json` (index : taille, date, SHA-256)
permettent de vérifier une version et de la restaurer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys
import threading
import time
from uuid import uuid4

import backup_tasks as bt

FORMAT = 1
SUMMARY = "sauvegarde.json"
INDEX = "fichiers.json"
REPORT = "rapport_sauvegarde.txt"
VERSION_PREFIX = "SauvegardePC_"
CATEGORIES = ("files", "browsers", "licenses", "programs", "email", "wifi")
RESTORABLE = ("files", "browsers", "email", "programs", "wifi", "drivers")
CATEGORY_LABELS = {
    "files": "Fichiers personnels", "browsers": "Navigateurs", "licenses": "Licences",
    "programs": "Programmes", "email": "Messagerie", "wifi": "Wi-Fi", "drivers": "Pilotes",
}
# Fichier uniquement dans le cloud (OneDrive « à la demande ») ou hors ligne.
CLOUD_ATTRIBUTES = 0x400000 | 0x40000 | 0x1000
# Jonctions et liens symboliques : jamais suivis (boucles, doublons, autres disques).
LINK_TAGS = (0xA0000003, 0xA000000C)
CHUNK = 1024 * 1024
FILE_WEIGHT = 16 * 1024  # coût fixe estimé par fichier pour la progression


def _long(path) -> str:
    """Préfixe \\\\?\\ pour dépasser la limite Windows de 260 caractères."""
    s = os.path.abspath(str(path))
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _key(path) -> str:
    s = str(path)
    if s.startswith("\\\\?\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith("\\\\?\\"):
        s = s[4:]
    return os.path.normcase(os.path.abspath(s))


def _within(path, parent) -> bool:
    path, parent = _key(path), _key(parent)
    return path == parent or path.startswith(parent.rstrip("\\/") + os.sep)


def write_json(path: Path, data) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(_long(temporary), "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
    os.replace(_long(temporary), _long(path))


def read_json(path: Path, default=None):
    try:
        with open(_long(path), encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def file_digest(path) -> str:
    digest = hashlib.sha256()
    with open(_long(path), "rb") as stream:
        while chunk := stream.read(CHUNK):
            bt.check_cancel()
            digest.update(chunk)
    return digest.hexdigest()


def remove_tree(path: Path) -> None:
    def retry(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    if sys.version_info >= (3, 12):
        shutil.rmtree(_long(path), onexc=retry)
    else:
        shutil.rmtree(_long(path), onerror=retry)


# Dossiers de la destination qui appartiennent à l'application : jamais recopiés,
# même si la destination se trouve dans un dossier sauvegardé (ex. Documents).
OWNED_NAMES = ("Logiciels", "Licences", "Fichiers personnels", "Divers (dossiers personnalisés)",
               "Navigateurs", "Email", "WiFi", "Pilotes", "Programmes")


def owned_paths(root: Path) -> list[Path]:
    root = Path(root)
    try:
        versions = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(VERSION_PREFIX)]
    except OSError:
        versions = []
    return versions + [root / name for name in OWNED_NAMES if (root / name).exists()]


# --------------------------------------------------------------------------- #
# Progression
# --------------------------------------------------------------------------- #

class Job:
    """Progression d'ensemble d'une opération, lue par l'interface."""

    def __init__(self, log=lambda message: None):
        self.log = log
        self.lock = threading.Lock()
        self.phase = "Préparation"
        self.detail = ""
        self.units_total = 0
        self.units_done = 0
        self.files_total = 0
        self.files_done = 0
        self.started = time.monotonic()
        self.phase_started = self.started

    def start_phase(self, phase, files_total=0, bytes_total=0):
        with self.lock:
            self.phase, self.detail = phase, ""
            self.files_total, self.files_done = files_total, 0
            self.units_total = bytes_total + files_total * FILE_WEIGHT
            self.units_done = 0
            self.phase_started = time.monotonic()

    def advance(self, files=1, nbytes=0, detail=None):
        with self.lock:
            self.files_done += files
            self.units_done += nbytes + files * FILE_WEIGHT
            if detail is not None:
                self.detail = detail

    def set_detail(self, detail):
        with self.lock:
            self.detail = detail

    def snapshot(self):
        with self.lock:
            percent = round(self.units_done * 100 / self.units_total) if self.units_total else 0
            elapsed = time.monotonic() - self.phase_started
            eta = None
            if self.units_total and self.units_done and elapsed > 3:
                eta = elapsed * (self.units_total - self.units_done) / self.units_done
            label = self.phase
            if self.files_total:
                label += f" · {self.files_done}/{self.files_total} fichiers"
            if eta is not None and percent < 100:
                label += f" · environ {bt.format_duration(eta)} restantes"
            return dict(percent=min(percent, 100), label=label, detail=self.detail)


# --------------------------------------------------------------------------- #
# Sources à sauvegarder
# --------------------------------------------------------------------------- #

@dataclass
class Source:
    category: str
    target: str                 # chemin relatif dans la version (séparateur « / »)
    path: Path                  # dossier ou fichier d'origine
    restore: dict               # comment le remettre en place
    filter: bt.FileFilter = field(default_factory=bt.FileFilter)

    def describe(self):
        return dict(category=self.category, target=self.target, original=str(self.path), restore=self.restore)


def plan_sources(config: dict, categories, log=lambda message: None) -> list[Source]:
    sources: list[Source] = []
    if "files" in categories:
        user_filter = bt.FileFilter.from_config(config)
        for name in config.get("selected_folders", bt.STANDARD_FOLDERS):
            if name in bt.STANDARD_FOLDERS:
                sources.append(Source("files", f"Fichiers personnels/{name}", bt.get_known_folder(name),
                                      dict(kind="known_folder", name=name), user_filter))
        saved_games = bt.USERPROFILE / "Saved Games"
        if saved_games.is_dir():
            sources.append(Source("files", "Fichiers personnels/Saved Games", saved_games,
                                  dict(kind="saved_games"), user_filter))
        used = set()
        for folder in config.get("custom_folders", []):
            path = Path(folder)
            name = _unique(_safe_name(path.name or path.drive.rstrip(":") or "Dossier"), used)
            sources.append(Source("files", f"Divers (dossiers personnalisés)/{name}", path,
                                  dict(kind="path", path=str(path)), user_filter))
    if "browsers" in categories:
        for name, (path, _exe) in bt.chromium_browsers().items():
            if path.exists():
                sources.append(Source("browsers", f"Navigateurs/{name}", path,
                                      dict(kind="chromium", browser=name), bt.CACHE_FILTER))
        firefox = bt.firefox_dir()
        for profile in bt.mozilla_profiles(firefox):
            sources.append(Source("browsers", f"Navigateurs/Mozilla Firefox/{profile.name}", profile,
                                  dict(kind="firefox_profile", name=profile.name), bt.CACHE_FILTER))
        if (firefox / "profiles.ini").is_file():
            sources.append(Source("browsers", "Navigateurs/Mozilla Firefox/_profiles.ini", firefox / "profiles.ini",
                                  dict(kind="metadata")))
    if "email" in categories:
        thunderbird = bt.thunderbird_dir()
        for profile in bt.thunderbird_profiles():
            sources.append(Source("email", f"Email/Thunderbird/{profile.name}", profile,
                                  dict(kind="thunderbird_profile", name=profile.name), bt.CACHE_FILTER))
        if (thunderbird / "profiles.ini").is_file():
            sources.append(Source("email", "Email/Thunderbird/_profiles.ini", thunderbird / "profiles.ini",
                                  dict(kind="metadata")))
        used = set()
        for pst in bt.outlook_data_files():
            name = _unique(pst.name, used)
            sources.append(Source("email", f"Email/Outlook/{name}", pst, dict(kind="outlook_pst", name=name)))
        signatures = bt.outlook_signatures_dir()
        if signatures.is_dir():
            sources.append(Source("email", "Email/Outlook/Signatures", signatures, dict(kind="outlook_signatures")))
    return sources


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name).strip(" .") or "Dossier"


def _unique(name: str, used: set) -> str:
    candidate, n = name, 2
    while candidate.casefold() in used:
        stem, dot, ext = name.rpartition(".")
        candidate = f"{stem} ({n}).{ext}" if dot and stem else f"{name} ({n})"
        n += 1
    used.add(candidate.casefold())
    return candidate


# --------------------------------------------------------------------------- #
# Analyse (inventaire des fichiers)
# --------------------------------------------------------------------------- #

@dataclass
class Scan:
    entries: list = field(default_factory=list)      # (index source, chemin relatif, taille, date ns)
    per_category: dict = field(default_factory=dict)  # catégorie → [fichiers, octets]
    skipped: dict = field(default_factory=lambda: dict(cloud=0, large=0, excluded=0, links=0))
    cloud_examples: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def total_bytes(self):
        return sum(e[2] for e in self.entries)


def scan(sources: list[Source], exclude_roots=(), job: Job | None = None) -> Scan:
    result = Scan()
    excluded = {_key(p) for p in exclude_roots}
    for index, source in enumerate(sources):
        bt.check_cancel()
        if job:
            job.set_detail(str(source.path))
        counts = result.per_category.setdefault(source.category, [0, 0])
        if not source.path.exists():
            if source.restore.get("kind") == "path":
                result.errors.append(f"Dossier introuvable, ignoré : {source.path}")
            continue
        if any(_within(source.path, root) for root in excluded):
            result.errors.append(f"{source.path} se trouve dans le dossier de sauvegarde : ignoré.")
            continue
        if source.path.is_file():
            st = os.stat(_long(source.path))
            result.entries.append((index, "", st.st_size, st.st_mtime_ns))
            counts[0] += 1
            counts[1] += st.st_size
            continue
        for rel, size, mtime in _walk(source, excluded, result):
            result.entries.append((index, rel, size, mtime))
            counts[0] += 1
            counts[1] += size
            if job and len(result.entries) % 500 == 0:
                job.set_detail(f"{len(result.entries)} fichiers trouvés…")
    return result


def _walk(source: Source, excluded: set, result: Scan):
    root = _long(source.path)
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        directory = root if not rel_dir else root + "\\" + rel_dir.replace("/", "\\")
        try:
            iterator = os.scandir(directory)
        except OSError as exc:
            result.errors.append(f"Dossier illisible : {_key(directory)} ({exc.__class__.__name__})")
            continue
        with iterator:
            for entry in iterator:
                bt.check_cancel()
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    st = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or getattr(st, "st_reparse_tag", 0) in LINK_TAGS:
                        result.skipped["links"] += 1
                        continue
                    if source.filter.excluded(entry.name):
                        result.skipped["excluded"] += 1
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        if _key(entry.path) in excluded:
                            continue
                        stack.append(rel)
                        continue
                    if getattr(st, "st_file_attributes", 0) & CLOUD_ATTRIBUTES and not source.filter.download_cloud:
                        result.skipped["cloud"] += 1
                        if len(result.cloud_examples) < 20:
                            result.cloud_examples.append(_key(entry.path))
                        continue
                    if source.filter.max_size and st.st_size > source.filter.max_size:
                        result.skipped["large"] += 1
                        continue
                    yield rel, st.st_size, st.st_mtime_ns
                except OSError as exc:
                    result.errors.append(f"Fichier illisible : {_key(entry.path)} ({exc.__class__.__name__})")


def _target_rel(source: Source, rel: str) -> str:
    return source.target + ("/" + rel if rel else "")


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

def computer_name() -> str:
    return _safe_name(os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC")


def list_versions(root: Path, computer: str | None = None) -> list[dict]:
    """Versions trouvées dans une destination, de la plus récente à la plus ancienne."""
    versions = []
    try:
        candidates = [p for p in Path(root).iterdir() if p.is_dir() and p.name.startswith(VERSION_PREFIX)]
    except OSError:
        candidates = []
    for folder in candidates:
        summary = read_json(folder / SUMMARY)
        if not isinstance(summary, dict) or summary.get("format") != FORMAT:
            continue
        if computer and summary.get("computer") != computer:
            continue
        versions.append(dict(summary, folder=str(folder), name=folder.name))
    versions.sort(key=lambda v: v.get("created", ""), reverse=True)
    if computer is None and is_legacy_backup(Path(root)):
        versions.append(dict(folder=str(root), name=Path(root).name, legacy=True, status="legacy",
                             created="", computer="", categories=detect_categories(Path(root))))
    return versions


def is_legacy_backup(folder: Path) -> bool:
    """Sauvegarde des versions ≤ 1.10 : catégories directement à la racine, sans index."""
    return not (folder / SUMMARY).exists() and any(
        (folder / name).exists() for name in ("Fichiers personnels", "Navigateurs", "Email", "WiFi", "Pilotes", "Programmes"))


def resolve_restore_source(path: Path) -> list[dict]:
    """Versions utilisables à partir d'un dossier choisi (version, destination ou ancienne sauvegarde)."""
    path = Path(path)
    summary = read_json(path / SUMMARY)
    if isinstance(summary, dict) and summary.get("format") == FORMAT:
        return [dict(summary, folder=str(path), name=path.name)]
    return list_versions(path)


def detect_categories(folder: Path) -> list[str]:
    found = []
    if (folder / "Fichiers personnels").is_dir() or (folder / "Divers (dossiers personnalisés)").is_dir():
        found.append("files")
    if (folder / "Navigateurs").is_dir():
        found.append("browsers")
    if (folder / "Email").is_dir():
        found.append("email")
    if (folder / "Programmes" / "programmes.json").is_file():
        found.append("programs")
    if (folder / "WiFi").is_dir() and any((folder / "WiFi").glob("*.xml")):
        found.append("wifi")
    if (folder / "Pilotes").is_dir() and any((folder / "Pilotes").rglob("*.inf")):
        found.append("drivers")
    return found


def supports_hard_links(root: Path) -> bool:
    probe = Path(root) / f".liens-{uuid4().hex[:8]}"
    link = probe.with_name(probe.name + "-b")
    try:
        probe.write_bytes(b"")
        os.link(_long(probe), _long(link))
        return True
    except OSError:
        return False
    finally:
        for path in (link, probe):
            try:
                path.unlink()
            except OSError:
                pass


def previous_version(root: Path, computer: str):
    for version in list_versions(root, computer):
        if version.get("status") in ("complete", "partial"):
            index = read_json(Path(version["folder"]) / INDEX, {})
            if isinstance(index, dict):
                return Path(version["folder"]), index
    return None, {}


def apply_retention(root: Path, computer: str, keep: int, current: Path, log) -> int:
    """Garde les `keep` versions utilisables les plus récentes de ce PC ; supprime les plus
    anciennes et les versions interrompues antérieures à la dernière version utilisable."""
    keep = max(1, int(keep or 1))
    versions = [v for v in list_versions(root, computer) if not v.get("legacy")]
    usable = [v for v in versions if v.get("status") in ("complete", "partial")]
    if not usable:
        return 0
    newest_usable = usable[0].get("created", "")
    doomed = usable[keep:] + [v for v in versions if v.get("status") not in ("complete", "partial")
                              and v.get("created", "") < newest_usable]
    removed = 0
    for version in doomed:
        folder = Path(version["folder"])
        if _key(folder) == _key(current) or not folder.name.startswith(VERSION_PREFIX):
            continue
        try:
            remove_tree(folder)
            removed += 1
            log(f"  🗑 Ancienne version supprimée : {folder.name}")
        except OSError as exc:
            log(f"  ⚠ Ancienne version non supprimée : {folder.name} ({exc.__class__.__name__})")
    return removed


# --------------------------------------------------------------------------- #
# Estimation et sauvegarde
# --------------------------------------------------------------------------- #

def estimate(root: Path, config: dict, categories, job: Job | None = None, log=lambda m: None) -> dict:
    root = Path(root)
    sources = plan_sources(config, categories, log)
    result = scan(sources, owned_paths(root), job)
    _, previous = previous_version(root, computer_name())
    links = supports_hard_links(root)
    to_copy = 0
    for index, rel, size, mtime in result.entries:
        known = previous.get(_target_rel(sources[index], rel))
        if not (links and known and known[0] == size and known[1] == mtime):
            to_copy += size
    free = shutil.disk_usage(root).free
    return dict(
        total_bytes=result.total_bytes, files=len(result.entries), to_copy_bytes=to_copy, free_bytes=free,
        enough_space=free >= _required(to_copy), hard_links=links,
        per_category={k: dict(files=v[0], bytes=v[1]) for k, v in result.per_category.items()},
        skipped=result.skipped, errors=result.errors[:50], computed_at=datetime.now().isoformat(timespec="seconds"),
    )


def _required(nbytes: int) -> int:
    return int(nbytes * 1.01) + 200 * 1024 * 1024


def run_backup(root: Path, config: dict, categories, job: Job, app_version: str = "") -> dict:
    """Crée une nouvelle version complète dans `root`. Renvoie un résumé pour l'interface."""
    log = job.log
    root = Path(root)
    bt.ensure_dir(root)
    computer = computer_name()
    stamp = datetime.now()
    folder = root / f"{VERSION_PREFIX}{computer}_{stamp:%Y-%m-%d_%H-%M-%S}"
    folder.mkdir()
    summary = dict(format=FORMAT, app_version=app_version, computer=computer, user=os.environ.get("USERNAME", ""),
                   created=stamp.isoformat(timespec="seconds"), finished="", status="running",
                   categories=list(categories), sources=[], stats={}, skipped={}, errors=[])
    write_json(folder / SUMMARY, summary)
    errors: list[str] = summary["errors"]
    index: dict = {}
    try:
        job.start_phase("Analyse des fichiers")
        log(f"━━ Nouvelle version : {folder.name} ━━")
        sources = plan_sources(config, categories, log)
        summary["sources"] = [s.describe() for s in sources]
        result = scan(sources, owned_paths(root), job)
        errors.extend(result.errors)
        summary["skipped"] = dict(result.skipped)
        if result.skipped["cloud"]:
            log(f"ℹ {result.skipped['cloud']} fichier(s) OneDrive uniquement en ligne non téléchargé(s) : "
                "ils restent disponibles dans votre OneDrive.")
        if result.skipped["large"] or result.skipped["excluded"]:
            log(f"ℹ Exclusions appliquées : {result.skipped['excluded']} élément(s) exclu(s), "
                f"{result.skipped['large']} fichier(s) au-dessus de la taille maximale.")

        prev_folder, previous = previous_version(root, computer)
        links = supports_hard_links(root)
        if prev_folder and not links:
            log(f"ℹ Le support ({bt.filesystem_name(root) or 'inconnu'}) ne gère pas les liens physiques : "
                "cette version est une copie complète.")
        plan, to_copy = [], 0
        for item in result.entries:
            idx, rel, size, mtime = item
            known = previous.get(_target_rel(sources[idx], rel)) if links else None
            reuse = bool(known and known[0] == size and known[1] == mtime)
            plan.append((item, known if reuse else None))
            to_copy += 0 if reuse else size
        free = shutil.disk_usage(root).free
        if free < _required(to_copy):
            raise OSError(f"Espace insuffisant : {bt.format_size(to_copy)} à copier, "
                          f"{bt.format_size(free)} libres sur la destination.")
        log(f"→ {len(plan)} fichier(s), {bt.format_size(result.total_bytes)} au total, "
            f"{bt.format_size(to_copy)} à copier.")

        copied, linked, new_files = _copy_all(sources, plan, folder, prev_folder, job, index, errors)
        summary["stats"] = dict(files=len(index), total_bytes=sum(v[0] for v in index.values()),
                                copied_files=copied, copied_bytes=to_copy, linked_files=linked)

        if config.get("verify_after_copy", True) and new_files:
            _verify_new(folder, new_files, index, job, errors)

        for category in categories:
            if category in ("licenses", "programs", "wifi"):
                _run_command_category(category, root, folder, job, errors)
        _index_exports(folder, index, job)

        summary["status"] = "partial" if errors else "complete"
        log(f"✓ Version {folder.name} : {copied} fichier(s) copié(s), {linked} inchangé(s) réutilisé(s).")
    except bt.Cancelled:
        summary["status"] = "cancelled"
        log("⚠ Sauvegarde annulée : cette version est incomplète et ne servira pas de référence.")
        raise
    except Exception as exc:
        summary["status"] = "failed"
        errors.append(str(exc))
        raise
    finally:
        summary["finished"] = datetime.now().isoformat(timespec="seconds")
        summary["errors"] = errors[:2000]
        write_json(folder / INDEX, index)
        write_json(folder / SUMMARY, summary)
        _write_version_report(folder, summary)
    if summary["status"] in ("complete", "partial"):
        apply_retention(root, computer, config.get("keep_versions", 3), folder, log)
    return dict(partial=summary["status"] == "partial", folder=str(folder), report=str(folder / REPORT),
                message=f"{len(errors)} point(s) à vérifier." if errors else "")


def _copy_all(sources, plan, folder, prev_folder, job, index, errors):
    total_bytes = sum(item[2] for item, reuse in plan if not reuse)
    job.start_phase("Copie", len(plan), total_bytes)
    copied = linked = 0
    new_files = []
    created_dirs = set()
    for (idx, rel, size, mtime), reuse in plan:
        bt.check_cancel()
        source = sources[idx]
        target_rel = _target_rel(source, rel)
        src = source.path / rel if rel else source.path
        dst = folder / target_rel
        parent = str(dst.parent)
        try:
            if parent not in created_dirs:
                os.makedirs(_long(dst.parent), exist_ok=True)
                created_dirs.add(parent)
            if reuse:
                try:
                    os.link(_long(prev_folder / target_rel), _long(dst))
                    index[target_rel] = [size, mtime, reuse[2]]
                    linked += 1
                    job.advance(1, 0, target_rel)
                    continue
                except OSError:
                    pass  # version précédente modifiée ou limite de liens : copie normale
            checksum = _copy_file(src, dst, job)
            index[target_rel] = [size, mtime, checksum]
            new_files.append(target_rel)
            copied += 1
            job.advance(1, 0, target_rel)
        except bt.Cancelled:
            raise
        except OSError as exc:
            errors.append(f"Fichier ignoré (verrouillé ou inaccessible) : {src} ({exc.__class__.__name__})")
            job.advance(1, 0)
    return copied, linked, new_files


def _copy_file(src: Path, dst: Path, job: Job) -> str:
    digest = hashlib.sha256()
    temporary = dst.with_name(dst.name + ".part")
    try:
        with open(_long(src), "rb") as reader, open(_long(temporary), "wb") as writer:
            while chunk := reader.read(CHUNK):
                bt.check_cancel()
                writer.write(chunk)
                digest.update(chunk)
                job.advance(0, len(chunk))
        shutil.copystat(_long(src), _long(temporary))
        os.replace(_long(temporary), _long(dst))
    except BaseException:
        try:
            os.unlink(_long(temporary))
        except OSError:
            pass
        raise
    return digest.hexdigest()


def _verify_new(folder, new_files, index, job, errors):
    job.start_phase("Vérification", len(new_files), sum(index[r][0] for r in new_files))
    bad = 0
    for rel in new_files:
        bt.check_cancel()
        try:
            if file_digest(folder / rel) != index[rel][2]:
                raise OSError("contenu différent")
        except OSError as exc:
            bad += 1
            errors.append(f"Vérification échouée : {rel} ({exc})")
        job.advance(1, index[rel][0], rel)
    job.log(f"✓ Vérification : {len(new_files) - bad}/{len(new_files)} fichier(s) copiés identiques à l'original.")


def _run_command_category(category, root, folder, job, errors):
    log = job.log
    job.start_phase(CATEGORY_LABELS[category])
    log(f"━━ {CATEGORY_LABELS[category].upper()} ━━")
    try:
        if category == "licenses":
            # Les licences saisies dans l'application restent à la racine de la destination.
            licenses = bt.load_licenses(root)
            key = bt.get_windows_key(log)
            if key and not any(item.get("logiciel") == "Windows OEM" for item in licenses):
                licenses.append({"logiciel": "Windows OEM", "cle": key})
                bt.save_licenses(root, licenses, log)
            bt.save_licenses(folder, licenses, log)
        elif category == "programs":
            bt.export_installed_programs(folder, log)
        elif category == "wifi":
            job.start_phase("Wi-Fi")
            bt.export_wifi_profiles(folder, log)
            bt.check_cancel()
            job.start_phase("Export des pilotes")
            bt.export_drivers(folder, log)
    except bt.Cancelled:
        raise
    except Exception as exc:
        errors.append(f"{CATEGORY_LABELS[category]} : {exc}")
        log(f"⚠ {CATEGORY_LABELS[category]} : {exc}")


def _index_exports(folder: Path, index: dict, job: Job) -> None:
    """Ajoute à l'index les exports (licences, programmes, Wi-Fi, pilotes) pour pouvoir les vérifier."""
    files = [p for name in ("Licences", "Programmes", "WiFi", "Pilotes") if (folder / name).is_dir()
             for p in (folder / name).rglob("*") if p.is_file()]
    if not files:
        return
    job.start_phase("Indexation des exports", len(files), sum(p.stat().st_size for p in files))
    for path in files:
        st = path.stat()
        index[str(path.relative_to(folder)).replace("\\", "/")] = [st.st_size, st.st_mtime_ns, file_digest(path)]
        job.advance(1, st.st_size)


def _write_version_report(folder: Path, summary: dict) -> None:
    stats = summary.get("stats", {})
    lines = [
        "Sauvegarde PC — rapport de version", "=" * 36, "",
        f"Version : {folder.name}", f"PC : {summary.get('computer')}  ·  Utilisateur : {summary.get('user')}",
        f"Début : {summary.get('created')}  ·  Fin : {summary.get('finished')}",
        f"État : {STATUS_LABELS.get(summary.get('status'), summary.get('status'))}",
        f"Catégories : {', '.join(CATEGORY_LABELS.get(c, c) for c in summary.get('categories', []))}", "",
        f"Fichiers : {stats.get('files', 0)} ({bt.format_size(stats.get('total_bytes', 0))})",
        f"Copiés : {stats.get('copied_files', 0)} ({bt.format_size(stats.get('copied_bytes', 0))})  ·  "
        f"Inchangés réutilisés : {stats.get('linked_files', 0)}",
    ]
    skipped = summary.get("skipped", {})
    if any(skipped.values()):
        lines += ["", f"Non copiés : {skipped.get('cloud', 0)} fichier(s) OneDrive en ligne uniquement, "
                      f"{skipped.get('excluded', 0)} élément(s) exclu(s), {skipped.get('large', 0)} trop volumineux, "
                      f"{skipped.get('links', 0)} lien(s)/jonction(s)."]
    if summary.get("errors"):
        lines += ["", "Points à vérifier :", *summary["errors"]]
    (folder / REPORT).write_text("\n".join(lines), encoding="utf-8")


STATUS_LABELS = {"complete": "Terminée", "partial": "Terminée avec des points à vérifier", "running": "En cours",
                 "cancelled": "Annulée", "failed": "Échec", "legacy": "Ancienne sauvegarde"}


# --------------------------------------------------------------------------- #
# Vérification d'une version existante
# --------------------------------------------------------------------------- #

def verify_version(folder: Path, job: Job) -> dict:
    folder = Path(folder)
    index = read_json(folder / INDEX)
    if not isinstance(index, dict):
        raise ValueError("Cette sauvegarde n'a pas d'index de vérification (ancienne version).")
    job.start_phase("Vérification de la sauvegarde", len(index), sum(v[0] for v in index.values()))
    problems = []
    for rel, (size, _mtime, checksum) in index.items():
        bt.check_cancel()
        path = folder / rel
        try:
            if os.stat(_long(path)).st_size != size:
                problems.append(f"Taille différente : {rel}")
            elif file_digest(path) != checksum:
                problems.append(f"Contenu altéré : {rel}")
        except FileNotFoundError:
            problems.append(f"Fichier manquant : {rel}")
        except OSError as exc:
            problems.append(f"Fichier illisible : {rel} ({exc.__class__.__name__})")
        job.advance(1, size, rel)
    report = folder / f"verification_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    report.write_text("\n".join([f"Vérification de {folder.name}", f"{len(index)} fichier(s) contrôlé(s).",
                                 f"{len(problems)} problème(s).", "", *problems]), encoding="utf-8")
    job.log(f"{'✓' if not problems else '⚠'} Vérification de {folder.name} : {len(index) - len(problems)}/"
            f"{len(index)} fichier(s) intacts.")
    return dict(partial=bool(problems), report=str(report),
                message=f"{len(problems)} fichier(s) manquant(s) ou altéré(s)." if problems else "")


# --------------------------------------------------------------------------- #
# Restauration complète
# --------------------------------------------------------------------------- #

def version_sources(folder: Path) -> list[dict]:
    summary = read_json(Path(folder) / SUMMARY) if not is_legacy_backup(Path(folder)) else None
    return (summary or {}).get("sources") or _legacy_sources(Path(folder))


def browsers_in(folder: Path) -> list[str]:
    names = []
    for source in version_sources(folder):
        kind = source["restore"].get("kind")
        name = source["restore"].get("browser") if kind == "chromium" else \
            "Mozilla Firefox" if kind == "firefox_profile" else None
        if name and name not in names and (Path(folder) / source["target"]).exists():
            names.append(name)
    return names


def restore(folder: Path, categories, job: Job, browser: str | None = None) -> dict:
    """Restaure les catégories choisies ; `browser` limite la restauration à un seul navigateur."""
    folder = Path(folder)
    log = job.log
    index = read_json(folder / INDEX, {}) if not is_legacy_backup(folder) else {}
    sources = version_sources(folder)
    if browser:
        sources = [s for s in sources if s["category"] != "browsers" or s["restore"].get("browser") == browser
                   or (browser == "Mozilla Firefox" and s["restore"].get("kind") in ("firefox_profile", "metadata"))]
    issues: list[str] = []
    stamp = f"{datetime.now():%Y-%m-%d %H-%M}"

    for category in [c for c in RESTORABLE if c in categories]:
        bt.check_cancel()
        log(f"━━ RESTAURATION : {CATEGORY_LABELS[category].upper()} ━━")
        job.start_phase(f"Restauration : {CATEGORY_LABELS[category]}")
        try:
            if category in ("files", "browsers", "email"):
                _restore_trees(folder, [s for s in sources if s["category"] == category], index, job, issues, stamp)
            elif category == "programs":
                if not bt.import_installed_programs(folder, log):
                    issues.append("Programmes : certains logiciels restent à réinstaller (voir le journal).")
            elif category == "wifi":
                bt.import_wifi_profiles(folder, log)
            elif category == "drivers":
                if not bt.import_drivers(folder, log):
                    issues.append("Pilotes : installation incomplète ou droits administrateur requis.")
        except bt.Cancelled:
            raise
        except Exception as exc:
            issues.append(f"{CATEGORY_LABELS[category]} : {exc}")
            log(f"⚠ {CATEGORY_LABELS[category]} : {exc}")
    report = None
    if issues:
        report = bt.ensure_dir(bt.get_known_folder("Documents")) / f"Restauration Sauvegarde PC {stamp}.txt"
        report.write_text("\n".join([f"Restauration depuis {folder}", f"Date : {stamp}", "", *issues]), encoding="utf-8")
    return dict(partial=bool(issues), report=str(report or ""),
                message=f"{len(issues)} point(s) à vérifier." if issues else "")


def _legacy_sources(folder: Path) -> list[dict]:
    """Reconstitue les sources d'une sauvegarde 1.10 à partir de ses dossiers."""
    sources = []
    personal = folder / "Fichiers personnels"
    if personal.is_dir():
        for child in personal.iterdir():
            if child.is_dir():
                kind = dict(kind="saved_games") if child.name == "Saved Games" else \
                    dict(kind="known_folder", name=child.name) if child.name in bt.STANDARD_FOLDERS else \
                    dict(kind="path", path="")
                sources.append(dict(category="files", target=f"Fichiers personnels/{child.name}", restore=kind))
    custom = folder / "Divers (dossiers personnalisés)"
    if custom.is_dir():
        for child in custom.iterdir():
            if child.is_dir():
                sources.append(dict(category="files", target=f"{custom.name}/{child.name}",
                                    restore=dict(kind="path", path="")))
    browsers = folder / "Navigateurs"
    if browsers.is_dir():
        for child in browsers.iterdir():
            if child.name == "Mozilla Firefox":
                for profile in child.iterdir():
                    if profile.is_dir():
                        sources.append(dict(category="browsers", target=f"Navigateurs/Mozilla Firefox/{profile.name}",
                                            restore=dict(kind="firefox_profile", name=profile.name)))
            elif child.is_dir() and child.name in bt.chromium_browsers():
                sources.append(dict(category="browsers", target=f"Navigateurs/{child.name}",
                                    restore=dict(kind="chromium", browser=child.name)))
    email = folder / "Email"
    for profile in (email / "Thunderbird").iterdir() if (email / "Thunderbird").is_dir() else []:
        if profile.is_dir():
            sources.append(dict(category="email", target=f"Email/Thunderbird/{profile.name}",
                                restore=dict(kind="thunderbird_profile", name=profile.name)))
    for pst in (email / "Outlook").glob("*.pst") if (email / "Outlook").is_dir() else []:
        sources.append(dict(category="email", target=f"Email/Outlook/{pst.name}",
                            restore=dict(kind="outlook_pst", name=pst.name)))
    return sources


def restore_destination(source: dict) -> Path | None:
    """Emplacement sur ce PC où remettre une source sauvegardée."""
    restore = source["restore"]
    kind = restore.get("kind")
    fallback = bt.USERPROFILE / "Dossiers restaurés" / Path(source["target"]).name
    if kind == "known_folder":
        return bt.get_known_folder(restore["name"])
    if kind == "saved_games":
        return bt.USERPROFILE / "Saved Games"
    if kind == "path":
        original = Path(restore.get("path") or "")
        # Disque d'origine absent (ex. D:) ou chemin inconnu : dossier de l'utilisateur.
        if restore.get("path") and original.anchor and Path(original.anchor).exists():
            return original
        return fallback
    if kind == "chromium":
        browsers = bt.chromium_browsers()
        return browsers[restore["browser"]][0] if restore["browser"] in browsers else None
    if kind == "firefox_profile":
        return bt.firefox_dir() / "Profiles" / restore["name"]
    if kind == "thunderbird_profile":
        return bt.thunderbird_dir() / "Profiles" / restore["name"]
    if kind == "outlook_pst":
        return bt.get_known_folder("Documents") / "Fichiers Outlook" / restore["name"]
    if kind == "outlook_signatures":
        return bt.outlook_signatures_dir()
    return None


WHOLE_PROFILE_KINDS = ("chromium", "firefox_profile", "thunderbird_profile", "outlook_signatures")


def _restore_trees(folder: Path, sources: list[dict], index: dict, job: Job, issues: list, stamp: str):
    log = job.log
    processes = {bt.BROWSER_PROCESSES.get(s["restore"].get("browser", ""), "") for s in sources
                 if s["restore"].get("kind") == "chromium"}
    if any(s["restore"].get("kind") == "firefox_profile" for s in sources):
        processes.add("firefox.exe")
    if any(s["restore"].get("kind") == "thunderbird_profile" for s in sources):
        processes.add("thunderbird.exe")
    if any(s["restore"].get("kind") in ("outlook_pst", "outlook_signatures") for s in sources):
        processes.add("outlook.exe")
    running = bt.running_processes([p for p in processes if p])
    if running:
        raise RuntimeError("Fermez d'abord ces programmes puis relancez la restauration : " + ", ".join(running))

    work = []
    for source in sources:
        if source["restore"].get("kind") == "metadata":
            continue
        base = folder / source["target"]
        target = restore_destination(source)
        if target is None or not base.exists():
            continue
        if source["restore"].get("kind") == "path" and not source["restore"].get("path"):
            log(f"ℹ Emplacement d'origine inconnu pour « {Path(source['target']).name} » : restauré dans {target}")
        if source["restore"].get("kind") in WHOLE_PROFILE_KINDS and target.is_dir() and any(target.iterdir()):
            # Un profil applicatif ne se fusionne pas fichier par fichier : l'actuel est mis de côté en bloc.
            kept = _unique_path(target.with_name(f"{target.name} (avant restauration {stamp})"))
            os.replace(_long(target), _long(kept))
            log(f"ℹ Profil existant conservé sous {kept}")
        files = [("", base)] if base.is_file() else [
            (str(p.relative_to(base)).replace("\\", "/"), p) for p in base.rglob("*") if p.is_file()]
        work.extend((source, target, rel, path) for rel, path in files)
    job.start_phase(job.phase, len(work), sum(p.stat().st_size for _s, _t, _r, p in work))

    restored = skipped = 0
    for source, target, rel, path in work:
        bt.check_cancel()
        dst = target / rel if rel else target
        size = path.stat().st_size
        try:
            known = index.get(source["target"] + ("/" + rel if rel else ""))
            if dst.exists():
                current = dst.stat()
                if current.st_size == size and abs(current.st_mtime - path.stat().st_mtime) < 2:
                    skipped += 1
                    job.advance(1, size, rel)
                    continue
                # Fichier différent déjà présent : conservé à côté, jamais écrasé sans copie.
                kept = _unique_path(dst.with_name(f"{dst.stem} (avant restauration {stamp}){dst.suffix}"))
                os.replace(_long(dst), _long(kept))
            os.makedirs(_long(dst.parent), exist_ok=True)
            checksum = _copy_file(path, dst, Job())
            if known and checksum != known[2]:
                issues.append(f"Fichier de sauvegarde altéré, restauré tel quel : {source['target']}/{rel}")
            restored += 1
        except OSError as exc:
            issues.append(f"Non restauré : {dst} ({exc.__class__.__name__})")
        job.advance(1, size, rel)
    log(f"✓ {restored} fichier(s) restauré(s), {skipped} déjà identique(s).")

    for app_dir, kind, saved_ini in ((bt.firefox_dir(), "firefox_profile", "Navigateurs/Mozilla Firefox/_profiles.ini"),
                                     (bt.thunderbird_dir(), "thunderbird_profile", "Email/Thunderbird/_profiles.ini")):
        names = [s["restore"]["name"] for s in sources if s["restore"].get("kind") == kind]
        if names:
            ini = folder / saved_ini
            default = bt.mozilla_default_profile(ini.read_text(encoding="utf-8", errors="replace")) if ini.is_file() else ""
            bt.register_mozilla_profiles(app_dir, names, default)
            log(f"✓ Profil(s) déclaré(s) dans {app_dir / 'profiles.ini'} : {', '.join(names)}")
    if any(s["restore"].get("kind") == "outlook_pst" for s in sources):
        log("ℹ Outlook : ouvrez les fichiers .pst restaurés dans Documents\\Fichiers Outlook "
            "(Fichier → Ouvrir et exporter → Ouvrir un fichier de données Outlook).")
    if any(s["restore"].get("kind") == "chromium" for s in sources):
        log("ℹ Navigateurs Chromium : les mots de passe enregistrés ne se déchiffrent que sur la session Windows "
            "d'origine. Après une réinstallation, utilisez la synchronisation du compte.")


def _unique_path(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} {n}{path.suffix}")
        n += 1
    return candidate
