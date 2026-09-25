"""Accueil Aurora dessiné avec Pillow, avec contrôles Tkinter natifs."""

from __future__ import annotations

import math
import shutil
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageTk
import ttkbootstrap as ttkb

import design_system as ds


def _rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _blend(a, b, amount):
    return tuple(round(x * (1 - amount) + y * amount) for x, y in zip(a, b))


def _glow(image, box, color, strength=90, blur=80):
    scale = 4
    size = (max(1, image.width // scale), max(1, image.height // scale))
    layer = Image.new("RGBA", size)
    ImageDraw.Draw(layer).ellipse(tuple(int(v / scale) for v in box),
                                  fill=(*_rgb(color), strength))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, blur / scale)))
    image.alpha_composite(layer.resize(image.size, Image.Resampling.BILINEAR))


def _glass(image, box, *, tint="#223A61", radius=14, accent=None):
    x1, y1, x2, y2 = map(int, box)
    width, height = x2 - x1, y2 - y1
    layer = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(layer)
    base = _rgb(tint)
    luminous = _blend(base, _rgb("#5269AB"), .23)
    shadow = _blend(base, _rgb("#111B30"), .12)
    for y in range(height):
        draw.line((0, y, width, y), fill=(*_blend(luminous, shadow, y / max(height - 1, 1)), 255))
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1),
                                           radius=radius, fill=187)
    layer.putalpha(mask)
    image.alpha_composite(layer, (x1, y1))
    border = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(border)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius,
                           outline=(162, 193, 248, 86), width=1)
    draw.line((radius, 1, width - radius, 1), fill=(213, 229, 255, 58), width=1)
    if accent:
        draw.line((radius, 2, 84, 2), fill=(*_rgb(accent), 180), width=2)
    image.alpha_composite(border, (x1, y1))


def _gradient_button(image, box):
    x1, y1, x2, y2 = map(int, box)
    width, height = max(1, x2 - x1), max(1, y2 - y1)
    strip = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(strip)
    left, middle, right = _rgb("#36B9F7"), _rgb("#438CFA"), _rgb("#A15CF3")
    for x in range(width):
        t = x / max(width - 1, 1)
        color = _blend(left, middle, t * 2) if t < .5 else _blend(middle, right, (t - .5) * 2)
        draw.line((x, 0, x, height), fill=(*color, 255))
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=16, fill=255)
    strip.putalpha(mask)
    image.alpha_composite(strip, (x1, y1))
    outline = ImageDraw.Draw(image)
    outline.rounded_rectangle((x1, y1, x2 - 1, y2 - 1), radius=16,
                              outline=(211, 224, 255, 105), width=1)


class AuroraButton(tk.Canvas):
    """Bouton principal dégradé, activable à la souris et au clavier."""

    def __init__(self, parent, command):
        super().__init__(parent, height=60, bg=ds.Color.BG_CARD, bd=0,
                         highlightthickness=0, takefocus=1, cursor="hand2")
        self.command = command
        self.state = "normal"
        self.hover = False
        self._image = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())

    def set_state(self, state):
        self.state = state
        self.configure(cursor="hand2" if state == "normal" else "arrow")
        self._draw()

    def _enter(self, _e):
        self.hover = True
        self._draw()

    def _leave(self, _e):
        self.hover = False
        self._draw()

    def _invoke(self, _e):
        if self.state == "normal":
            self.command()

    def _draw(self):
        width = max(self.winfo_width(), 10)
        image = Image.new("RGBA", (width, 60), ds.Color.BG_CARD)
        if self.state == "normal":
            _glow(image, (0, 35, width, 90), "#6387FF", 95 if self.hover else 56, 17)
            _gradient_button(image, (3, 3, width - 3, 56))
        else:
            ImageDraw.Draw(image).rounded_rectangle((3, 3, width - 3, 56),
                                                     radius=16, fill="#344361")
        self._image = ImageTk.PhotoImage(image.convert("RGB"))
        self.delete("all")
        self.create_image(0, 0, image=self._image, anchor="nw")
        self.create_text(width / 2, 30, text="▶   Lancer la sauvegarde",
                         fill="#FFFFFF" if self.state == "normal" else "#A5B2C7",
                         font=(ds.Font.FAMILY, 12, "bold"))
        if self.focus_get() is self:
            self.create_rectangle(2, 2, width - 2, 57, outline="#FFFFFF", width=2)


