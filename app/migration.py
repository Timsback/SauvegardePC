"""
Assistant de migration : restaure une sauvegarde faite sur un ancien PC (souvent en
groupe de travail, données en local) dans la session de l'utilisateur actuellement
connecté sur un nouveau PC joint au domaine ARBJ.LOCAL, dont le dossier personnel vit
sur un lecteur réseau (NAS) avec quota par propriétaire.

S'appuie sur `backup_engine` pour tout ce qui concerne le format de sauvegarde
(`sauvegarde.json`/`fichiers.json`, `version_sources`) : ce module n'ajoute que ce qui
est spécifique à une migration — tri avant copie, choix de destination par catégorie,
favoris de navigateur uniquement, détection du domaine. Ne modifie jamais la
sauvegarde source (lecture seule) et ne copie jamais ACL ni propriétaire d'origine.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import backup_engine as engine
import backup_tasks as bt

# --------------------------------------------------------------------------- #
# 1. Détection de l'environnement (domaine, session, dossiers redirigés)
# --------------------------------------------------------------------------- #

def detect_environment() -> dict:
    """Décrit la session Windows courante : domaine, utilisateur, dossier personnel
    réseau, redirection éventuelle des dossiers connus, élévation administrateur."""
    domain = os.environ.get("USERDNSDOMAIN", "")
    userdomain = os.environ.get("USERDOMAIN", "")
    computername = os.environ.get("COMPUTERNAME", "")
    is_domain_joined = bool(domain) and userdomain.upper() != computername.upper()

    homeshare = os.environ.get("HOMESHARE", "")
    homedrive_env = os.environ.get("HOMEDRIVE", "")
    homepath = os.environ.get("HOMEPATH", "")
    network_home = homeshare or (homedrive_env + homepath if homedrive_env and homepath else "")
    if not homeshare and homedrive_env:
        # HOMEDRIVE est une lettre de lecteur (ex. « H: ») : tente de résoudre le
        # partage UNC réel derrière, pour afficher un chemin explicite à l'écran.
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if ctypes.windll.mpr.WNetGetConnectionW(homedrive_env, buf, ctypes.byref(size)) == 0:
                network_home = buf.value + homepath
        except Exception:
            pass

    redirected = {}
    for name in bt.STANDARD_FOLDERS:
        try:
            real_path = str(bt.get_known_folder(name))
            redirected[name] = {"path": real_path, "is_network": real_path.startswith("\\\\")}
        except Exception:
            redirected[name] = {"path": "", "is_network": False}

    return {
        "is_domain_joined": is_domain_joined,
        "domain": domain or userdomain,
        "username": os.environ.get("USERNAME", ""),
        "user_full": f"{userdomain}\\{os.environ.get('USERNAME', '')}" if userdomain else os.environ.get("USERNAME", ""),
        "computer_name": computername,
        "network_home": network_home,
        "known_folders": redirected,
        "is_admin": bt.is_admin(),
    }


# --------------------------------------------------------------------------- #
# 2. Lecture de la sauvegarde source (délègue le format à backup_engine)
# --------------------------------------------------------------------------- #

INSTALLER_EXTS = {".exe", ".msi"}
DISK_IMAGE_EXTS = {".iso", ".img"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".mpg", ".mpeg", ".webm"}
SHORTCUT_EXTS = {".lnk"}
JUNK_NAMES = {"desktop.ini", "thumbs.db"}
DEFAULT_LARGE_THRESHOLD = 200 * 1024 * 1024  # 200 Mo


def parse_old_pc_name(folder: Path) -> str:
    """Nom de l'ancien PC : lu dans sauvegarde.json si possible (fiable même après un
    renommage du dossier), sinon deviné depuis le nom « SauvegardePC_<PC>_<date> »."""
    summary = engine.read_json(Path(folder) / engine.SUMMARY)
    if isinstance(summary, dict) and summary.get("computer"):
        return summary["computer"]
    match = re.match(rf"{engine.VERSION_PREFIX}(.+?)_\d{{4}}-\d{{2}}-\d{{2}}", Path(folder).name)
    return match.group(1) if match else Path(folder).name


def scan_migration(folder: Path, log=lambda m: None, job: "engine.Job | None" = None) -> list[dict]:
    """Construit un enregistrement par fichier des dossiers standards (Bureau,
    Documents, Images, Téléchargements, Vidéos, Musique) de cette version de
    sauvegarde. Réutilise l'empreinte SHA-256 de `fichiers.json` quand elle est
    disponible (sauvegardes faites avec cette version ou plus récente) ; la calcule
    à la volée pour les sauvegardes plus anciennes (sans index)."""
    folder = Path(folder)
    index = engine.read_json(folder / engine.INDEX, {}) or {}
    sources = [s for s in engine.version_sources(folder)
               if s["category"] == "files" and s["restore"].get("kind") == "known_folder"]

    all_files = []
    for source in sources:
        base = folder / source["target"]
        if not base.exists():
            continue
        category = source["restore"]["name"]
        if base.is_file():
            all_files.append((category, "", base, source["target"]))
            continue
        for path in base.rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(base)).replace("\\", "/")
                all_files.append((category, rel, path, f"{source['target']}/{rel}"))

    if job:
        job.start_phase("Analyse de la sauvegarde", len(all_files))
    records = []
    for category, rel, path, index_key in all_files:
        bt.check_cancel()
        try:
            stat = path.stat()
        except OSError as exc:
            log(f"⚠ Fichier illisible ignoré : {path} ({exc.__class__.__name__})")
            continue
        known = index.get(index_key)
        sha256 = known[2] if known and known[0] == stat.st_size and known[1] == stat.st_mtime_ns else ""
        records.append({
            "category": category, "relative": rel or Path(path).name, "path": str(path),
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha256,
        })
        if job:
            job.advance(1, 0, f"{category}/{rel}")

    # Empreintes manquantes : seulement pour les fichiers qui partagent une taille
    # avec au moins un autre (un fichier de taille unique ne peut être un doublon).
    by_size: dict[int, list[dict]] = {}
    for rec in records:
        by_size.setdefault(rec["size"], []).append(rec)
    to_hash = [rec for group in by_size.values() if len(group) > 1 for rec in group if not rec["sha256"]]
    if job and to_hash:
        job.start_phase("Recherche des doublons (empreintes)", len(to_hash), sum(r["size"] for r in to_hash))
    for rec in to_hash:
        bt.check_cancel()
        try:
            rec["sha256"] = engine.file_digest(Path(rec["path"]))
        except OSError as exc:
            log(f"⚠ Empreinte impossible, exclu du dédoublonnage : {rec['path']} ({exc.__class__.__name__})")
        if job:
            job.advance(1, rec["size"], rec["relative"])
    return records


def ensure_hash(record: dict) -> str:
    if not record["sha256"]:
        record["sha256"] = engine.file_digest(Path(record["path"]))
    return record["sha256"]


# --------------------------------------------------------------------------- #
# 3. Assistant de tri : classification + dédoublonnage
# --------------------------------------------------------------------------- #

_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")


def _cleanliness_score(relative: str) -> tuple:
    """Plus petit = chemin « plus propre » à garder en cas de doublon :
    sans suffixe « (1) »/« (2) », puis le plus court."""
    stem = Path(relative).stem
    has_suffix = 1 if _SUFFIX_RE.search(stem) else 0
    return (has_suffix, len(relative), relative)


def classify_file(relative: str, size: int, large_threshold: int) -> tuple[str, bool]:
    """Renvoie (catégorie_de_tri, coché_par_défaut) pour un fichier isolé (hors
    doublons, traités séparément par `build_triage_plan`)."""
    name = Path(relative).name
    ext = Path(relative).suffix.lower()
    if name.lower() in JUNK_NAMES:
        return "inutile", False
    if ext in SHORTCUT_EXTS:
        return "raccourci", False
    if ext in INSTALLER_EXTS:
        return "installeur", False
    if ext in DISK_IMAGE_EXTS:
        return "image_disque", False
    if ext in ARCHIVE_EXTS:
        return "archive", True
    if ext in VIDEO_EXTS:
        return "video", False
    if size >= large_threshold:
        return "gros_fichier", False
    return "normal", True


def build_triage_plan(records: list[dict], large_threshold: int = DEFAULT_LARGE_THRESHOLD) -> list[dict]:
    """Construit le plan de tri : un enregistrement « décision » par fichier, avec
    la catégorie de tri, la décision par défaut (coché/décoché) et, pour les
    doublons, le fichier de référence conservé (le plus « propre »)."""
    by_hash: dict[str, list[dict]] = {}
    for rec in records:
        if rec["sha256"]:
            by_hash.setdefault(rec["sha256"], []).append(rec)

    duplicate_ids = set()
    kept_by_hash = {}
    for digest, group in by_hash.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda r: _cleanliness_score(r["relative"]))
        kept_by_hash[digest] = ordered[0]
        for rec in ordered[1:]:
            duplicate_ids.add(id(rec))

    reasons = {
        "inutile": "Fichier système sans utilité (desktop.ini/Thumbs.db).",
        "raccourci": "Raccourci (.lnk) : pointe probablement vers un ancien programme.",
        "installeur": "Installeur (.exe/.msi) : à réinstaller plutôt qu'à copier.",
        "image_disque": "Image disque (.iso) volumineuse et rarement utile après migration.",
        "archive": "Archive : vérifiez son contenu avant de l'exclure.",
        "video": "Fichier vidéo volumineux.",
        "gros_fichier": f"Fichier volumineux (≥ {bt.format_size(large_threshold)}).",
        "normal": "",
    }
    plan = []
    for rec in records:
        if id(rec) in duplicate_ids:
            kept = kept_by_hash[rec["sha256"]]
            plan.append({**rec, "tri": "doublon", "checked": False,
                        "reason": f"Doublon de « {kept['category']}/{kept['relative']} »"})
            continue
        tri, checked = classify_file(rec["relative"], rec["size"], large_threshold)
        plan.append({**rec, "tri": tri, "checked": checked, "reason": reasons[tri]})
    return plan


def plan_summary(plan: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    total_size = total_checked = 0
    for entry in plan:
        g = groups.setdefault(entry["tri"], {"count": 0, "size": 0, "checked_count": 0, "checked_size": 0})
        g["count"] += 1
        g["size"] += entry["size"]
        if entry["checked"]:
            g["checked_count"] += 1
            g["checked_size"] += entry["size"]
        total_size += entry["size"]
        if entry["checked"]:
            total_checked += entry["size"]
    return {
        "groups": groups, "total_size": total_size, "total_checked_size": total_checked,
        "total_count": len(plan), "total_checked_count": sum(1 for e in plan if e["checked"]),
    }


def estimate_space(plan: list[dict], destination: Path) -> dict:
    checked_size = sum(e["size"] for e in plan if e["checked"])
    total_size = sum(e["size"] for e in plan)
    probe = Path(destination)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free_bytes = None
    try:
        import shutil as _shutil
        free_bytes = _shutil.disk_usage(probe).free
    except OSError:
        free_bytes = None
    fits = free_bytes is None or checked_size <= free_bytes
    return {
        "total_size": total_size, "checked_size": checked_size, "free_bytes": free_bytes, "fits": fits,
        "total_size_text": bt.format_size(total_size), "checked_size_text": bt.format_size(checked_size),
        "free_bytes_text": bt.format_size(free_bytes) if free_bytes is not None else "inconnu",
    }


def export_plan_csv(plan: list[dict], path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Catégorie", "Chemin relatif", "Taille (octets)", "Tri", "Décision", "Raison"])
        for entry in plan:
            writer.writerow([entry["category"], entry["relative"], entry["size"], entry["tri"],
                             "Copier" if entry["checked"] else "Exclure", entry.get("reason", "")])
    return path


# --------------------------------------------------------------------------- #
# 4. Choix de la destination par catégorie
# --------------------------------------------------------------------------- #

DEST_KNOWN_FOLDER = "known_folder"
DEST_NETWORK_PERSONAL = "network_personal"
DEST_MANUAL = "manual"


def migration_subfolder_name(old_pc_name: str) -> str:
    return f"Migration – {old_pc_name} – {datetime.now().strftime('%Y-%m-%d')}"


def resolve_destination(category: str, mode: str, old_pc_name: str, network_home: str, manual_path: str = "") -> Path:
    if mode == DEST_KNOWN_FOLDER:
        return bt.get_known_folder(category)
    if mode == DEST_NETWORK_PERSONAL:
        if not network_home:
            raise ValueError("Aucun dossier personnel réseau détecté pour cette session.")
        return Path(network_home) / migration_subfolder_name(old_pc_name) / category
    if mode == DEST_MANUAL:
        if not manual_path:
            raise ValueError("Choisissez un dossier de destination.")
        return Path(manual_path) / category
    raise ValueError(f"Mode de destination inconnu : {mode}")


# --------------------------------------------------------------------------- #
# 5. Copie avec reprise, politique de conflit et vérification
# --------------------------------------------------------------------------- #

CONFLICT_IGNORE = "ignore"
CONFLICT_RENAME = "rename"
CONFLICT_REPLACE_IF_NEWER = "replace_if_newer"


def _unique_renamed(path: Path) -> Path:
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 1
    candidate = path
    while candidate.exists():
        candidate = parent / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def _already_copied(src: Path, dst: Path) -> bool:
    """Vrai si `dst` existe déjà avec la même taille et la même date de
    modification que `src` — permet de reprendre une migration interrompue sans
    tout recopier."""
    try:
        s, d = src.stat(), dst.stat()
    except OSError:
        return False
    return s.st_size == d.st_size and abs(s.st_mtime_ns - d.st_mtime_ns) < 2_000_000_000


def copy_plan(plan: list[dict], destinations: dict[str, Path], log, job: "engine.Job | None" = None,
              simulate: bool = False, conflict_policy: str = CONFLICT_RENAME) -> dict:
    """Copie les fichiers cochés du plan vers la destination résolue par
    catégorie. En mode simulation, ne touche à rien et journalise ce qui serait
    fait. Ne modifie jamais la sauvegarde source."""
    to_copy = [e for e in plan if e["checked"]]
    copied, skipped, errors, ignored_conflicts = [], [], [], []
    if job:
        job.start_phase("Copie" if not simulate else "Simulation de la copie",
                        len(to_copy), sum(e["size"] for e in to_copy))

    for entry in to_copy:
        bt.check_cancel()
        src = Path(entry["path"])
        dest_root = destinations.get(entry["category"])
        if dest_root is None:
            errors.append({"path": entry["relative"], "error": "Aucune destination choisie pour cette catégorie."})
            if job:
                job.advance(1, entry["size"])
            continue
        dst = dest_root / entry["relative"]
        label = f"{entry['category']}/{entry['relative']}"

        if simulate:
            action = "copierait"
            if dst.exists():
                if _already_copied(src, dst):
                    action = "ignorerait (déjà présent)"
                elif conflict_policy == CONFLICT_IGNORE:
                    action = "ignorerait (conflit)"
                elif conflict_policy == CONFLICT_RENAME:
                    action = "renommerait"
            log(f"[simulation] {action} : {label}")
            copied.append(entry)
            if job:
                job.advance(1, entry["size"], label)
            continue

        try:
            if dst.exists():
                if _already_copied(src, dst):
                    skipped.append(entry)
                    if job:
                        job.advance(1, entry["size"], label)
                    continue
                if conflict_policy == CONFLICT_IGNORE:
                    ignored_conflicts.append(entry)
                    if job:
                        job.advance(1, entry["size"], label)
                    continue
                if conflict_policy == CONFLICT_REPLACE_IF_NEWER:
                    if dst.stat().st_mtime_ns >= src.stat().st_mtime_ns:
                        ignored_conflicts.append(entry)
                        if job:
                            job.advance(1, entry["size"], label)
                        continue
                elif conflict_policy == CONFLICT_RENAME:
                    dst = _unique_renamed(dst)

            os.makedirs(engine._long(dst.parent), exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(engine._long(src), engine._long(dst))
            copied.append({**entry, "dest": str(dst)})
        except bt.Cancelled:
            raise
        except (OSError, PermissionError) as exc:
            log(f"⚠ Copie impossible : {label} ({exc.__class__.__name__})")
            errors.append({"path": entry["relative"], "error": str(exc)})
        if job:
            job.advance(1, entry["size"], label)

    log(f"{'[simulation] ' if simulate else ''}{len(copied)} fichier(s) copié(s), "
        f"{len(skipped)} déjà présent(s), {len(ignored_conflicts)} ignoré(s) (conflit), {len(errors)} erreur(s).")
    return {"copied": copied, "skipped": skipped, "ignored_conflicts": ignored_conflicts, "errors": errors}


def verify_copy(copy_result: dict, log, job: "engine.Job | None" = None) -> list[dict]:
    """Recalcule l'empreinte de chaque fichier copié et la compare à celle
    calculée depuis la source au moment du tri ; journalise les écarts."""
    targets = [e for e in copy_result["copied"] if e.get("dest")]
    if job:
        job.start_phase("Vérification", len(targets), sum(e["size"] for e in targets))
    mismatches = []
    for entry in targets:
        bt.check_cancel()
        dest = entry["dest"]
        expected = ensure_hash(entry)
        try:
            actual = engine.file_digest(Path(dest))
        except OSError as exc:
            mismatches.append({"path": entry["relative"], "error": str(exc)})
            if job:
                job.advance(1, entry["size"], entry["relative"])
            continue
        if actual != expected:
            mismatches.append({"path": entry["relative"], "error": "empreinte différente après copie"})
        if job:
            job.advance(1, entry["size"], entry["relative"])
    log(f"⚠ Vérification : {len(mismatches)} fichier(s) avec une empreinte différente après copie."
        if mismatches else "✔ Vérification : toutes les empreintes correspondent.")
    return mismatches


# --------------------------------------------------------------------------- #
# 6. Navigateurs : favoris uniquement (par défaut)
# --------------------------------------------------------------------------- #

def restore_chromium_bookmarks_only(folder: Path, browser_name: str, log) -> bool:
    """Copie uniquement le fichier « Bookmarks » (favoris) d'un profil Chrome/Edge
    sauvegardé, sans toucher aux mots de passe ni à l'historique."""
    folder = Path(folder)
    sources = [s for s in engine.version_sources(folder)
               if s["restore"].get("kind") == "chromium" and s["restore"].get("browser") == browser_name]
    dst_base = bt.chromium_browsers().get(browser_name, (None, None))[0]
    if not sources or not dst_base:
        log(f"— {browser_name} : aucune sauvegarde trouvée, favoris ignorés.")
        return False
    found = False
    for source in sources:
        src_base = folder / source["target"]
        for bookmarks in src_base.rglob("Bookmarks"):
            rel = bookmarks.relative_to(src_base)
            target = dst_base / rel
            try:
                os.makedirs(engine._long(target.parent), exist_ok=True)
                import shutil as _shutil
                _shutil.copy2(engine._long(bookmarks), engine._long(target))
                log(f"  ✓ Favoris restaurés : {rel}")
                found = True
            except OSError as exc:
                log(f"  ⚠ Favoris non restaurés pour {rel} : {exc.__class__.__name__}")
    if not found:
        log(f"— {browser_name} : aucun fichier de favoris trouvé dans la sauvegarde.")
    return found


