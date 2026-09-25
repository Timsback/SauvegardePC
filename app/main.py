"""
Sauvegarde PC — application graphique pour préparer une réinstallation de Windows.

Reprend les étapes du guide « Sauvegarder son PC avant une réinstallation de
Windows » : fichiers personnels, navigateurs, licences, programmes, e-mail,
Wi-Fi, pilotes/divers et checklist finale.

Interface (ttkbootstrap, thème sombre "telamon" inspiré des cleaners desktop
modernes) : barre latérale groupée, cartes, jauge circulaire et bascules.

Lancement : python main.py
"""

import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import ttkbootstrap as ttkb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, TOP, X, Y, W, E, NW

import backup_tasks as bt
import design_system as ds
import aurora_home

APP_VERSION = "1.4"
APP_THEME = ds.APP_THEME
SIDEBAR_WIDTH = 218
FONT_FAMILY = ds.Font.FAMILY

# ---------------------------------------------------------------------------
# Thème et palette : entièrement définis dans design_system.py (Design System
# "Aurora" — sombre, bleu nuit, accent bleu électrique). Les constantes
# ci-dessous ne font que pointer vers ds.Color/ds.Font : le reste de ce
# fichier (sidebar, topbar, console, pages) n'a pas besoin d'être réécrit pour
# hériter de la palette — changer une couleur dans design_system.py suffit à
# faire évoluer toute l'application de façon cohérente.
# ---------------------------------------------------------------------------
BG_APP = ds.Color.BG_APP
BG_SIDEBAR = ds.Color.BG_SIDEBAR
BG_CARD = ds.Color.BG_CARD
BG_CARD_SOFT = ds.Color.BG_CARD_SOFT
BORDER_SOFT = ds.Color.BORDER_DEFAULT
TEXT_MUTED = ds.Color.TEXT_SECONDARY
CONSOLE_BG = ds.Color.BG_CONSOLE
CONSOLE_FG = ds.Color.CYAN

CHECKLIST_ITEMS = [
    "Fichiers personnels copiés et vérifiés sur le support externe",
    "Synchronisation activée (ou export fait) pour chaque navigateur utilisé",
    "Mot de passe du compte Microsoft / Google connu et testé",
    "Clé Windows et clés logicielles notées",
    "Liste des programmes exportée (winget) et/ou logiciels non-winget notés",
    "E-mails locaux (Outlook/Thunderbird) sauvegardés",
    "Profils Wi-Fi exportés",
    "Support externe débranché et rangé en lieu sûr avant de lancer l'installation",
]

# Barre latérale groupée par thème (comme les sections "Protection /
# Optimization / Memory" des cleaners desktop) : un premier item autonome
# (Accueil) puis des groupes avec un intitulé de section.
NAV_GROUPS = [
    (None, [("home", "⌂", "Accueil")]),
    ("Sauvegarde", [
        ("files", "▣", "Fichiers & divers"),
        ("browsers", "◎", "Navigateurs"),
        ("email", "✉", "E-mail"),
    ]),
    ("Sécurité", [
        ("licenses", "⌘", "Licences"),
        ("wifi", "◫", "Wi-Fi & Pilotes"),
    ]),
    ("Suivi", [
        ("programs", "▤", "Programmes"),
        ("checklist", "✓", "Checklist finale"),
    ]),
]
NAV_ITEMS = [item for _, items in NAV_GROUPS for item in items]
NAV_LABELS = {key: (icon, label) for key, icon, label in NAV_ITEMS}

BROWSER_ICONS = {
    "Google Chrome": "🟢",
    "Microsoft Edge": "🔵",
    "Mozilla Firefox": "🟠",
}


def _resource_path(*parts: str) -> Path:
    """Chemin vers un fichier embarqué (ex. app/assets/...), qu'on tourne depuis
    les sources ou depuis l'exécutable PyInstaller (données extraites dans
    sys._MEIPASS en mode --onefile)."""
    base = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
    return base.joinpath(*parts)


def _bind_click(widget, callback):
    """Rend un widget (et ses enfants directs) cliquable — pratique pour les
    cartes de navigation qui doivent réagir au clic sur n'importe lequel de
    leurs éléments internes (icône, titre, description)."""
    widget.configure(cursor="hand2")
    widget.bind("<Button-1>", lambda _e: callback())
    for child in widget.winfo_children():
        _bind_click(child, callback)