class AuroraDashboard(tk.Frame):
    CATEGORIES = (
        ("files", "▣", "Fichiers personnels", "Documents, images, vidéos et jeux", "Bureau · Téléchargements · Dossiers", "#4CDAFF"),
        ("browsers", "◎", "Navigateurs", "Profils et favoris", "Chrome · Edge · Firefox", "#98B5FF"),
        ("licenses", "⚿", "Licences", "Clé Windows et licences", "Clé OEM détectée si disponible", "#CE9AFF"),
        ("programs", "▦", "Programmes", "Liste des logiciels installés", "Export winget et inventaire", "#82E3D0"),
        ("email", "✉", "Messagerie", "Outlook et Thunderbird", "Archives et profils locaux", "#F199D5"),
        ("wifi", "≋", "Wi-Fi et pilotes", "Profils réseau et pilotes", "Accès administrateur requis", "#67DCED"),
    )

    def __init__(self, parent, app):
        super().__init__(parent, bg=ds.Color.BG_APP)
        self.app = app
        self.canvas = tk.Canvas(self, bg=ds.Color.BG_APP, bd=0, highlightthickness=0)
        self.scrollbar = ttkb.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                         bootstyle="round")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
        self._render_job = None
        self._background = None
        self.last_backup = "Jamais"
        self.status = ("Choisissez une destination", "Sélectionnez un dossier avant de commencer.", "!", ds.Color.WARNING)
        self.destination = ("Aucun dossier sélectionné", "Choisissez un support externe", "", False, 0.0)
        self.category_vars = {
            key: tk.BooleanVar(value=app.config_data.get("home_categories", {}).get(key, True))
            for key, *_ in self.CATEGORIES
        }
        self.category_checks = {}
        for key, *_ in self.CATEGORIES:
            var = self.category_vars[key]
            self.category_checks[key] = tk.Checkbutton(
                self.canvas, variable=var, command=self._category_changed, text="✓",
                indicatoron=False, width=2, bd=0, relief="flat", cursor="hand2",
                font=(ds.Font.FAMILY, 11, "bold"), highlightthickness=1,
                highlightcolor="#8BD7FF", highlightbackground="#5471AD",
                selectcolor="#397FF1", activebackground="#5798FF",
                activeforeground="#FFFFFF", fg="#FFFFFF", bg="#263B5C",
            )
        self.button = AuroraButton(self.canvas, app._run_all)
        self.change_button = tk.Button(
            self.canvas, text="⚙   Modifier la destination", command=app._choose_destination,
            bg="#233955", fg="#F1F5FF", activebackground="#315177",
            activeforeground="#FFFFFF", relief="flat", bd=0, cursor="hand2",
            highlightthickness=1, highlightbackground="#4B668D",
            font=(ds.Font.FAMILY, 10),
        )

    def _wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _on_resize(self, _event=None):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(60, self.render)

    def _category_changed(self):
        self.app._home_category_changed()

    def set_last(self, value):
        self.last_backup = value
        self._on_resize()

    def set_status(self, title, detail, icon, color):
        self.status = (title, detail, icon, color)
        self._on_resize()

    def set_destination(self, title, space, path, connected, usage):
        self.destination = (title, space, path, connected, usage)
        self._on_resize()

    def _text(self, x, y, text, size, color="#F4F7FF", weight="normal", **options):
        anchor = options.pop("anchor", "nw")
        self.canvas.create_text(x, y, text=text, anchor=anchor, fill=color,
                                font=(ds.Font.FAMILY, size, weight), **options)

    def render(self):
        if not self.winfo_exists():
            return
        self._render_job = None
        width = max(self.canvas.winfo_width(), 720)
        margin = 28
        gap = 16
        top_y = 146
        top_h = 337
        usable = width - 2 * margin
        compact = usable < 900
        if compact:
            left = (margin, top_y, width - margin, top_y + top_h)
            right = (margin, top_y + top_h + gap, width - margin,
                     top_y + 2 * top_h + gap)
            section_y = right[3] + 26
        else:
            card_w = (usable - gap) // 2
            left = (margin, top_y, margin + card_w, top_y + top_h)
            right = (margin + card_w + gap, top_y, width - margin, top_y + top_h)
            section_y = 507
        left_w = left[2] - left[0]
        right_w = right[2] - right[0]
        right_y = right[1]
        cols = 3 if usable >= 960 else 2
        tile_gap = 13
        tile_w = (usable - tile_gap * (cols - 1)) // cols
        tile_y = section_y + (105 if compact else 83)
        tile_h = 139
        rows = math.ceil(len(self.CATEGORIES) / cols)
        height = max(tile_y + rows * (tile_h + tile_gap) + 60, self.canvas.winfo_height())

        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        top, bottom = _rgb("#16264A"), _rgb("#0C172B")
        for y in range(height):
            t = y / max(height - 1, 1)
            draw.line((0, y, width, y), fill=(*_blend(top, bottom, t), 255))
        _glow(image, (width * .36, -240, width * 1.15, 300), "#435DDF", 138, 120)
        _glow(image, (width * .66, 70, width * 1.25, 440), "#7132BA", 73, 115)
        _glow(image, (10, 70, width * .54, 490), "#087AB9", 40, 100)
        grid = Image.new("RGBA", image.size)
        grid_draw = ImageDraw.Draw(grid)
        for x in range(0, width, 90):
            grid_draw.line((x, 0, x, height), fill=(145, 177, 239, 8), width=1)
        image.alpha_composite(grid)
        _glass(image, left, tint="#213859", accent="#45D9FF")
        _glass(image, right, tint="#20345D", accent="#8B79F2")
        _glass(image, (right[0] + 18, right_y + 76, right[2] - 18, right_y + 240), tint="#152640", radius=11)
        _glass(image, (right[0] + 18, right_y + 285, right[2] - 18, right_y + 325), tint="#294363", radius=9)
        # Anneau lumineux du statut.
        cx, cy = left[0] + 112, top_y + 162
        ring = Image.new("RGBA", (190, 190))
        ring_draw = ImageDraw.Draw(ring)
        ring_draw.arc((24, 24, 166, 166), 0, 360,
                      fill=(42, 223, 255, 145), width=9)
        image.alpha_composite(ring.filter(ImageFilter.GaussianBlur(15)), (cx - 95, cy - 95))
        ImageDraw.Draw(image).arc((cx - 69, cy - 69, cx + 69, cy + 69), 0, 360,
                                  fill="#42D8FA", width=7)
        # Tuile de sélection : les reflets restent fins pour garder les textes lisibles.
        for index, (key, _icon, _title, _subtitle, _detail, tint) in enumerate(self.CATEGORIES):
            col, row = index % cols, index // cols
            x = margin + col * (tile_w + tile_gap)
            y = tile_y + row * (tile_h + tile_gap)
            selected = self.category_vars[key].get()
            _glass(image, (x, y, x + tile_w, y + tile_h),
                   tint="#1B3455" if selected else "#1B2942", accent=tint if selected else None)
            icon_fill = (*_rgb(tint), 88 if selected else 48)
            icon_layer = Image.new("RGBA", image.size)
            ImageDraw.Draw(icon_layer).rounded_rectangle((x + 20, y + 24, x + 83, y + 87),
                                                         radius=13, fill=icon_fill,
                                                         outline=(*_rgb(tint), 150), width=1)
            image.alpha_composite(icon_layer)
        self._background = ImageTk.PhotoImage(image.convert("RGB"))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._background, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        if height <= self.canvas.winfo_height() + 2:
            if self.scrollbar.winfo_manager():
                self.scrollbar.pack_forget()
        elif not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)

        self._text(margin, 23, "SAUVEGARDE PC  /  VUE D’ENSEMBLE", 9, "#7ECDF9", "bold")
        self._text(margin, 48, "Sauvegarde PC", 34, "#FFFFFF", "bold")
        self._text(margin + 2, 108, "Protégez ce qui compte. Simplement.", 12, "#BDD0EA")
        if not compact:
            self._text(width - margin, 63, "PLUS LOIN POUR VOS SOUVENIRS  ───", 8,
                       "#93AFE0", anchor="ne")

        self._text(left[0] + 25, top_y + 22, "Vue d’ensemble", 16, "#FFFFFF", "bold")
        self._text(cx - 18, cy - 30, self.status[2], 37, self.status[3], "bold")
        status_x = left[0] + 204
        self._text(status_x, top_y + 104, self.status[0], 15, "#FFFFFF", "bold")
        self._text(status_x, top_y + 143, self.status[1], 10, "#C6D5EB")
        self._text(status_x, top_y + 169, "Dernière sauvegarde : " + self.last_backup,
                   10, "#AFC5E2")
        self.canvas.create_window(left[0] + 28, top_y + 266, window=self.button,
                                  anchor="nw", width=left_w - 56, height=60)

        self._text(right[0] + 22, right_y + 22, "▤  Destination", 16, "#FFFFFF", "bold")
        self._text(right[0] + 44, right_y + 103, "▣", 28, "#92B6FF")
        dest_x = right[0] + 106
        self._text(dest_x, right_y + 96, self.destination[0], 12, "#F4F7FF", "bold")
        self._text(dest_x, right_y + 125, self.destination[1], 10, "#BECCE5")
        status_color = "#79E6AE" if self.destination[3] else "#F5BD77"
        self._text(right[2] - 37, right_y + 94,
                   "●  Connecté" if self.destination[3] else "●  À choisir", 9,
                   status_color, anchor="ne")
        track_x2 = right[2] - 40
        self.canvas.create_rectangle(dest_x, right_y + 153, track_x2, right_y + 161,
                                     fill="#324B73", outline="")
        self.canvas.create_rectangle(dest_x, right_y + 153,
                                     dest_x + (track_x2 - dest_x) * self.destination[4],
                                     right_y + 161, fill="#76DDF9", outline="")
        self.canvas.create_line(right[0] + 42, right_y + 185, right[2] - 42, right_y + 185,
                                fill="#416084")
        self._text(right[0] + 45, right_y + 199, "Chemin", 9, "#B8CDE7")
        self._text(right[2] - 42, right_y + 199,
                   self.destination[2] or "Aucun dossier sélectionné", 9,
                   "#DBE7F7", anchor="ne")
        self.canvas.create_window(right[0] + 24, right_y + 289, window=self.change_button,
                                  anchor="nw", width=right_w - 48, height=32)

        self._text(margin, section_y, "Éléments à sauvegarder", 16, "#FFFFFF", "bold")
        self._text(margin, section_y + 31, "Sélectionnez les catégories à inclure dans votre sauvegarde.",
                   11, "#C0D0E8")
        count = sum(var.get() for var in self.category_vars.values())
        self._text(width - margin, section_y + (62 if compact else 33),
                   f"{count} catégories sélectionnées", 10,
                   "#9FC9FF", anchor="ne")
        for index, (key, icon, title, subtitle, detail, tint) in enumerate(self.CATEGORIES):
            col, row = index % cols, index // cols
            x = margin + col * (tile_w + tile_gap)
            y = tile_y + row * (tile_h + tile_gap)
            tag = f"tile_{key}"
            self._text(x + 37, y + 42, icon, 24, tint, "bold", tags=tag)
            self._text(x + 99, y + 26, title, 11, "#FFFFFF", "bold", tags=tag)
            self._text(x + 99, y + 55, subtitle, 9, "#C6D5E9", tags=tag)
            self._text(x + 99, y + 80, detail, 8, "#A8BEDB", tags=tag)
            self.canvas.create_window(x + tile_w - 30, y + 25,
                                      window=self.category_checks[key], anchor="center",
                                      width=25, height=25)
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, k=key: self._toggle(k))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.configure(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.configure(cursor=""))
        footer_y = tile_y + rows * (tile_h + tile_gap) + 6
        self._text(margin, footer_y, "◇   Une sauvegarde aujourd’hui. Un esprit tranquille demain.",
                   9, "#9CB8DB")
        self._text(width - margin, footer_y, "AURORA GRID  ───  SIMPLEMENT PLUS SÛR",
                   8, "#9CB8DB", anchor="ne")

    def _toggle(self, key):
        var = self.category_vars[key]
        var.set(not var.get())
        self._category_changed()


def destination_details(value):
    if not value:
        return "Aucun dossier sélectionné", "Choisissez un support externe", "", False, 0.0
    path = Path(value)
    title = path.name or str(path.anchor)
    short_path = value if len(value) <= 38 else "…" + value[-37:]
    try:
        usage = shutil.disk_usage(path if path.exists() else path.anchor)
        return (title, f"{usage.free / (1024 ** 3):.1f} Go disponibles",
                short_path, True, usage.used / usage.total if usage.total else 0.0)
    except (OSError, ValueError):
        return title, "Espace disponible indisponible", short_path, False, 0.0
