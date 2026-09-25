"""
Lanceur intelligent pour le raccourci bureau « Sauvegarde PC ».

Avant de lancer l'exécutable compilé (dist_release/SauvegardePC.exe), compare la
date des fichiers sources (app/*.py) à celle du dernier build. Si le code a
été modifié depuis, propose de recompiler avant de lancer — pour que le
raccourci lance toujours la bonne version, sans jamais recompiler à l'insu de
l'utilisateur. L'installeur (SauvegardePC-Setup.exe) est régénéré dans la
foulée à chaque recompilation, pour ne jamais rester désynchronisé de
l'exécutable qu'il embarque.

Ce script est le nouveau point d'entrée du raccourci bureau (voir la
propriété "Cible" du raccourci : pythonw.exe check_update.py).
"""

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "app"
DIST_DIR = ROOT / "dist_release"
EXE_PATH = DIST_DIR / "SauvegardePC.exe"
BUILD_LOG = ROOT / "build_error.log"
INSTALLER_ISS = ROOT / "installer.iss"
INSTALLER_EXE = ROOT / "SauvegardePC-Setup.exe"
ISCC_PATH = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")

BUILD_CMD = [sys.executable, str(ROOT / 'scripts/build_release.py')]
INSTALLER_CMD = [str(ISCC_PATH), str(INSTALLER_ISS)]


def source_last_modified() -> float:
    return max((f.stat().st_mtime for f in APP_DIR.rglob("*") if f.is_file() and f.suffix in {".py", ".html", ".css", ".js", ".ico", ".json"}), default=0)


def ask_yes_no(title: str, message: str) -> bool:
    root = tk.Tk()
    root.withdraw()
    answer = messagebox.askyesno(title, message)
    root.destroy()
    return answer


def show_error(title: str, message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def rebuild_with_progress() -> tuple[bool, str]:
    """Recompile l'exécutable puis régénère l'installeur, avec une fenêtre
    d'attente ; renvoie (succès, sortie combinée des deux étapes)."""
    result: dict = {}

    win = tk.Tk()
    win.title("Sauvegarde PC — Mise à jour")
    win.geometry("380x120")
    win.resizable(False, False)
    status_var = tk.StringVar(value="Compilation de l'application...\nMerci de patienter (30 s à 2 min).")
    tk.Label(win, textvariable=status_var, pady=16, justify="center").pack()
    bar = ttk.Progressbar(win, mode="indeterminate", length=300)
    bar.pack(pady=8)
    bar.start(12)
    win.protocol("WM_DELETE_WINDOW", lambda: None)  # pas d'annulation en plein build

    def worker():
        ok, output = _run(BUILD_CMD)
        combined_output = output

        result["ok"] = ok
        result["output"] = combined_output
        win.after(0, win.destroy)

    threading.Thread(target=worker, daemon=True).start()
    win.mainloop()
    return result.get("ok", False), result.get("output", "")


def launch_exe() -> None:
    subprocess.Popen([str(EXE_PATH)], cwd=str(DIST_DIR))


def main() -> None:
    exe_mtime = EXE_PATH.stat().st_mtime if EXE_PATH.exists() else 0
    src_mtime = source_last_modified()
    needs_update = (not EXE_PATH.exists()) or (src_mtime > exe_mtime)

    if needs_update:
        if not EXE_PATH.exists():
            message = "L'application n'a encore jamais été compilée.\n\nLa compiler maintenant ?"
        else:
            message = (
                "Le code source de Sauvegarde PC a été modifié depuis la dernière "
                "compilation.\n\nMettre à jour l'application avant de la lancer ?"
            )
        if ask_yes_no("Mise à jour disponible", message):
            ok, output = rebuild_with_progress()
            if not ok:
                BUILD_LOG.write_text(output, encoding="utf-8")
                show_error(
                    "Échec de la mise à jour",
                    f"La compilation a échoué. Détails dans {BUILD_LOG.name}.\n"
                    "Lancement de la version précédente si disponible.",
                )

    if EXE_PATH.exists():
        launch_exe()
    else:
        show_error("Introuvable", f"Exécutable introuvable : {EXE_PATH}")


if __name__ == "__main__":
    main()
