"""
Sauvegarde automatique via le Planificateur de tâches Windows.

La tâche lance l'application sans fenêtre (`--auto-backup`) toutes les 30
minutes. Elle ne sauvegarde que si la dernière sauvegarde est plus ancienne
que l'intervalle choisi et que le disque de destination est branché : une
sauvegarde démarre donc peu après le branchement du disque.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import backup_tasks as bt

TASK_NAME = "Sauvegarde PC - sauvegarde automatique"
CHECK_MINUTES = 30
STATUS_FILE = bt.CONFIG_DIR / "sauvegarde_auto.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def task_command() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--auto-backup"])
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(pythonw if pythonw.exists() else sys.executable)
    return subprocess.list2cmdline([interpreter, str(Path(__file__).with_name("web_main.py")), "--auto-backup"])


def _schtasks(*args) -> subprocess.CompletedProcess:
    result = subprocess.run(["schtasks", *args], capture_output=True,
                            stdin=subprocess.DEVNULL, timeout=30, creationflags=NO_WINDOW)
    result.stdout, result.stderr = bt.decode_output(result.stdout), bt.decode_output(result.stderr)
    return result


def enable() -> None:
    result = _schtasks("/Create", "/F", "/TN", TASK_NAME, "/TR", task_command(),
                       "/SC", "MINUTE", "/MO", str(CHECK_MINUTES), "/RL", "LIMITED")
    if result.returncode:
        raise OSError((result.stderr or result.stdout).strip() or "Création de la tâche planifiée impossible.")


def disable() -> None:
    result = _schtasks("/Delete", "/F", "/TN", TASK_NAME)
    if result.returncode and is_enabled():
        raise OSError((result.stderr or result.stdout).strip() or "Suppression de la tâche planifiée impossible.")


def is_enabled() -> bool:
    try:
        return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def read_status() -> dict:
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(**values) -> None:
    data = dict(read_status(), **values)
    bt.ensure_dir(STATUS_FILE.parent)
    temporary = STATUS_FILE.with_name(STATUS_FILE.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, STATUS_FILE)


def last_backup_at(config: dict) -> str:
    """Dernière sauvegarde réussie, faite depuis l'interface ou automatiquement."""
    return max(config.get("last_backup_at", ""), read_status().get("last_success", ""))


def is_due(config: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    interval = timedelta(days=max(1, int(config.get("auto_backup", {}).get("interval_days", 7))))
    last = last_backup_at(config)
    try:
        return not last or now - datetime.fromisoformat(last) >= interval
    except ValueError:
        return True