class NavOrb(tk.Canvas):
    """Pastille de navigation lisible, sans les boutons rectangulaires classiques.

    Le Canvas est volontairement isolé de tout contenu défilant : il ne dépend
    pas du redimensionnement de la page et reste donc fluide et stable.
    """

    def __init__(self, parent, icon: str, label: str, command):
        super().__init__(
            parent, width=190, height=46, bg=BG_SIDEBAR, highlightthickness=0,
            bd=0, cursor="hand2", takefocus=1,
        )
        self.icon = icon
        self.label = label
        self.command = command
        self.active = False
        self.hovered = False
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Return>", lambda _event: self.command())
        self.bind("<space>", lambda _event: self.command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    def _on_enter(self, _event):
        self.hovered = True
        self._draw()

    def _on_leave(self, _event):
        self.hovered = False
        self._draw()

    def set_active(self, active: bool):
        self.active = active
        self._draw()

    def _draw(self):
        self.delete("all")
        if self.active:
            fill, outline, fg = ds.Color.ACCENT_SOFT, ds.Color.ACCENT_SOFT, ds.Color.TEXT_PRIMARY
        elif self.hovered:
            fill, outline, fg = ds.Color.BG_CARD_SOFT, ds.Color.BORDER_DEFAULT, ds.Color.TEXT_PRIMARY
        else:
            fill, outline, fg = BG_SIDEBAR, BG_SIDEBAR, ds.Color.TEXT_SECONDARY
        ds.draw_rounded_rect(self, 3, 3, 187, 43, 7, fill=fill, outline=outline, width=1)
        if self.active:
            self.create_rectangle(3, 6, 6, 40, fill=ds.Color.CYAN, outline="")
        self.create_text(35, 23, text=self.icon, font=("Segoe UI Symbol", 15), fill=fg)
        self.create_text(56, 23, text=self.label, anchor="w", font=(FONT_FAMILY, 10), fill=fg)


class App(ttkb.Window):
    def __init__(self):
        super().__init__(
            title="Sauvegarde PC — Centre de protection", themename=APP_THEME, size=(1200, 820),
            minsize=(1024, 700), iconphoto=str(_resource_path("assets", "icon.ico")),
        )
        ds.Font.resolve()
        self.colors = self.style.colors

        self.config_data = bt.load_config()
        self.dest_var = tk.StringVar(value=self.config_data.get("last_destination", ""))
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.progress_queue: queue.Queue[tuple[str, int, int]] = queue.Queue()
        # Un thread de travail ne doit jamais appeler Tkinter directement.
        # Cette file est consommée par la boucle graphique dans _poll_log_queue.
        self.ui_queue: queue.Queue = queue.Queue()
        self.pages: dict[str, ttkb.Frame] = {}
        self.built_pages: set[str] = set()
        self.nav_buttons: dict[str, NavOrb] = {}
        self.active_tasks = 0
        self._stats_request_id = 0
        self.windows_oem_key = ""

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_sidebar()
        self._build_topbar()
        self._build_content()
        self._build_console()

        self.dest_var.trace_add("write", self._on_destination_changed)
        self._select_page("home")
        self._refresh_dashboard()
        self._poll_log_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._leave_fullscreen)
        # La fenêtre est d'abord construite puis maximisée au prochain tour de
        # boucle : cela évite un premier rendu tronqué sur les petits écrans.
        self.after_idle(self._maximize_window)

    def _maximize_window(self):
        try:
            self.state("zoomed")
        except tk.TclError:
            pass

    def _toggle_fullscreen(self, _event=None):
        self.attributes("-fullscreen", not bool(self.attributes("-fullscreen")))
        return "break"

    def _leave_fullscreen(self, _event=None):
        if bool(self.attributes("-fullscreen")):
            self.attributes("-fullscreen", False)
        return "break"

    def _on_close(self):
        # os._exit garantit l'arrêt réel du processus (y compris les threads de fond en
        # cours) : fermer avec la croix ne doit jamais laisser une instance zombie qui
        # bloquerait un futur remplacement/mise à jour de l'exécutable.
        self.destroy()
        os._exit(0)

    # ------------------------------------------------------------- Sidebar --
    def _build_sidebar(self):
        shell = tk.Frame(self, bg=BG_SIDEBAR, width=SIDEBAR_WIDTH + 2)
        shell.grid(row=0, column=0, rowspan=3, sticky="ns")
        shell.grid_propagate(False)

        sidebar = tk.Frame(shell, bg=BG_SIDEBAR, width=SIDEBAR_WIDTH)
        sidebar.pack(side=LEFT, fill=BOTH, expand=True)
        sidebar.pack_propagate(False)

        edge = tk.Canvas(shell, width=2, bg=ds.Color.BORDER_SUBTLE, highlightthickness=0, bd=0)
        edge.pack(side=RIGHT, fill=Y)

        header_wrap = tk.Canvas(sidebar, bg=BG_SIDEBAR, highlightthickness=0, bd=0, height=118)
        header_wrap.pack(fill=X)
        def _draw_header(event):
            header_wrap.delete("all")
            y = 30
            header_wrap.create_text(18, y, text="◢", font=(FONT_FAMILY, 28, "bold"), fill=ds.Color.CYAN, anchor="nw")
            header_wrap.create_text(
                65, y + 1, text="A U R O R A", font=(FONT_FAMILY, 12, "bold"), fill=self.colors.fg, anchor="nw"
            )
            header_wrap.create_text(
                66, y + 24, text="G R I D", font=(FONT_FAMILY, 8), fill=TEXT_MUTED, anchor="nw"
            )

        header_wrap.bind("<Configure>", _draw_header)

        ttkb.Separator(sidebar, bootstyle="secondary").pack(fill=X, padx=20, pady=(0, 10))

        for group_index, (group_title, items) in enumerate(NAV_GROUPS):
            if group_index:
                ttkb.Separator(sidebar, bootstyle="secondary").pack(fill=X, padx=24, pady=(10, 7))
            if group_title:
                tk.Label(
                    sidebar, text=group_title.upper(), font=(FONT_FAMILY, 7, "bold"),
                    bg=BG_SIDEBAR, fg=TEXT_MUTED,
                ).pack(anchor=W, padx=20, pady=(0, 3))
            for key, icon, label in items:
                self._build_nav_item(sidebar, key, icon, label)

        tk.Frame(sidebar, bg=BG_SIDEBAR).pack(fill=BOTH, expand=True)  # espace flexible

        self.admin_footer = tk.Label(
            sidebar, text="", bg=BG_SIDEBAR, fg=TEXT_MUTED, font=(FONT_FAMILY, 8),
            justify=LEFT, wraplength=SIDEBAR_WIDTH - 36,
        )
        self.admin_footer.pack(fill=X, padx=18, pady=16, side="bottom")
        self._refresh_admin_status()

        aurora = tk.Canvas(sidebar, bg=BG_SIDEBAR, height=154, bd=0,
                           highlightthickness=0)
        aurora.pack(side="bottom", fill=X)

        def _draw_sidebar_aurora(event):
            import math
            aurora.delete("all")
            width, height = event.width, event.height
            for layer in range(34, 0, -1):
                tint = ds.lerp_color(BG_SIDEBAR, "#3269D8", (35 - layer) / 110)
                points = []
                for x in range(0, width + 6, 6):
                    y = height - 31 - 28 * math.sin(x / max(width, 1) * math.pi * 1.35)
                    y += layer * 1.25
                    points.extend((x, y))
                aurora.create_line(*points, fill=tint, width=2, smooth=True)
            for offset, color in ((0, "#44D9F4"), (13, "#987AFF")):
                points = []
                for x in range(0, width + 6, 6):
                    y = height - 31 - 28 * math.sin(x / max(width, 1) * math.pi * 1.35)
                    points.extend((x, y + offset))
                aurora.create_line(*points, fill=color, width=1, smooth=True)
            aurora.create_text(19, 21, text="VOS DONNÉES", anchor="nw",
                               font=(FONT_FAMILY, 8, "bold"), fill="#A8C6F7")
            aurora.create_text(19, 42, text="AUJOURD’HUI.", anchor="nw",
                               font=(FONT_FAMILY, 8), fill="#A8C6F7")
            aurora.create_text(19, 63, text="TOUJOURS DEMAIN.", anchor="nw",
                               font=(FONT_FAMILY, 8), fill="#A8C6F7")

        aurora.bind("<Configure>", _draw_sidebar_aurora)

    def _build_nav_item(self, sidebar, key, icon, label):
        row = tk.Frame(sidebar, bg=BG_SIDEBAR)
        row.pack(fill=X, padx=14, pady=1)
        orb = NavOrb(row, icon, label, command=lambda k=key: self._select_page(k))
        orb.pack()
        self.nav_buttons[key] = orb

    def _refresh_admin_status(self):
        if bt.is_admin():
            self.admin_footer.configure(text="● Session administrateur")
        else:
            self.admin_footer.configure(text="⚠ Accès standard\nWi-Fi / pilotes limités")

    # -------------------------------------------------------------- Topbar --
    def _build_topbar(self):
        wrap = ttkb.Frame(self, padding=(24, 16, 24, 8))
        wrap.grid(row=0, column=1, sticky="ew")
        self.topbar_wrap = wrap

        bar = ds.Card(wrap, elevation=1, padding=(18, 12), page_bg=BG_APP, top_accent=True)
        bar.pack(fill=X)
        topbar = tk.Frame(bar.body, bg=BG_CARD)
        topbar.pack(fill=X)

        badge = tk.Canvas(topbar, width=30, height=30, bg=BG_CARD, highlightthickness=0, bd=0)
        badge.pack(side=LEFT)
        ds.draw_rounded_rect(badge, 1, 1, 29, 29, ds.Radius.XS, fill=ds.Color.ACCENT_SOFT, outline=ds.Color.BORDER_DEFAULT)
        badge.create_text(15, 15, text="◈", font=("Segoe UI Symbol", 13), fill=ds.Color.CYAN)
        ttkb.Label(
            topbar, text="ESPACE DE SAUVEGARDE", font=(FONT_FAMILY, 9, "bold"), bootstyle="info"
        ).pack(side=LEFT, padx=(10, 0))

        self.dest_button = ttkb.Button(topbar, text="📂  Choisir une destination", bootstyle="info-outline", command=self._choose_destination)
        self.dest_button.pack(side=LEFT, padx=(18, 10))
        self.dest_hint = ds.Badge(topbar, "À CHOISIR", variant="warning", page_bg=BG_CARD)
        self.dest_hint.pack(side=LEFT)
        self.dest_var.trace_add("write", lambda *_: self._refresh_dest_hint())
        self._refresh_dest_hint()

        self.progress = ttkb.Progressbar(topbar, mode="indeterminate", bootstyle="primary-striped", length=150)
        # affiché uniquement pendant une tâche (voir _run_background)

        self.admin_badge = ds.Badge(topbar, "", variant="success", page_bg=BG_CARD)
        self.admin_badge.pack(side=RIGHT)
        self._refresh_admin_badge()

    def _refresh_dest_hint(self):
        value = self.dest_var.get().strip()
        if value:
            self.dest_button.configure(text=f"📂  {Path(value).name or value}")
            self.dest_hint.set_text("PRÊTE", variant="success")
        else:
            self.dest_button.configure(text="📂  Choisir une destination")
            self.dest_hint.set_text("À CHOISIR", variant="warning")

    def _refresh_admin_badge(self):
        if bt.is_admin():
            self.admin_badge.set_text("✓ ADMINISTRATEUR", variant="success")
        else:
            self.admin_badge.set_text("⚠ UTILISATEUR STANDARD", variant="warning")

    # ------------------------------------------------------------- Content --
    def _build_content(self):
        container = ttkb.Frame(self)
        container.grid(row=1, column=1, sticky="nsew", padx=24, pady=(0, 10))
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self.content_container = container

        for key, _, _ in NAV_ITEMS:
            frame = ttkb.Frame(container)
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = frame

        self._page_builders = {
            "home": self._build_home_page,
            "files": self._build_files_page,
            "browsers": self._build_browsers_page,
            "licenses": self._build_licenses_page,
            "programs": self._build_programs_page,
            "email": self._build_email_page,
            "wifi": self._build_wifi_page,
            "checklist": self._build_checklist_page,
        }
        self._ensure_page("home")

    def _ensure_page(self, key: str):
        """Construit une page seulement quand elle devient utile.

        Construire chaque onglet avant le premier affichage créait un écran vide
        perceptible, surtout avec les cartes Canvas et leurs animations.
        """
        if key not in self.built_pages:
            self._page_builders[key](self.pages[key])
            self.built_pages.add(key)

    def _select_page(self, key: str):
        self._ensure_page(key)
        for k, btn in self.nav_buttons.items():
            btn.set_active(k == key)
        self.pages[key].tkraise()
        if key == "home":
            self.topbar_wrap.grid_remove()
            if not self.active_tasks:
                self.console_frame.grid_remove()
        else:
            self.topbar_wrap.grid()
            self.console_frame.grid()
        if key == "home":
            self._refresh_dashboard_dest_badge()
            self._refresh_dashboard_status()

    # ---------------------------------------------------------------- Console
    def _build_console(self):
        frame = ttkb.Labelframe(self, text="  Journal d'activité", padding=10, bootstyle="secondary")
        frame.grid(row=2, column=1, sticky="ew", padx=24, pady=(0, 18))
        self.console_frame = frame

        header = ttkb.Frame(frame)
        header.pack(fill=X)
        ttkb.Button(header, text="Effacer", bootstyle="link", command=self._clear_log).pack(side=RIGHT)

        transfer_row = ttkb.Frame(frame)
        transfer_row.pack(fill=X, pady=(0, 8))
        self.transfer_label = ttkb.Label(transfer_row, text="Aucun transfert en cours", font=(FONT_FAMILY, 9))
        self.transfer_label.pack(side=LEFT)
        self.transfer_progress = ttkb.Progressbar(
            transfer_row, mode="determinate", bootstyle="info", maximum=100, value=0
        )
        self.transfer_progress.pack(side=LEFT, fill=X, expand=True, padx=(10, 0))

        text_wrap = ttkb.Frame(frame)
        text_wrap.pack(fill=BOTH, expand=True, pady=(4, 0))
        self.log_text = tk.Text(
            text_wrap,
            height=4,
            wrap="word",
            state="disabled",
            background=CONSOLE_BG,
            foreground=CONSOLE_FG,
            insertbackground=CONSOLE_FG,
            font=("Cascadia Code", 9),
            relief="flat",
            padx=10,
            pady=8,
        )
        self.log_text.pack(fill=BOTH, expand=True, side=LEFT)
        scroll = ttkb.Scrollbar(text_wrap, command=self.log_text.yview, bootstyle="round")
        scroll.pack(fill=Y, side=RIGHT)
        self.log_text.configure(yscrollcommand=scroll.set)
        self._log("Prêt. Choisissez un dossier de destination pour commencer.")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ Home
    def _build_home_page(self, page: ttkb.Frame):
        aurora_home.build(self, page)

    def _home_category_changed(self):
        self.config_data['home_categories'] = {key: var.get() for key, var in self.home_category_vars.items()}
        bt.save_config(self.config_data)
        self.home_dashboard.render()

    def _build_stat_card(self, parent, row, col, icon, title, initial="—", badge=False):
        """Petite carte de statistique (icône + titre + valeur, badge optionnel)
        pour la grille « Aperçu de la sauvegarde » du tableau de bord."""
        card = ds.Card(parent, elevation=1, padding=ds.Space.MD, page_bg=BG_APP)
        card.grid(
            row=row, column=col, sticky="nsew",
            padx=(0 if col == 0 else ds.Space.MD, 0), pady=(0 if row == 0 else ds.Space.MD, 0),
        )
        head = tk.Frame(card.body, bg=BG_CARD)
        head.pack(fill=X)
        tk.Label(head, text=icon, font=(FONT_FAMILY, 13), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY).pack(side=LEFT)
        title_lbl = tk.Label(head, text=title, font=(FONT_FAMILY, 8, "bold"), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY)
        title_lbl.configure(foreground=TEXT_MUTED)
        title_lbl.pack(side=LEFT, padx=(6, 0))
        value_lbl = tk.Label(card.body, text=initial, font=(FONT_FAMILY, 13, "bold"), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY)
        value_lbl.pack(anchor=W, pady=(4, 0))
        badge_widget = None
        if badge:
            badge_widget = ds.Badge(card.body, "À CHOISIR", variant="warning", page_bg=BG_CARD)
            badge_widget.pack(anchor=W, pady=(4, 0))
        return value_lbl, badge_widget

    def _build_quick_link_card(self, parent, col, key, icon, title, desc):
        card = ds.Card(parent, elevation=1, padding=ds.Space.MD, page_bg=BG_APP)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else ds.Space.MD, 0))
        row = tk.Frame(card.body, bg=BG_CARD)
        row.pack(fill=X)
        tk.Label(row, text=icon, font=(FONT_FAMILY, 16), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY).pack(side=LEFT)
        tk.Label(row, text="›", font=(FONT_FAMILY, 13, "bold"), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY).pack(side=RIGHT)
        tk.Label(
            card.body, text=title, font=(FONT_FAMILY, 10, "bold"), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY,
        ).pack(anchor=W, pady=(8, 2))
        caption = tk.Label(card.body, text=desc, font=(FONT_FAMILY, 8), bg=BG_CARD, fg=ds.Color.TEXT_PRIMARY)
        caption.configure(foreground=TEXT_MUTED)
        caption.pack(anchor=W)
        _bind_click(card, lambda k=key: self._select_page(k))
        ds.bind_hover_color(
            card, ds.Color.BORDER_SUBTLE, ds.Color.ACCENT, duration=ds.DURATION_FAST,
            apply=lambda w, c: w.set_border(c),
        )

    # ------------------------------------------------------- Rafraîchissement
    def _refresh_dashboard(self):
        if not hasattr(self, 'home_dashboard'):
            return
        self._refresh_dashboard_status()
        self._refresh_dashboard_dest_badge()
        self.home_dashboard.set_last(self.config_data.get('last_backup_at') or 'Jamais')

    def _refresh_dashboard_status(self):
        aurora_home.update_status(self)

    def _refresh_dashboard_dest_badge(self):
        aurora_home.update_destination(self)

    def _on_destination_changed(self, *_args):
        """Rafraîchit l'accueil sans lancer une lecture disque à chaque frappe."""
        self._refresh_dashboard_dest_badge()
        self._refresh_dashboard_status()
        self._stats_request_id += 1  # invalide une analyse lancée pour un ancien chemin
        if hasattr(self, "stat_files_val"):
            self.after(600, self._refresh_backup_stats)

    def _refresh_backup_stats(self):
        """Recalcule le nombre de fichiers et l'espace utilisé dans le dossier de
        destination (parcours disque en arrière-plan, purement informatif)."""
        if not hasattr(self, "stat_files_val"):
            return
        value = self.dest_var.get().strip()
        if not value:
            self.stat_files_val.configure(text="—")
            self.stat_size_val.configure(text="—")
            return
        dest = Path(value)
        self._stats_request_id += 1
        request_id = self._stats_request_id

        def task():
            count, size = bt.compute_backup_stats(dest)
            self.ui_queue.put(
                lambda: self._apply_backup_stats_if_current(request_id, value, count, size)
            )

        # L'analyse d'un disque externe peut être longue. Elle ne doit ni
        # retarder le premier affichage, ni afficher le spinner des vraies
        # sauvegardes.
        def start_if_current():
            if request_id == self._stats_request_id:
                threading.Thread(target=task, daemon=True).start()

        self.after(600, start_if_current)

    def _apply_backup_stats_if_current(self, request_id, destination, count, size_bytes):
        if request_id != self._stats_request_id or self.dest_var.get().strip() != destination:
            return
        self._apply_backup_stats(count, size_bytes)

    def _apply_backup_stats(self, count: int, size_bytes: int):
        self.stat_files_val.configure(text=f"{count:,}".replace(",", " "))
        self.stat_size_val.configure(text=bt.format_size(size_bytes))

    def _checklist_percent(self) -> int:
        saved_state = self.config_data.get("checklist", {})
        if not CHECKLIST_ITEMS:
            return 0
        done = sum(1 for item in CHECKLIST_ITEMS if saved_state.get(item, False))
        return round(100 * done / len(CHECKLIST_ITEMS))

    def _relaunch_admin(self):
        bt.relaunch_as_admin()

    # ----------------------------------------------------------- Notifications
    def _toast(self, message: str, variant: str = "info", title: str | None = None):
        ds.Toast(self, message, variant=variant, title=title)

    def _alert(self, title: str, message: str, variant: str = "warning"):
        ds.ConfirmDialog.ask(self, title, message, variant=variant, confirm_text="OK", show_cancel=False)

    def _confirm(self, title: str, message: str, variant: str = "warning", confirm_text: str = "Confirmer") -> bool:
        return ds.ConfirmDialog.ask(self, title, message, variant=variant, confirm_text=confirm_text)

    # ------------------------------------------------------------- Fichiers --
    def _build_files_page(self, page: ttkb.Frame):
        self._page_title(page, "▣", "Fichiers & divers")

        card = ttkb.Labelframe(page, text="  Dossiers standards", padding=16, bootstyle="info")
        card.pack(fill=X, pady=(0, 14))
        self.folder_vars: dict[str, tk.BooleanVar] = {}
        toggles = ttkb.Frame(card)
        toggles.pack(fill=X)
        for i, name in enumerate(bt.STANDARD_FOLDERS):
            var = tk.BooleanVar(value=True)
            self.folder_vars[name] = var
            ttkb.Checkbutton(toggles, text=name, variable=var, bootstyle="round-toggle").grid(
                row=i // 3, column=i % 3, sticky=W, padx=(0, 24), pady=4
            )

        self.saved_games_var = tk.BooleanVar(value=True)
        ttkb.Checkbutton(
            card,
            text="Sauvegardes de jeux vidéo (dossier « Saved Games »)",
            variable=self.saved_games_var,
            bootstyle="round-toggle",
        ).pack(anchor=W, pady=(10, 0))

        ttkb.Button(
            card, text="Sauvegarder les dossiers sélectionnés", bootstyle="primary", command=self._run_files_backup
        ).pack(anchor=W, pady=(14, 0))

        custom_card = ttkb.Labelframe(page, text="  Dossiers personnalisés", padding=16, bootstyle="info")
        custom_card.pack(fill=BOTH, expand=True)
        ttkb.Label(
            custom_card,
            text="Dossiers SOURCES à copier (pas la destination, celle-ci se choisit en haut) : VPN "
            "d'entreprise, certificats, logiciels métier, presets, photos de téléphone synchronisées "
            "uniquement sur ce PC, etc.",
            wraplength=680,
            justify=LEFT,
            bootstyle="secondary",
        ).pack(anchor=W, pady=(0, 8))

        list_frame = ttkb.Frame(custom_card)
        list_frame.pack(fill=BOTH, expand=True)
        self.custom_listbox = tk.Listbox(
            list_frame,
            height=6,
            background=str(self.colors.inputbg),
            foreground=str(self.colors.inputfg),
            selectbackground=str(self.colors.primary),
            selectforeground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground=str(self.colors.border),
        )
        self.custom_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        for folder in self.config_data.get("custom_folders", []):
            self.custom_listbox.insert("end", folder)
        cscroll = ttkb.Scrollbar(list_frame, command=self.custom_listbox.yview, bootstyle="round")
        cscroll.pack(side=RIGHT, fill=Y)
        self.custom_listbox.configure(yscrollcommand=cscroll.set)

        btn_row = ttkb.Frame(custom_card)
        btn_row.pack(anchor=W, pady=(10, 0))
        ttkb.Button(btn_row, text="+ Ajouter un dossier", bootstyle="secondary", command=self._add_custom_folder).pack(
            side=LEFT
        )
        ttkb.Button(
            btn_row, text="Retirer la sélection", bootstyle="secondary-outline", command=self._remove_custom_folder
        ).pack(side=LEFT, padx=6)
        ttkb.Button(
            btn_row, text="Sauvegarder ces dossiers", bootstyle="primary", command=self._run_custom_backup
        ).pack(side=LEFT, padx=6)

    def _add_custom_folder(self):
        folder = filedialog.askdirectory(title="Choisir un dossier à sauvegarder")
        if not folder:
            return
        dest = self.dest_var.get().strip()
        if dest and self._paths_conflict(folder, dest):
            self._alert(
                "Conflit destination / source",
                f"« {folder} » est identique ou contient votre dossier de destination actuel\n"
                f"« {dest} ».\n\nCe dossier ne sera PAS copié (cela créerait une boucle infinie).\n"
                "Choisissez un autre dossier source, ou une autre destination.",
                variant="warning",
            )
        self.custom_listbox.insert("end", folder)
        self._persist_custom_folders()

    def _remove_custom_folder(self):
        selection = self.custom_listbox.curselection()
        for index in reversed(selection):
            self.custom_listbox.delete(index)
        self._persist_custom_folders()

    def _persist_custom_folders(self):
        self.config_data["custom_folders"] = list(self.custom_listbox.get(0, "end"))
        bt.save_config(self.config_data)

    def _run_files_backup(self):
        dest = self._require_destination()
        if not dest:
            return
        selected = [name for name, var in self.folder_vars.items() if var.get()]
        include_saved_games = self.saved_games_var.get()

        def task(log, progress):
            bt.backup_standard_folders(dest, selected, log, progress=progress)
            if include_saved_games:
                bt.backup_saved_games(dest, log, progress=progress)
            log("✔ Sauvegarde des fichiers personnels terminée.")

        self._run_background(task, success_message="Fichiers personnels sauvegardés avec succès.")

    def _run_custom_backup(self):
        dest = self._require_destination()
        if not dest:
            return
        folders = list(self.custom_listbox.get(0, "end"))
        if not folders:
            self._alert("Aucun dossier", "Ajoutez au moins un dossier personnalisé.", variant="warning")
            return

        def task(log, progress):
            bt.backup_custom_folders(dest, folders, log, progress=progress)
            log("✔ Sauvegarde des dossiers personnalisés terminée.")

        self._run_background(task, success_message="Dossiers personnalisés sauvegardés avec succès.")

    def _make_scrollable(self, page: ttkb.Frame) -> ttkb.Frame:
        """Enveloppe `page` dans un canvas défilant (molette) et renvoie le frame intérieur
        où placer le contenu réel — nécessaire pour les pages dont le contenu peut dépasser
        la hauteur de la fenêtre (ex. plusieurs sections empilées)."""
        canvas = tk.Canvas(page, highlightthickness=0, bd=0, bg=BG_APP)
        vscroll = ttkb.Scrollbar(page, orient="vertical", bootstyle="round")
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)

        inner = ttkb.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor=NW)

        def _refresh_scrollregion():
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_scrollbar(*args):
            # Certaines cartes du Design System (animées, imbriquées dans ce canvas
            # défilant) ne font pas toujours remonter un <Configure> complet vers `inner`
            # une fois leur animation terminée — le scrollregion peut alors rester
            # légèrement obsolète (trop petit) et bloquer le défilement. On le recalcule
            # systématiquement juste avant chaque interaction avec la scrollbar/molette.
            _refresh_scrollregion()
            canvas.yview(*args)

        vscroll.configure(command=_on_scrollbar)
        inner.bind("<Configure>", lambda e: _refresh_scrollregion())
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))

        def _on_wheel(event):
            _refresh_scrollregion()
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Filet de sécurité : recalcule encore le scrollregion à quelques reprises après la
        # construction de la page, le temps que les animations d'apparition des cartes
        # (reveal_card) se terminent.
        for delay in (150, 400, 800, 1500):
            self.after(delay, _refresh_scrollregion)

        return inner

    # ------------------------------------------------------------ Navigateurs
    def _build_browsers_page(self, page: ttkb.Frame):
        page = self._make_scrollable(page)
        self._page_title(page, "◎", "Navigateurs")

        ttkb.Label(
            page,
            text="La méthode la plus fiable reste la synchronisation de compte (Google, Microsoft, "
            "Mozilla). Les cartes ci-dessous copient en plus le profil local (favoris, mots de passe "
            "chiffrés, historique) en filet de sécurité.",
            wraplength=760,
            justify=LEFT,
            bootstyle="secondary",
        ).pack(anchor=W, pady=(0, 16))

        detected = bt.detect_browsers()
        self.browser_buttons = {}
        for name, found in detected.items():
            surface = BG_CARD if found else BG_CARD_SOFT
            outer = tk.Frame(page, bg=surface)
            outer.pack(fill=X, pady=5)
            row = tk.Frame(outer, bg=surface)
            row.pack(fill=X, padx=14, pady=14)
            icon = BROWSER_ICONS.get(name, "🌐")
            tk.Label(
                row, text=f"{icon}  {name}", font=(FONT_FAMILY, 11, "bold"),
                bg=surface, fg=ds.Color.TEXT_PRIMARY,
            ).pack(side=LEFT)
            status = "détecté" if found else "non détecté"
            ttkb.Label(
                row, text=status,
                bootstyle=("success" if found else "secondary"),
                padding=(8, 2),
            ).pack(side=LEFT, padx=12)
            btn = ttkb.Button(
                row, text="Sauvegarder", bootstyle=("primary" if found else "secondary"),
                state=("normal" if found else "disabled"),
                command=lambda n=name: self._run_browser_backup(n),
            )
            btn.pack(side=RIGHT)
            self.browser_buttons[name] = btn

        ttkb.Button(
            page, text="Sauvegarder tous les navigateurs détectés", bootstyle="primary",
            command=self._run_all_browsers,
        ).pack(anchor=W, pady=16)

        ttkb.Separator(page, bootstyle="secondary").pack(fill=X, pady=(4, 18))

        self._page_title(page, "⟲", "Restaurer sur ce PC")
        ttkb.Label(
            page,
            text="Sur le nouvel environnement : choisissez le dossier de sauvegarde contenant le "
            "sous-dossier « Navigateurs », puis restaurez chaque profil détecté. Fermez le "
            "navigateur cible avant de restaurer son profil.",
            wraplength=760,
            justify=LEFT,
            bootstyle="secondary",
        ).pack(anchor=W, pady=(0, 12))

        src_row = ttkb.Frame(page)
        src_row.pack(fill=X, pady=(0, 12))
        ttkb.Label(src_row, text="📂 Dossier de sauvegarde (source) :", font=(FONT_FAMILY, 10, "bold")).pack(
            side=LEFT
        )
        self.restore_src_var = tk.StringVar(value="")
        ttkb.Entry(src_row, textvariable=self.restore_src_var, width=42, state="readonly").pack(
            side=LEFT, padx=8
        )
        ttkb.Button(
            src_row, text="Choisir...", bootstyle="secondary-outline", command=self._choose_restore_source
        ).pack(side=LEFT)

        self.restore_rows_frame = ttkb.Frame(page)
        self.restore_rows_frame.pack(fill=X)
        self.restore_all_btn = ttkb.Button(
            page, text="Restaurer tous les navigateurs détectés dans la sauvegarde", bootstyle="warning",
            command=self._run_all_browser_restores, state="disabled",
        )
        self.restore_all_btn.pack(anchor=W, pady=16)

        self.restore_hint = ttkb.Label(
            self.restore_rows_frame, text="Choisissez d'abord un dossier de sauvegarde ci-dessus.",
            bootstyle="secondary",
        )
        self.restore_hint.pack(anchor=W, pady=6)

    def _choose_restore_source(self):
        folder = filedialog.askdirectory(title="Choisir le dossier de sauvegarde à restaurer")
        if not folder:
            return
        self.restore_src_var.set(folder)
        self._refresh_restore_rows()

    def _refresh_restore_rows(self):
        for child in self.restore_rows_frame.winfo_children():
            child.destroy()

        src_value = self.restore_src_var.get().strip()
        if not src_value:
            self.restore_hint = ttkb.Label(
                self.restore_rows_frame, text="Choisissez d'abord un dossier de sauvegarde ci-dessus.",
                bootstyle="secondary",
            )
            self.restore_hint.pack(anchor=W, pady=6)
            self.restore_all_btn.configure(state="disabled")
            return

        detected = bt.detect_browser_backups(Path(src_value))
        any_found = any(detected.values())
        self.restore_all_btn.configure(state=("normal" if any_found else "disabled"))
        if not any_found:
            ttkb.Label(
                self.restore_rows_frame,
                text="Aucune sauvegarde de navigateur trouvée dans ce dossier "
                "(sous-dossier « Navigateurs » absent ou vide).",
                bootstyle="warning",
            ).pack(anchor=W, pady=6)
            return

        for name, found in detected.items():
            surface = BG_CARD if found else BG_CARD_SOFT
            outer = tk.Frame(self.restore_rows_frame, bg=surface)
            outer.pack(fill=X, pady=5)
            row = tk.Frame(outer, bg=surface)
            row.pack(fill=X, padx=14, pady=14)
            icon = BROWSER_ICONS.get(name, "🌐")
            tk.Label(
                row, text=f"{icon}  {name}", font=(FONT_FAMILY, 11, "bold"),
                bg=surface, fg=ds.Color.TEXT_PRIMARY,
            ).pack(side=LEFT)
            status = "sauvegarde trouvée" if found else "aucune sauvegarde"
            ttkb.Label(
                row, text=status,
                bootstyle=("success" if found else "secondary"),
                padding=(8, 2),
            ).pack(side=LEFT, padx=12)
            ttkb.Button(
                row, text="Restaurer", bootstyle=("warning" if found else "secondary"),
                state=("normal" if found else "disabled"),
                command=lambda n=name: self._run_browser_restore(n),
            ).pack(side=RIGHT)

    def _confirm_restore(self, message: str) -> bool:
        return self._confirm("Confirmer la restauration", message, variant="warning", confirm_text="Restaurer")

    def _run_browser_restore(self, name: str):
        src_value = self.restore_src_var.get().strip()
        if not src_value:
            return
        if not self._confirm_restore(
            f"Restaurer le profil {name} va écraser/fusionner les fichiers du profil actuel sur ce PC.\n\n"
            "Assurez-vous que le navigateur est bien fermé. Continuer ?"
        ):
            return
        src = Path(src_value)

        def task(log, progress):
            if name == "Mozilla Firefox":
                bt.restore_firefox(src, log, progress=progress)
            else:
                bt.restore_chromium_browser(name, src, log, progress=progress)
            log(f"✔ Restauration de {name} terminée.")

        self._run_background(task, success_message=f"Restauration de {name} terminée.")

    def _run_all_browser_restores(self):
        src_value = self.restore_src_var.get().strip()
        if not src_value:
            return
        if not self._confirm_restore(
            "Restaurer tous les navigateurs détectés va écraser/fusionner les profils actuels sur ce PC.\n\n"
            "Assurez-vous que ces navigateurs sont bien fermés. Continuer ?"
        ):
            return
        src = Path(src_value)
        detected = bt.detect_browser_backups(src)

        def task(log, progress):
            for name, found in detected.items():
                if not found:
                    continue
                if name == "Mozilla Firefox":
                    bt.restore_firefox(src, log, progress=progress)
                else:
                    bt.restore_chromium_browser(name, src, log, progress=progress)
            log("✔ Restauration des navigateurs terminée.")

        self._run_background(task, success_message="Restauration des navigateurs terminée.")

    def _run_browser_backup(self, name: str):
        dest = self._require_destination()
        if not dest:
            return

        def task(log, progress):
            if name == "Mozilla Firefox":
                bt.backup_firefox(dest, log, progress=progress)
            else:
                bt.backup_chromium_browser(name, dest, log, progress=progress)
            log(f"✔ Sauvegarde de {name} terminée.")

        self._run_background(task, success_message=f"Sauvegarde de {name} terminée.")

    def _run_all_browsers(self):
        dest = self._require_destination()
        if not dest:
            return
        detected = bt.detect_browsers()

        def task(log, progress):
            for name, found in detected.items():
                if not found:
                    continue
                if name == "Mozilla Firefox":
                    bt.backup_firefox(dest, log, progress=progress)
                else:
                    bt.backup_chromium_browser(name, dest, log, progress=progress)
            log("✔ Sauvegarde des navigateurs terminée.")

        self._run_background(task, success_message="Navigateurs sauvegardés avec succès.")

    # -------------------------------------------------------------- Licences
    def _build_licenses_page(self, page: ttkb.Frame):
        self._page_title(page, "⌘", "Licences")

        win_card = ttkb.Labelframe(page, text="  Clé Windows", padding=16, bootstyle="info")
        win_card.pack(fill=X, pady=(0, 14))
        top_row = ttkb.Frame(win_card)
        top_row.pack(fill=X)
        ttkb.Button(
            top_row, text="Récupérer la clé Windows OEM", bootstyle="primary", command=self._fetch_windows_key
        ).pack(side=LEFT)
        self.windows_key_var = tk.StringVar(value=self.windows_oem_key)
        ttkb.Entry(top_row, textvariable=self.windows_key_var, width=38, state="readonly").pack(
            side=LEFT, padx=10
        )

        lic_card = ttkb.Labelframe(page, text="  Autres logiciels et clés de licence", padding=16, bootstyle="info")
        lic_card.pack(fill=BOTH, expand=True)

        form = ttkb.Frame(lic_card)
        form.pack(fill=X, pady=(0, 10))
        ttkb.Label(form, text="Logiciel :").grid(row=0, column=0, sticky=W)
        self.lic_name_var = tk.StringVar()
        ttkb.Entry(form, textvariable=self.lic_name_var, width=28).grid(row=0, column=1, padx=6)
        ttkb.Label(form, text="Clé :").grid(row=0, column=2, sticky=W)
        self.lic_key_var = tk.StringVar()
        ttkb.Entry(form, textvariable=self.lic_key_var, width=28).grid(row=0, column=3, padx=6)
        ttkb.Button(form, text="Ajouter", bootstyle="success", command=self._add_license).grid(
            row=0, column=4, padx=6
        )

        columns = ("logiciel", "cle")
        self.lic_tree = ttkb.Treeview(lic_card, columns=columns, show="headings", height=8, bootstyle="primary")
        self.lic_tree.heading("logiciel", text="Logiciel")
        self.lic_tree.heading("cle", text="Clé de licence")
        self.lic_tree.pack(fill=BOTH, expand=True, pady=6)

        btn_row = ttkb.Frame(lic_card)
        btn_row.pack(anchor=W)
        ttkb.Button(
            btn_row, text="Retirer la sélection", bootstyle="secondary-outline", command=self._remove_license
        ).pack(side=LEFT)
        ttkb.Button(
            btn_row, text="Enregistrer dans le dossier de sauvegarde", bootstyle="primary",
            command=self._save_licenses,
        ).pack(side=LEFT, padx=6)

    def _fetch_windows_key(self):
        def task(log, progress):
            key = bt.get_windows_key(log)
            if key:
                self.ui_queue.put(lambda: self._display_windows_key(key))

        self._run_background(task)

    def _display_windows_key(self, key: str):
        """Met à jour les widgets de licence depuis le thread graphique."""
        self.windows_oem_key = key
        if "licenses" not in self.built_pages:
            return
        self.windows_key_var.set(key)
        if not any(self.lic_tree.item(item)["values"] == ["Windows (clé OEM)", key]
                   for item in self.lic_tree.get_children()):
            self.lic_tree.insert("", "end", values=("Windows (clé OEM)", key))

    def _add_license(self):
        name = self.lic_name_var.get().strip()
        key = self.lic_key_var.get().strip()
        if not name or not key:
            self._alert("Champs requis", "Renseignez le nom du logiciel et sa clé.", variant="warning")
            return
        self.lic_tree.insert("", "end", values=(name, key))
        self.lic_name_var.set("")
        self.lic_key_var.set("")

    def _remove_license(self):
        for item in self.lic_tree.selection():
            self.lic_tree.delete(item)

    def _save_licenses(self):
        dest = self._require_destination()
        if not dest:
            return
        licenses = [
            {"logiciel": self.lic_tree.item(i)["values"][0], "cle": self.lic_tree.item(i)["values"][1]}
            for i in self.lic_tree.get_children()
        ]

        def task(log, progress):
            bt.save_licenses(dest, licenses, log)

        self._run_background(task, success_message="Licences enregistrées avec succès.")

    # ------------------------------------------------------------- Programmes
    def _build_programs_page(self, page: ttkb.Frame):
        self._page_title(page, "▤", "Programmes")

        card = ttkb.Labelframe(page, text="  Export de la liste des logiciels", padding=16, bootstyle="info")
        card.pack(fill=X)
        ttkb.Label(
            card,
            text="Exporte la liste des logiciels installés via winget (réinstallation automatique "
            "possible après le formatage) ainsi que la liste complète pour repérer les logiciels non "
            "pris en charge par winget (à réinstaller manuellement).",
            wraplength=700,
            justify=LEFT,
            bootstyle="secondary",
        ).pack(anchor=W, pady=(0, 14))
        ttkb.Button(
            card, text="Exporter la liste des programmes", bootstyle="primary", command=self._export_programs
        ).pack(anchor=W)

    def _export_programs(self):
        dest = self._require_destination()
        if not dest:
            return

        def task(log, progress):
            bt.export_installed_programs(dest, log)
            log("✔ Export des programmes terminé.")

        self._run_background(task, success_message="Liste des programmes exportée avec succès.")

    # ------------------------------------------------------------------ Email
    def _build_email_page(self, page: ttkb.Frame):
        self._page_title(page, "✉", "E-mail")

        card = ttkb.Labelframe(page, text="  Courrier électronique local", padding=16, bootstyle="info")
        card.pack(fill=X)
        ttkb.Label(
            card,
            text="Sauvegarde automatiquement les données locales d'Outlook (.pst/.ost) et de "
            "Thunderbird. Pour un webmail (Gmail, Outlook.com...), rien à faire : vos messages restent "
            "en ligne, vérifiez simplement que vous connaissez le mot de passe du compte.",
            wraplength=700,
            justify=LEFT,
            bootstyle="secondary",
        ).pack(anchor=W, pady=(0, 14))
        ttkb.Button(
            card, text="Sauvegarder le courrier local (Outlook / Thunderbird)", bootstyle="primary",
            command=self._run_email_backup,
        ).pack(anchor=W)

    def _run_email_backup(self):
        dest = self._require_destination()
        if not dest:
            return

        def task(log, progress):
            bt.backup_email(dest, log, progress=progress)
            log("✔ Sauvegarde du courrier électronique terminée.")

        self._run_background(task, success_message="Courrier électronique sauvegardé avec succès.")

    # -------------------------------------------------------- Wi-Fi & Pilotes
    def _build_wifi_page(self, page: ttkb.Frame):
        self._page_title(page, "◫", "Wi-Fi & Pilotes")

        warn = ttkb.Frame(page, bootstyle="warning", padding=14)
        warn.pack(fill=X, pady=(0, 14))
        ttkb.Label(
            warn, text="⚠ Ces deux exports nécessitent les droits administrateur.",
            bootstyle="inverse-warning", font=(FONT_FAMILY, 10, "bold"),
        ).pack(anchor=W)

        card = ttkb.Labelframe(page, text="  Exports", padding=16, bootstyle="info")
        card.pack(fill=X)
        ttkb.Button(
            card, text="Exporter les profils Wi-Fi (avec mots de passe)", bootstyle="primary",
            command=self._run_wifi_export,
        ).pack(anchor=W, pady=4)
        ttkb.Button(
            card, text="Exporter les pilotes installés", bootstyle="primary", command=self._run_drivers_export
        ).pack(anchor=W, pady=4)

        if not bt.is_admin():
            ttkb.Button(
                page, text="🛡  Relancer l'application en administrateur", bootstyle="warning-outline",
                command=self._relaunch_admin,
            ).pack(anchor=W, pady=16)

    def _run_wifi_export(self):
        dest = self._require_destination()
        if not dest:
            return

        def task(log, progress):
            bt.export_wifi_profiles(dest, log)

        self._run_background(task, success_message="Profils Wi-Fi exportés avec succès.")

    def _run_drivers_export(self):
        dest = self._require_destination()
        if not dest:
            return

        def task(log, progress):
            bt.export_drivers(dest, log)

        self._run_background(task, success_message="Pilotes exportés avec succès.")

    # ---------------------------------------------------------------- Checklist
    def _build_checklist_page(self, page: ttkb.Frame):
        self._page_title(page, "✓", "Checklist finale")

        card = ttkb.Frame(page, padding=4)
        card.pack(fill=BOTH, expand=True)

        saved_state = self.config_data.get("checklist", {})
        self.checklist_vars: dict[str, tk.BooleanVar] = {}
        for item in CHECKLIST_ITEMS:
            var = tk.BooleanVar(value=saved_state.get(item, False))
            var.trace_add("write", lambda *_: self._persist_checklist())
            self.checklist_vars[item] = var
            outer = tk.Frame(card, bg=BG_CARD)
            outer.pack(fill=X, pady=3)
            row = tk.Frame(outer, bg=BG_CARD)
            row.pack(fill=X, padx=14, pady=10)
            ttkb.Checkbutton(row, text=item, variable=var, bootstyle="success-round-toggle").pack(anchor=W)

        ttkb.Button(
            page, text="📝  Écrire le rapport de sauvegarde", bootstyle="success", command=self._write_report
        ).pack(anchor=W, pady=(16, 0))

    def _persist_checklist(self):
        self.config_data["checklist"] = {item: var.get() for item, var in self.checklist_vars.items()}
        bt.save_config(self.config_data)
        self._refresh_dashboard_status()

    def _write_report(self):
        dest = self._require_destination()
        if not dest:
            return
        lines = ["Rapport de sauvegarde PC", "=" * 30, ""]
        for item, var in self.checklist_vars.items():
            mark = "[x]" if var.get() else "[ ]"
            lines.append(f"{mark} {item}")
        path = bt.write_report(dest, lines)
        self._log(f"✔ Rapport écrit : {path}")
        self._toast(f"Rapport enregistré :\n{path}", variant="success", title="Rapport écrit")

    # --------------------------------------------------------- Tout sauvegarder
    def _run_all(self):
        # Les choix de la page Fichiers sont nécessaires à la sauvegarde
        # complète ; elle est donc créée seulement à cet instant si besoin.
        self._ensure_page("files")
        dest = self._require_destination()
        if not dest:
            return
        selected = [name for name, var in self.folder_vars.items() if var.get()]
        categories = {key: var.get() for key, var in self.home_category_vars.items()}
        if not any(categories.values()):
            self._alert("Aucune catégorie", "Sélectionnez au moins une catégorie à sauvegarder.")
            return
        detected = bt.detect_browsers() if categories["browsers"] else {}

        def task(log, progress):
            log("=== Démarrage de la sauvegarde complète ===")
            if categories["files"]:
                bt.backup_standard_folders(dest, selected, log, progress=progress)
                bt.backup_saved_games(dest, log, progress=progress)
                custom = list(self.config_data.get("custom_folders", []))
                if custom:
                    bt.backup_custom_folders(dest, custom, log, progress=progress)
            if categories["browsers"]:
                for name, found in detected.items():
                    if not found:
                        continue
                    if name == "Mozilla Firefox":
                        bt.backup_firefox(dest, log, progress=progress)
                    else:
                        bt.backup_chromium_browser(name, dest, log, progress=progress)
            if categories["licenses"]:
                key = bt.get_windows_key(log)
                if key:
                    self.ui_queue.put(lambda: self._display_windows_key(key))
            if categories["programs"]:
                bt.export_installed_programs(dest, log)
            if categories["email"]:
                bt.backup_email(dest, log, progress=progress)
            if categories["wifi"]:
                bt.export_wifi_profiles(dest, log)
                bt.export_drivers(dest, log)
            log("=== Sauvegarde complète terminée ===")

        def on_done():
            self.config_data["last_backup_at"] = datetime.now().strftime("%d/%m/%Y à %H:%M")
            bt.save_config(self.config_data)
            self._refresh_dashboard()

        self._run_background(
            task, on_done=on_done,
            success_message="Toutes les sauvegardes automatisables ont été effectuées avec succès.",
        )

    # ------------------------------------------------------------ Utilitaires
    def _page_title(self, page: ttkb.Frame, icon: str, title: str):
        row = ttkb.Frame(page)
        row.pack(fill=X, pady=(0, 18))
        badge = tk.Canvas(row, width=40, height=40, bg=BG_APP, highlightthickness=0, bd=0)
        badge.pack(side=LEFT)
        ds.draw_rounded_rect(badge, 1, 1, 39, 39, ds.Radius.SM, fill=ds.Color.ACCENT_SOFT, outline=ds.Color.BORDER_DEFAULT, width=1)
        badge.create_text(20, 20, text=icon, font=("Segoe UI Symbol", 16), fill=ds.Color.CYAN)
        ttkb.Label(row, text=title, font=(FONT_FAMILY, 15, "bold")).pack(side=LEFT, padx=(12, 0))

    def _choose_destination(self):
        folder = filedialog.askdirectory(title="Choisir le dossier/disque de sauvegarde")
        if not folder:
            return
        conflicts = [
            f for f in self.config_data.get("custom_folders", [])
            if self._paths_conflict(f, folder)
        ]
        if conflicts:
            self._alert(
                "Conflit destination / source",
                "La destination choisie est identique ou contenue dans un dossier source déjà "
                "ajouté :\n\n" + "\n".join(conflicts) + "\n\nCe(s) dossier(s) source ne seront PAS "
                "copiés (cela créerait une boucle infinie). Choisissez une autre destination, ou "
                "retirez ce(s) dossier(s) de la liste des dossiers personnalisés.",
                variant="warning",
            )
        self.dest_var.set(folder)
        self.config_data["last_destination"] = folder
        bt.save_config(self.config_data)

    @staticmethod
    def _paths_conflict(path_a: str, path_b: str) -> bool:
        """True si l'un des deux chemins est identique ou contenu dans l'autre."""
        try:
            a = Path(path_a).resolve()
            b = Path(path_b).resolve()
        except OSError:
            return False
        if a == b:
            return True
        try:
            a.relative_to(b)
            return True
        except ValueError:
            pass
        try:
            b.relative_to(a)
            return True
        except ValueError:
            return False

    def _require_destination(self) -> Path | None:
        value = self.dest_var.get().strip()
        if not value:
            self._alert(
                "Destination manquante", "Choisissez d'abord un dossier ou disque de sauvegarde.",
                variant="warning",
            )
            return None
        dest = Path(value)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._alert("Erreur", f"Impossible d'utiliser ce dossier : {exc}", variant="danger")
            return None
        return dest

    def _log(self, message: str):
        self.log_queue.put(message)

    def _progress(self, label: str, done: int, total: int):
        self.progress_queue.put((label, done, total))

    def _poll_log_queue(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass

        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", message + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass

        latest = None
        try:
            while True:
                latest = self.progress_queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            label, done, total = latest
            percent = round(100 * done / total) if total else 0
            self.transfer_progress.configure(maximum=max(total, 1), value=done)
            self.transfer_label.configure(text=f"{label} — {done}/{total} fichiers ({percent} %)")

        self.after(150, self._poll_log_queue)

    def _run_background(self, task_fn, on_done=None, success_message=None):
        self.active_tasks += 1
        if self.active_tasks == 1:
            self.console_frame.grid()
            self.progress.pack(side=RIGHT, padx=10)
            self.progress.start(12)
            if hasattr(self, "run_all_button"):
                self.run_all_button.set_state("disabled")
            self._refresh_dashboard_status()

        def worker():
            error = None
            try:
                task_fn(self._log, self._progress)
            except Exception as exc:  # sécurité : ne jamais planter le thread silencieusement
                error = str(exc)
                self._log(f"⚠ Erreur inattendue : {exc}")
            finally:
                self.ui_queue.put(lambda: self._task_finished(on_done, success_message, error))

        threading.Thread(target=worker, daemon=True).start()

    def _task_finished(self, on_done=None, success_message=None, error=None):
        self.active_tasks = max(0, self.active_tasks - 1)
        if self.active_tasks == 0:
            self.progress.stop()
            self.progress.pack_forget()
            self.transfer_progress.configure(value=0)
            self.transfer_label.configure(text="Aucun transfert en cours")
            if hasattr(self, "run_all_button"):
                self.run_all_button.set_state("normal")
            self._refresh_dashboard_status()
        if error:
            self._toast(f"L'opération a rencontré une erreur : {error}", variant="danger", title="Échec")
        elif success_message:
            self._toast(success_message, variant="success")
        if on_done and error is None:
            on_done()


if __name__ == "__main__":
    app = App()
    app.mainloop()