def export_firefox_bookmarks_html(folder: Path, destination: Path, log) -> Path | None:
    """Exporte les favoris Firefox sauvegardés vers un fichier HTML standard
    (format Netscape, importable depuis n'importe quel navigateur), sans jamais
    écrire dans un profil Firefox vivant — évite tout risque de corruption de
    places.sqlite si Firefox tournait encore."""
    folder = Path(folder)
    sources = [s for s in engine.version_sources(folder) if s["restore"].get("kind") == "firefox_profile"]
    if not sources:
        log("— Firefox : aucune sauvegarde trouvée, favoris ignorés.")
        return None

    bookmarks_html = ['<!DOCTYPE NETSCAPE-Bookmark-file-1>', '<TITLE>Favoris Firefox</TITLE>', '<H1>Favoris Firefox</H1>', '<DL>']
    total = 0
    for source in sources:
        places = folder / source["target"] / "places.sqlite"
        if not places.exists():
            continue
        try:
            uri = f"file:{engine._long(places)}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute(
                    "SELECT b.title, p.url FROM moz_bookmarks b JOIN moz_places p ON p.id = b.fk "
                    "WHERE b.type = 1 AND p.url IS NOT NULL"
                ).fetchall()
            for title, url in rows:
                safe_title = (title or url or "Sans titre").replace("<", "&lt;").replace(">", "&gt;")
                bookmarks_html.append(f'<DT><A HREF="{url}">{safe_title}</A>')
                total += 1
        except sqlite3.Error as exc:
            log(f"  ⚠ Profil Firefox illisible ({source['restore'].get('name', '')}) : {exc}")
    bookmarks_html.append("</DL>")

    if total == 0:
        log("— Firefox : aucun favori trouvé dans les profils sauvegardés.")
        return None

    bt.ensure_dir(destination)
    out_path = destination / f"Favoris_Firefox_{datetime.now().strftime('%Y%m%d')}.html"
    out_path.write_text("\n".join(bookmarks_html), encoding="utf-8")
    log(f"  ✓ {total} favori(s) Firefox exporté(s) : {out_path}")
    log("  ℹ Importez ce fichier depuis Firefox : Bibliothèque > Importer et sauvegarder > Importer des favoris depuis un fichier HTML.")
    return out_path


