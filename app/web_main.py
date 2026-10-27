"""Aurora desktop interface, backed by the backup engine.

`--auto-backup` runs a scheduled backup without any window (see scheduler.py).
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

import webview

import backup_tasks as bt
import backup_engine as engine
import scheduler
import software_backup as sb
import app_updates
import vault


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB = ROOT / "web"
CATEGORIES = engine.CATEGORIES
SENSITIVE = ("licenses", "wifi")
CHECKLIST = (
    "Fichiers personnels copiés et vérifiés sur le support externe",
    "Synchronisation activée (ou export fait) pour chaque navigateur utilisé",
    "Mot de passe du compte Microsoft / Google connu et testé",
    "Clé Windows et clés logicielles notées",
    "Liste des programmes exportée (winget) et/ou logiciels non-winget notés",
    "E-mails locaux (Outlook/Thunderbird) sauvegardés",
    "Profils Wi-Fi exportés",
    "Support externe débranché et rangé en lieu sûr avant de lancer l'installation",
)


def _now():
    return datetime.now().isoformat(timespec="minutes")


def _version_view(version):
    stats = version.get("stats", {})
    return dict(name=version.get("name", ""), folder=version.get("folder", ""), created=version.get("created", ""),
                computer=version.get("computer", ""), status=version.get("status", ""), legacy=bool(version.get("legacy")),
                status_label=engine.STATUS_LABELS.get(version.get("status"), version.get("status", "")),
                files=stats.get("files", 0), size=bt.format_size(stats.get("total_bytes", 0)),
                copied=bt.format_size(stats.get("copied_bytes", 0)),
                categories=[engine.CATEGORY_LABELS.get(c, c) for c in version.get("categories", [])])


class BackupAPI:
    def __init__(self):
        self.config = bt.load_config()
        self.config.setdefault("selected_categories", [key for key in CATEGORIES if self.config.get("home_categories", {}).get(key, True)])
        self.config.setdefault("selected_folders", list(bt.STANDARD_FOLDERS))
        self.lock = threading.RLock()
        self.events: list[dict] = []
        # Unlike `events` (trimmed for the UI), these keep every line for the reports.
        self.session_log: list[str] = []
        self.operation_log: list[str] = []
        self.busy = False
        self.task = ""
        self.progress = 0
        self.progress_label = ""
        self.job = None
        self.cancelable = False
        self.restore_source = ""
        self.restore_versions = []
        self.restore_version = ""
        self.restore_categories = []
        self.restore_browsers = []
        self.restore_sensitive = {}
        # Clé du coffre de la version à restaurer, ouverte par mot de passe pour cette session seulement.
        self.restore_private = None
        self.clear_folders = 0
        self._window = None
        self.software_plan = None
        self.software_backups = []
        self.software_result = None
        self.estimate = None
        self.versions = []
        self.dest_info = {}
        self.auto_enabled = scheduler.is_enabled()
        self.auto_seen = scheduler.read_status().get("last_success", "")
        self.updates = app_updates.UpdateManager()
        self.config.setdefault('auto_check_updates', True)
        self._refresh_destination()

    def _event(self, kind, message):
        with self.lock:
            self.events.append({"kind": kind, "message": str(message)})
            self.events = self.events[-600:]

    def _log(self, message):
        with self.lock:
            self.session_log.append(str(message))
            self.operation_log.append(str(message))
        self._event("log", message)

    def _report_lines(self, lines):
        return ["Sauvegarde PC — Aurora", datetime.now().strftime("%d/%m/%Y %H:%M"), "", *lines]

    def _progress(self, label, done, total):
        with self.lock:
            self.progress = round(done * 100 / total) if total else 0
            self.progress_label = f"{label} · {done}/{total} fichiers"

    def _save(self):
        bt.save_config(self.config)

    def _destination(self):
        value = self.config.get("last_destination", "")
        if not value:
            raise ValueError("Choisissez d’abord une destination de sauvegarde.")
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _refresh_destination(self):
        """Disque, système de fichiers et versions de la destination (calculés hors du rafraîchissement)."""
        value = self.config.get("last_destination", "")
        info, versions, clear = {}, [], 0
        if value and Path(value).exists():
            info = dict(same_disk=bt.same_disk_as_system(value), filesystem=bt.filesystem_name(value),
                        hard_links=engine.supports_hard_links(Path(value)))
            versions = [_version_view(v) for v in engine.list_versions(Path(value))]
            clear = len(engine.clear_sensitive_folders(Path(value)))
        with self.lock:
            self.dest_info, self.versions, self.clear_folders = info, versions, clear

    def _pick_folder(self, initial=""):
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial)
        return str(result[0]) if result else ""

    def get_state(self):
        auto = scheduler.read_status()
        if auto.get("last_success", "") != self.auto_seen and not self.busy:
            self.auto_seen = auto.get("last_success", "")
            self._refresh_destination()
        with self.lock:
            destination = self.config.get("last_destination", "")
            capacity_free = ""
            capacity_percent = 0
            if destination and Path(destination).exists():
                try:
                    disk = shutil.disk_usage(destination)
                    capacity_free = f"{bt.format_size(disk.free)} libres sur {bt.format_size(disk.total)}"
                    capacity_percent = round(100 * disk.free / disk.total) if disk.total else 0
                except OSError:
                    pass
            progress, progress_label, progress_detail = self.progress, self.progress_label, ""
            if self.busy and self.job:
                snap = self.job.snapshot()
                progress, progress_label, progress_detail = snap["percent"], snap["label"], snap["detail"]
            return {
                "destination": destination,
                "destination_exists": bool(destination and Path(destination).exists()),
                "destination_info": self.dest_info,
                "capacity_free": capacity_free,
                "capacity_percent": capacity_percent,
                "last_backup_at": scheduler.last_backup_at(self.config),
                "selected_categories": self.config["selected_categories"],
                "selected_folders": self.config["selected_folders"],
                "custom_folders": self.config.get("custom_folders", []),
                "checklist": self.config.get("checklist", {}),
                "checklist_items": CHECKLIST,
                "detected_browsers": bt.detect_browsers(),
                "admin": bt.is_admin(),
                "busy": self.busy,
                "task": self.task,
                "progress": progress,
                "progress_label": progress_label,
                "progress_detail": progress_detail,
                "cancelable": self.busy and self.cancelable,
                "cancel_requested": self.busy and bt.CANCEL.is_set(),
                "estimate": self.estimate,
                "versions": self.versions,
                "settings": {
                    "exclusions": self.config.get("exclusions", []),
                    "max_file_size_mb": self.config.get("max_file_size_mb", 0),
                    "onedrive_cloud": self.config.get("onedrive_cloud", "skip"),
                    "keep_versions": self.config.get("keep_versions", 3),
                    "verify_after_copy": self.config.get("verify_after_copy", True),
                    "auto_backup": dict(self.config.get("auto_backup", {}), enabled=self.auto_enabled),
                    "auto_status": auto,
                },
                "restore_source": self.restore_source,
                "restore_versions": self.restore_versions,
                "restore_version": self.restore_version,
                "restore_categories": self.restore_categories,
                "restore_browsers": self.restore_browsers,
                "restore_sensitive": self.restore_sensitive,
                "restore_unlocked": self.restore_private is not None,
                "vault": {"configured": bool(self.config.get("vault")), "clear_folders": self.clear_folders},
                "software_plan": self.software_plan,
                "software_backups": self.software_backups,
                "software_result": self.software_result,
                "update_info": dict(self.updates.snapshot(), enabled=self.config['auto_check_updates']),
                "events": self.events[-150:],
            }

    # ------------------------------------------------------------------ #
    # Mises à jour
    # ------------------------------------------------------------------ #

    def check_updates(self, manual=True):
        if not manual and not self.config.get('auto_check_updates', True):
            return {'ok': True}
        self.updates.check()
        return {'ok': True}

    def set_auto_check_updates(self, enabled):
        with self.lock:
            self.config['auto_check_updates'] = bool(enabled)
            bt.save_config(self.config)
        return {'ok': True}

    def install_update(self):
        if not getattr(sys, 'frozen', False):
            return {'ok': False, 'error': 'Installez la version compilée pour utiliser la mise à jour intégrée.'}
        def operation():
            try:
                path, checksum = self.updates.download(bt.LOCALAPPDATA/'SauvegardePC'/'Updates')
                self.updates.set_state(status='installing', message='Ouverture de l’installeur. L’application va se fermer.')
                app_updates.launch_installer(path, checksum)
                self._window.destroy()
            except Exception as exc:
                self.updates.set_state(status='available', message=str(exc))
                raise
        # Same lock as backups/restores: no installer can start while they are running.
        return self._run('Mise à jour de Sauvegarde PC', operation)

    # ------------------------------------------------------------------ #
    # Destination, sélection et réglages
    # ------------------------------------------------------------------ #

    def choose_destination(self):
        path = self._pick_folder(self.config.get("last_destination", ""))
        if path:
            with self.lock:
                self.config["last_destination"] = path
                self.estimate = None
                self._save()
            self._refresh_destination()
        return self.get_state()

    def choose_restore_source(self):
        path = self._pick_folder(self.restore_source)
        if path:
            versions = [_version_view(v) for v in engine.resolve_restore_source(Path(path))]
            with self.lock:
                self.restore_source = path
                self.restore_versions = versions
                self.software_backups = sb.snapshots(path)
            self.set_restore_version(versions[0]["folder"] if versions else "")
        return self.get_state()

    def set_restore_version(self, folder):
        if folder and folder not in [v["folder"] for v in self.restore_versions]:
            raise ValueError("Version inconnue")
        with self.lock:
            self.restore_version = folder
            self.restore_categories = engine.detect_categories(Path(folder)) if folder else []
            self.restore_browsers = engine.browsers_in(Path(folder)) if folder else []
            self.restore_sensitive = engine.sensitive_state(Path(folder)) if folder else {}
            self.restore_private = None
        return self.get_state()

    def add_custom_folder(self):
        path = self._pick_folder()
        if path:
            with self.lock:
                items = self.config.setdefault("custom_folders", [])
                if path not in items:
                    items.append(path)
                    self.estimate = None
                    self._save()
        return self.get_state()

    def remove_custom_folder(self, path):
        with self.lock:
            self.config["custom_folders"] = [p for p in self.config["custom_folders"] if p != path]
            self.estimate = None
            self._save()
        return self.get_state()

    def set_selection(self, kind, value, enabled):
        key, allowed = ("selected_categories", CATEGORIES) if kind == "category" else ("selected_folders", bt.STANDARD_FOLDERS)
        if value not in allowed:
            raise ValueError("Sélection inconnue")
        with self.lock:
            selected = set(self.config[key])
            selected.add(value) if enabled else selected.discard(value)
            self.config[key] = [item for item in allowed if item in selected]
            self.estimate = None
            self._save()
        return self.get_state()

    def set_checklist(self, index, checked):
        if not 0 <= int(index) < len(CHECKLIST):
            raise ValueError("Étape inconnue")
        with self.lock:
            self.config.setdefault("checklist", {})[CHECKLIST[int(index)]] = bool(checked)
            self._save()
        return self.get_state()

    def set_setting(self, name, value):
        if name == "max_file_size_mb":
            value = int(value or 0)
            if not 0 <= value <= 10_000_000:
                raise ValueError("Taille maximale invalide")
        elif name == "keep_versions":
            value = int(value)
            if not 1 <= value <= 30:
                raise ValueError("Choisissez entre 1 et 30 versions.")
        elif name == "onedrive_cloud":
            if value not in ("skip", "download"):
                raise ValueError("Option OneDrive inconnue")
        elif name == "verify_after_copy":
            value = bool(value)
        else:
            raise ValueError("Réglage inconnu")
        with self.lock:
            self.config[name] = value
            if name in ("max_file_size_mb", "onedrive_cloud"):
                self.estimate = None
            self._save()
        return self.get_state()

    def add_exclusion(self, pattern):
        pattern = str(pattern or "").strip()
        if not pattern or len(pattern) > 200 or any(c in pattern for c in '\\/:"<>|'):
            raise ValueError("Saisissez un nom ou un motif (ex. *.iso, node_modules), sans chemin.")
        with self.lock:
            items = self.config.setdefault("exclusions", [])
            if pattern.casefold() not in [p.casefold() for p in items]:
                items.append(pattern)
                self.estimate = None
                self._save()
        return self.get_state()

    def remove_exclusion(self, pattern):
        with self.lock:
            self.config["exclusions"] = [p for p in self.config.get("exclusions", []) if p != pattern]
            self.estimate = None
            self._save()
        return self.get_state()

    def set_auto_backup(self, enabled, interval_days=7):
        interval = int(interval_days)
        if interval not in (1, 7):
            raise ValueError("Intervalle inconnu")
        if enabled and not self.config.get("last_destination"):
            raise ValueError("Choisissez d’abord une destination de sauvegarde.")
        if enabled:
            scheduler.enable()
        else:
            scheduler.disable()
        with self.lock:
            self.config["auto_backup"] = {"enabled": bool(enabled), "interval_days": interval}
            self.auto_enabled = scheduler.is_enabled()
            self._save()
        return self.get_state()

    def relaunch_admin(self):
        with self.lock:
            if self.busy:
                return {'ok': False, 'error': 'Attendez la fin de l’opération en cours.'}
        if not bt.relaunch_as_admin():
            return {'ok': False, 'error': 'Élévation refusée : l’application continue sans droits administrateur.'}
        self._window.destroy()
        return {'ok': True}

    def open_folder(self, path):
        """Ouvre un dossier de sauvegarde connu dans l’Explorateur (jamais un chemin arbitraire)."""
        allowed = [v["folder"] for v in self.versions + self.restore_versions]
        target = Path(path)
        if str(target) not in allowed and str(target.parent) not in allowed:
            raise ValueError("Dossier inconnu")
        os.startfile(str(target if target.exists() else target.parent))
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # Logiciels
    # ------------------------------------------------------------------ #

    def list_software(self):
        return sb.installed()

    def analyze_software(self, identity):
        def operation():
            with self.lock:
                self.software_plan = None
            program = next((p for p in sb.installed() if p['id'] == identity), None)
            if program is None:
                raise ValueError('Logiciel introuvable. Actualisez la liste.')
            plan = sb.analyze(program, self._log)
            with self.lock:
                self.software_plan = plan
            self._log(f"Analyse de {program['name']} : {len(plan['files'])} dossiers et {len(plan['registry'])} clés utilisateur.")
        return self._run('Analyse du logiciel', operation)

    def backup_software(self, identity=None):
        try:
            with self.lock:
                plan = self.software_plan
            if not identity and not plan:
                raise ValueError('Sélectionnez un logiciel à sauvegarder.')
            destination = self._destination()
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        def operation():
            selected = plan
            if identity:
                program = next((p for p in sb.installed() if p['id'] == identity), None)
                if program is None:
                    raise ValueError('Logiciel introuvable. Actualisez la liste.')
                selected = sb.analyze(program, self._log)
                with self.lock:
                    self.software_plan = selected
            result = sb.backup(selected, destination, self._log, self._progress)
            with self.lock:
                self.software_result = result
            return result
        return self._run('Sauvegarde du logiciel', operation, mark_backup=True)

    def restore_software(self, identity):
        if identity not in [b['id'] for b in self.software_backups]:
            return {'ok': False, 'error': 'Sélectionnez une sauvegarde dans le dossier choisi.'}
        def operation():
            result = sb.restore(identity, self._log, self._progress)
            with self.lock:
                self.software_result = result
            return result
        return self._run('Installation et restauration du logiciel', operation)

    def choose_software_installer(self, identity=None):
        with self.lock:
            if self.busy:
                return {'ok': False, 'error': 'Une opération est déjà en cours.'}
            if identity:
                if identity not in [b['id'] for b in self.software_backups]:
                    return {'ok': False, 'error': 'Sélectionnez une sauvegarde dans le dossier choisi.'}
            elif not self.software_plan or self.software_plan.get('engine') != 2:
                return {'ok': False, 'error': 'Analysez d’abord le logiciel.'}
            plan = self.software_plan
        selected = self._window.create_file_dialog(webview.FileDialog.OPEN,
            allow_multiple=False, file_types=('Installeurs Windows (*.msi;*.exe)',))
        if not selected:
            return {'cancelled': True}
        def operation():
            from software_engine import supplied_installer, attach_installer
            if identity:
                result = attach_installer(identity, selected[0])
                with self.lock:
                    self.software_backups = sb.snapshots(self.restore_source)
                self._log('Installeur copié dans la sauvegarde. Elle peut être transférée sur le nouveau PC.')
                return result
            installer = supplied_installer(plan['program'], selected[0])
            with self.lock:
                if self.software_plan is not plan:
                    raise ValueError('Le logiciel sélectionné a changé. Recommencez l’ajout de l’installeur.')
                self.software_plan = dict(plan, installer=installer)
            self._log('Installeur sélectionné. Il sera inclus lors de la prochaine sauvegarde.')
        return self._run('Ajout de l’installeur', operation)

    # ------------------------------------------------------------------ #
    # Licences
    # ------------------------------------------------------------------ #

    def get_licenses(self):
        destination = self.config.get("last_destination", "")
        return engine.local_licenses(Path(destination) if destination else None)

    def save_licenses(self, licenses):
        if not isinstance(licenses, list) or any(not isinstance(i, dict) for i in licenses):
            raise ValueError("Liste de licences invalide")
        clean = [{"logiciel": str(i.get("logiciel", ""))[:150], "cle": str(i.get("cle", ""))[:500]} for i in licenses]
        vault.save_local_licenses(engine.local_licenses_path(), clean)
        self._log(f"✓ {len(clean)} licence(s) enregistrée(s) sur ce PC (protégées par votre session Windows).")
        return clean

    def read_windows_key(self):
        return bt.get_windows_key(self._log)

    # ------------------------------------------------------------------ #
    # Protection des données sensibles (licences, Wi-Fi)
    # ------------------------------------------------------------------ #

    def setup_vault(self, password):
        if self.config.get("vault"):
            return {"ok": False, "error": "Le mot de passe de protection existe déjà."}
        try:
            doc, recovery = vault.create(str(password or ""))
        except vault.VaultError as exc:
            return {"ok": False, "error": str(exc)}
        with self.lock:
            self.config["vault"] = doc
            self._save()
        self._log("✓ Protection des données sensibles activée.")
        return {"ok": True, "recovery": recovery}

    def change_vault_password(self, secret, new_password):
        doc = self.config.get("vault")
        if not doc:
            return {"ok": False, "error": "Aucun mot de passe de protection n'est encore créé."}
        try:
            changed = vault.change_password(doc, str(secret or ""), str(new_password or ""))
        except vault.VaultError as exc:
            return {"ok": False, "error": str(exc)}
        with self.lock:
            self.config["vault"] = changed
            self._save()
        # Les versions déjà sauvegardées s'ouvrent aussi avec le nouveau mot de passe.
        destination = self.config.get("last_destination", "")
        if destination and Path(destination).exists():
            for version in engine.list_versions(Path(destination)):
                current = vault.read_vault(Path(version["folder"]))
                if current and current.get("public_key") == changed["public_key"]:
                    vault.write_vault(Path(version["folder"]) / vault.VAULT_FILE, changed)
        self._log("✓ Mot de passe de protection modifié. La clé de secours reste valable.")
        return {"ok": True}

    def unlock_restore(self, secret):
        if not self.restore_version:
            return {"ok": False, "error": "Choisissez d’abord la sauvegarde à restaurer."}
        doc = vault.read_vault(Path(self.restore_version)) or self.config.get("vault")
        try:
            private = vault.unlock(doc, str(secret or ""))
        except vault.VaultError as exc:
            return {"ok": False, "error": str(exc)}
        with self.lock:
            self.restore_private = private
            if not self.config.get("vault"):
                # Nouveau PC : il reprend le même mot de passe pour ses prochaines sauvegardes.
                self.config["vault"] = doc
                self._save()
        return {"ok": True}

    def read_restore_licenses(self):
        if self.restore_sensitive.get("licenses") == "encrypted" and self.restore_private is None:
            return {"ok": False, "code": "secret_required", "error": "Mot de passe requis."}
        try:
            licenses = engine.read_licenses(Path(self.restore_version), self.restore_private)
        except (vault.VaultError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "licenses": licenses}

    def import_restore_licenses(self):
        result = self.read_restore_licenses()
        if not result.get("ok"):
            return result
        known = self.get_licenses()
        added = [item for item in result["licenses"] if item not in known]
        vault.save_local_licenses(engine.local_licenses_path(), known + added)
        self._log(f"✓ {len(added)} licence(s) ajoutée(s) à la liste de ce PC.")
        return {"ok": True, "added": len(added)}

    def protect_old_backups(self):
        doc = self.config.get("vault")
        try:
            dest = self._destination()
            if not doc:
                raise ValueError("Créez d’abord le mot de passe de protection.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return self._run("Protection des anciennes sauvegardes",
                         lambda: engine.protect_existing(dest, doc, self._new_job()), cancelable=True)

    # ------------------------------------------------------------------ #
    # Opérations longues
    # ------------------------------------------------------------------ #

    def _run(self, label, operation, mark_backup=False, cancelable=False):
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "Une opération est déjà en cours."}
            self.busy, self.task, self.progress, self.progress_label = True, label, 0, "Préparation…"
            self.job, self.cancelable = None, cancelable
            self.operation_log = []
            bt.CANCEL.clear()
        self._event("start", label)

        def worker():
            # The mutex is per thread: acquired and released here, around the operation only.
            lock = bt.OperationLock()
            try:
                if not lock.acquire():
                    raise RuntimeError("Une sauvegarde automatique est en cours. Réessayez dans quelques minutes.")
                result = operation()
                if mark_backup:
                    with self.lock:
                        self.config["last_backup_at"] = _now()
                        self._save()
                if isinstance(result, dict) and result.get('partial'):
                    self._event('warning', f"{label} terminé avec des points à vérifier. {result.get('message', '')} Rapport : {result.get('report', '')}")
                else:
                    self._event("done", f"{label} terminé. Consultez le journal pour les éventuels fichiers ignorés.")
            except bt.Cancelled:
                self._event("warning", f"{label} annulé.")
            except Exception as exc:
                self._event("error", f"{label} : {exc}")
            finally:
                lock.release()
                bt.CANCEL.clear()
                with self.lock:
                    self.busy = False
                    self.job = None
                    self.progress = 0
                    self.progress_label = ""
                self._refresh_destination()

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def cancel_operation(self):
        with self.lock:
            if not (self.busy and self.cancelable):
                return {"ok": False, "error": "Aucune opération annulable en cours."}
            bt.CANCEL.set()
        self._log("Annulation demandée…")
        return {"ok": True}

    def _new_job(self):
        job = engine.Job(self._log)
        with self.lock:
            self.job = job
        return job

    def start_backup(self, category="all"):
        try:
            dest = self._destination()
            chosen = list(self.config["selected_categories"]) if category == "all" else [category]
            if not chosen or any(c not in CATEGORIES for c in chosen):
                raise ValueError("Sélectionnez au moins une catégorie valide.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if any(c in SENSITIVE for c in chosen) and not self.config.get("vault"):
            return {"ok": False, "code": "vault_required",
                    "error": "Créez d’abord le mot de passe qui protège vos licences et mots de passe Wi-Fi."}

        def operation():
            with self.lock:
                config = dict(self.config)
            result = engine.run_backup(dest, config, chosen, self._new_job(), app_updates.VERSION)
            with self.lock:
                lines = list(self.operation_log)
            # Complete log of this backup only, next to the version summary report.
            (Path(result["folder"]) / "journal.txt").write_text("\n".join(self._report_lines(lines)), encoding="utf-8")
            with self.lock:
                self.estimate = None
            return result

        return self._run("Sauvegarde", operation, mark_backup=True, cancelable=True)

    def estimate_backup(self):
        try:
            dest = self._destination()
            chosen = list(self.config["selected_categories"])
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        def operation():
            with self.lock:
                config = dict(self.config)
            job = self._new_job()
            job.start_phase("Estimation de la taille")
            result = engine.estimate(dest, config, chosen, job, self._log)
            with self.lock:
                self.estimate = dict(result, total=bt.format_size(result["total_bytes"]),
                                     to_copy=bt.format_size(result["to_copy_bytes"]), free=bt.format_size(result["free_bytes"]),
                                     per_category={k: dict(v, size=bt.format_size(v["bytes"])) for k, v in result["per_category"].items()})
            self._log(f"Estimation : {self.estimate['total']} sélectionnés, {self.estimate['to_copy']} à copier, {self.estimate['free']} libres.")
        return self._run("Estimation", operation, cancelable=True)

    def verify_version(self, folder):
        if folder not in [v["folder"] for v in self.versions if not v["legacy"]]:
            return {"ok": False, "error": "Version inconnue."}
        return self._run("Vérification de la sauvegarde", lambda: engine.verify_version(Path(folder), self._new_job()),
                         cancelable=True)

    def restore_full(self, categories):
        if not self.restore_version:
            return {"ok": False, "error": "Choisissez d’abord le dossier de sauvegarde à restaurer."}
        chosen = [c for c in categories if c in self.restore_categories]
        if not chosen:
            return {"ok": False, "error": "Sélectionnez au moins un élément présent dans cette sauvegarde."}
        if "wifi" in chosen and self.restore_sensitive.get("wifi") == "encrypted" and self.restore_private is None:
            return {"ok": False, "code": "secret_required", "error": "Mot de passe requis pour restaurer le Wi-Fi."}
        folder, private = Path(self.restore_version), self.restore_private
        return self._run("Restauration", lambda: engine.restore(folder, chosen, self._new_job(), private=private),
                         cancelable=True)

    def restore_browser(self, name):
        if not self.restore_version:
            return {"ok": False, "error": "Choisissez d’abord le dossier de sauvegarde à restaurer."}
        if name not in self.restore_browsers:
            return {"ok": False, "error": "Aucune sauvegarde trouvée pour ce navigateur."}
        folder = Path(self.restore_version)
        return self._run(f"Restauration de {name}",
                         lambda: engine.restore(folder, ["browsers"], self._new_job(), browser=name), cancelable=True)

    def write_report(self):
        with self.lock:
            lines = list(self.session_log)
        path = bt.write_report(self._destination(), self._report_lines(lines))
        return str(path)


def auto_backup_main() -> int:
    """Scheduled run: back up only when enabled, due and the destination is plugged in."""
    config = bt.load_config()
    destination = config.get("last_destination", "")
    if not config.get("auto_backup", {}).get("enabled") or not destination or not Path(destination).exists():
        return 0
    if not scheduler.is_due(config):
        return 0
    lock = bt.OperationLock()
    if not lock.acquire():
        return 0
    lines: list[str] = []
    try:
        scheduler.write_status(last_attempt=_now())
        categories = [c for c in config.get("selected_categories", CATEGORIES) if c in CATEGORIES]
        result = engine.run_backup(Path(destination), config, categories, engine.Job(lines.append), app_updates.VERSION)
        (Path(result["folder"]) / "journal.txt").write_text("\n".join(lines), encoding="utf-8")
        scheduler.write_status(last_success=_now(), last_result="partial" if result["partial"] else "complete",
                               last_message=result.get("message", ""), last_folder=result["folder"])
        return 0
    except Exception as exc:
        scheduler.write_status(last_result="error", last_message=str(exc))
        return 1
    finally:
        lock.release()


def main():
    if "--auto-backup" in sys.argv:
        sys.exit(auto_backup_main())
    api = BackupAPI()
    window = webview.create_window("Sauvegarde PC — Aurora", (WEB / "index.html").as_uri(), js_api=api, width=1500, height=930, min_size=(1040, 700), background_color="#0a1121")
    api._window = window
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
