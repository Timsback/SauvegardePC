"""Aurora desktop interface, backed by the existing backup operations."""
from __future__ import annotations

import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

import webview

import backup_tasks as bt
import software_backup as sb
import app_updates


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB = ROOT / "web"
CATEGORIES = ("files", "browsers", "licenses", "programs", "email", "wifi")
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


class BackupAPI:
    def __init__(self):
        self.config = bt.load_config()
        self.config.setdefault("selected_categories", [key for key in CATEGORIES if self.config.get("home_categories", {}).get(key, True)])
        self.config.setdefault("selected_folders", list(bt.STANDARD_FOLDERS))
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self.busy = False
        self.task = ""
        self.progress = 0
        self.progress_label = ""
        self.restore_source = ""
        self._window = None
        self.software_plan = None
        self.software_backups = []
        self.software_result = None
        self.updates = app_updates.UpdateManager()
        self.config.setdefault('auto_check_updates', True)

    def _event(self, kind, message):
        with self.lock:
            self.events.append({"kind": kind, "message": str(message)})
            self.events = self.events[-600:]

    def _log(self, message):
        self._event("log", message)

    def _progress(self, label, done, total):
        with self.lock:
            self.progress = round(done * 100 / total) if total else 0
            self.progress_label = f"{label} · {done}/{total} fichiers"

    def _destination(self):
        value = self.config.get("last_destination", "")
        if not value:
            raise ValueError("Choisissez d’abord une destination de sauvegarde.")
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pick_folder(self, initial=""):
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial)
        return str(result[0]) if result else ""

    def get_state(self):
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
            return {
                "destination": destination,
                "destination_exists": bool(destination and Path(destination).exists()),
                "capacity_free": capacity_free,
                "capacity_percent": capacity_percent,
                "last_backup_at": self.config.get("last_backup_at", ""),
                "selected_categories": self.config["selected_categories"],
                "selected_folders": self.config["selected_folders"],
                "custom_folders": self.config.get("custom_folders", []),
                "checklist": self.config.get("checklist", {}),
                "checklist_items": CHECKLIST,
                "detected_browsers": bt.detect_browsers(),
                "admin": bt.is_admin(),
                "busy": self.busy,
                "task": self.task,
                "progress": self.progress,
                "progress_label": self.progress_label,
                "restore_source": self.restore_source,
                "software_plan": self.software_plan,
                "software_backups": self.software_backups,
                "software_result": self.software_result,
                "update_info": dict(self.updates.snapshot(), enabled=self.config['auto_check_updates']),
                "restore_browsers": bt.detect_browser_backups(Path(self.restore_source)) if self.restore_source else {},
                "events": self.events[-150:],
            }

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

    def choose_destination(self):
        path = self._pick_folder(self.config.get("last_destination", ""))
        if path:
            with self.lock:
                self.config["last_destination"] = path
                bt.save_config(self.config)
        return self.get_state()

    def choose_restore_source(self):
        path = self._pick_folder(self.restore_source)
        if path:
            self.restore_source = path
            self.software_backups = sb.snapshots(path)
        return self.get_state()

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

    def add_custom_folder(self):
        path = self._pick_folder()
        if path:
            with self.lock:
                items = self.config.setdefault("custom_folders", [])
                if path not in items:
                    items.append(path)
                    bt.save_config(self.config)
        return self.get_state()

    def remove_custom_folder(self, path):
        with self.lock:
            self.config["custom_folders"] = [p for p in self.config["custom_folders"] if p != path]
            bt.save_config(self.config)
        return self.get_state()

    def set_selection(self, kind, value, enabled):
        key, allowed = ("selected_categories", CATEGORIES) if kind == "category" else ("selected_folders", bt.STANDARD_FOLDERS)
        if value not in allowed:
            raise ValueError("Sélection inconnue")
        with self.lock:
            selected = set(self.config[key])
            selected.add(value) if enabled else selected.discard(value)
            self.config[key] = [item for item in allowed if item in selected]
            bt.save_config(self.config)
        return self.get_state()

    def set_checklist(self, index, checked):
        if not 0 <= int(index) < len(CHECKLIST):
            raise ValueError("Étape inconnue")
        with self.lock:
            self.config.setdefault("checklist", {})[CHECKLIST[int(index)]] = bool(checked)
            bt.save_config(self.config)
        return self.get_state()

    def get_licenses(self):
        return bt.load_licenses(self._destination())

    def save_licenses(self, licenses):
        if not isinstance(licenses, list) or any(not isinstance(i, dict) for i in licenses):
            raise ValueError("Liste de licences invalide")
        clean = [{"logiciel": str(i.get("logiciel", ""))[:150], "cle": str(i.get("cle", ""))[:500]} for i in licenses]
        bt.save_licenses(self._destination(), clean, self._log)
        return clean

    def _run(self, label, operation, mark_backup=False):
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "Une opération est déjà en cours."}
            self.busy, self.task, self.progress, self.progress_label = True, label, 0, "Préparation…"
        self._event("start", label)

        def worker():
            try:
                result = operation()
                if mark_backup:
                    with self.lock:
                        self.config["last_backup_at"] = datetime.now().isoformat(timespec="minutes")
                        bt.save_config(self.config)
                if isinstance(result, dict) and result.get('partial'):
                    self._event('warning', f"{label} terminé avec des points à vérifier. {result.get('message', '')} Rapport : {result.get('report', '')}")
                else:
                    self._event("done", f"{label} terminé. Consultez le journal pour les éventuels fichiers ignorés.")
            except Exception as exc:
                self._event("error", f"{label} : {exc}")
            finally:
                with self.lock:
                    self.busy = False
                    self.progress = 0
                    self.progress_label = ""

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def _backup_category(self, category, dest):
        self._log(f"━━ {category.upper()} ━━")
        if category == "files":
            bt.backup_standard_folders(dest, self.config["selected_folders"], self._log, self._progress)
            bt.backup_saved_games(dest, self._log, self._progress)
            bt.backup_custom_folders(dest, self.config.get("custom_folders", []), self._log, self._progress)
        elif category == "browsers":
            for name, detected in bt.detect_browsers().items():
                if detected:
                    bt.backup_firefox(dest, self._log, self._progress) if name == "Mozilla Firefox" else bt.backup_chromium_browser(name, dest, self._log, self._progress)
        elif category == "licenses":
            key = bt.get_windows_key(self._log)
            licenses = bt.load_licenses(dest)
            if key and not any(item.get("logiciel") == "Windows OEM" for item in licenses):
                licenses.append({"logiciel": "Windows OEM", "cle": key})
            bt.save_licenses(dest, licenses, self._log)
        elif category == "programs":
            bt.export_installed_programs(dest, self._log)
        elif category == "email":
            bt.backup_email(dest, self._log, self._progress)
        elif category == "wifi":
            bt.export_wifi_profiles(dest, self._log)
            bt.export_drivers(dest, self._log)

    def start_backup(self, category="all"):
        try:
            dest = self._destination()
            chosen = list(self.config["selected_categories"]) if category == "all" else [category]
            if not chosen or any(c not in CATEGORIES for c in chosen):
                raise ValueError("Sélectionnez au moins une catégorie valide.")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        def operation():
            for item in chosen:
                self._backup_category(item, dest)
            bt.write_report(dest, ["Sauvegarde PC — Aurora", datetime.now().strftime("%d/%m/%Y %H:%M"), "", *[e["message"] for e in self.events if e["kind"] == "log"]])

        return self._run("Sauvegarde", operation, mark_backup=True)

    def restore_browser(self, name):
        if not self.restore_source:
            return {"ok": False, "error": "Choisissez d’abord le dossier de sauvegarde à restaurer."}
        if name not in bt.detect_browser_backups(Path(self.restore_source)) or not bt.detect_browser_backups(Path(self.restore_source))[name]:
            return {"ok": False, "error": "Aucune sauvegarde trouvée pour ce navigateur."}
        def operation():
            source = Path(self.restore_source)
            bt.restore_firefox(source, self._log, self._progress) if name == "Mozilla Firefox" else bt.restore_chromium_browser(name, source, self._log, self._progress)
        return self._run(f"Restauration de {name}", operation)

    def read_windows_key(self):
        return bt.get_windows_key(self._log)

    def write_report(self):
        path = bt.write_report(self._destination(), ["Sauvegarde PC — Aurora", datetime.now().strftime("%d/%m/%Y %H:%M"), "", *[e["message"] for e in self.events if e["kind"] == "log"]])
        return str(path)


def main():
    api = BackupAPI()
    window = webview.create_window("Sauvegarde PC — Aurora", (WEB / "index.html").as_uri(), js_api=api, width=1500, height=930, min_size=(1040, 700), background_color="#0a1121")
    api._window = window
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