# --------------------------------------------------------------------------- #
# 7. Outlook
# --------------------------------------------------------------------------- #

def copy_outlook_pst(folder: Path, destination: Path, log) -> list[Path]:
    folder = Path(folder)
    sources = [s for s in engine.version_sources(folder) if s["restore"].get("kind") == "outlook_pst"]
    if not sources:
        log("— Outlook : aucun fichier .pst trouvé dans la sauvegarde.")
        return []
    bt.ensure_dir(destination)
    copied = []
    import shutil as _shutil
    for source in sources:
        src = folder / source["target"]
        if not src.is_file():
            continue
        target = destination / src.name
        if target.exists():
            target = _unique_renamed(target)
        try:
            _shutil.copy2(engine._long(src), engine._long(target))
            copied.append(target)
            log(f"  ✓ Copié : {target}")
        except OSError as exc:
            log(f"  ⚠ Copie impossible pour {src.name} : {exc.__class__.__name__}")
    if copied:
        log("  ℹ Ouvrez les fichiers .pst copiés dans Outlook : Fichier > Ouvrir et exporter > Ouvrir un fichier de données Outlook.")
    return copied


# --------------------------------------------------------------------------- #
# 8. Inventaire des programmes (export propre, filtré)
# --------------------------------------------------------------------------- #

NOISE_PATTERNS = [
    re.compile(r"^KB\d+", re.I),
    re.compile(r"Microsoft Visual C\+\+.*Redistributable", re.I),
    re.compile(r"Microsoft\.WindowsAppRuntime", re.I),
    re.compile(r"Microsoft\.UI\.Xaml", re.I),
    re.compile(r"Microsoft\.VCLibs", re.I),
    re.compile(r"Mise à jour pour", re.I),
    re.compile(r"Update for Microsoft", re.I),
]


def _is_noise(name: str) -> bool:
    return any(pattern.search(name) for pattern in NOISE_PATTERNS)


def _parse_winget_list(text: str) -> list[dict]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    header = lines[0]
    sep_idx = next((i for i, line in enumerate(lines) if set(line.strip()) <= {"-", " "}), None)
    if sep_idx is None:
        return []
    name_end = header.index("Id") if "Id" in header else len(header)
    entries = []
    for line in lines[sep_idx + 1:]:
        name = line[:name_end].strip()
        rest = line[name_end:].split()
        version = rest[1] if len(rest) > 1 else (rest[0] if rest else "")
        if name:
            entries.append({"name": name, "version": version})
    return entries


def export_program_inventory(folder: Path, log) -> Path | None:
    """Lit `Programmes/programmes_liste_complete.txt` (sortie `winget list`), filtre
    le bruit (mises à jour KB, redistribuables VC++, composants
    WindowsAppRuntime/UI.Xaml/VCLibs, doublons) et exporte un CSV propre, pour
    constituer un catalogue d'applications — pas de réinstallation automatique."""
    folder = Path(folder)
    src = folder / "Programmes" / "programmes_liste_complete.txt"
    if not src.exists():
        log("— Programmes : aucun inventaire trouvé dans la sauvegarde.")
        return None
    entries = _parse_winget_list(src.read_text(encoding="utf-8", errors="replace"))
    seen = set()
    clean = []
    for entry in entries:
        if _is_noise(entry["name"]):
            continue
        key = (entry["name"].casefold(), entry["version"])
        if key in seen:
            continue
        seen.add(key)
        clean.append(entry)
    clean.sort(key=lambda e: e["name"].casefold())

    out_path = folder / "Programmes" / "inventaire_propre.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Nom", "Version"])
        for entry in clean:
            writer.writerow([entry["name"], entry["version"]])
    log(f"  ✓ Inventaire propre exporté ({len(clean)} logiciel(s)) : {out_path}")
    return out_path


# --------------------------------------------------------------------------- #
# 9. Rapport final
# --------------------------------------------------------------------------- #

def write_migration_report(destination_root: Path, lines: list[str]) -> Path:
    bt.ensure_dir(destination_root)
    path = destination_root / "rapport_migration.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
