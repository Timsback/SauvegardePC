"""
Design System "Cyber Blue" — système de design réutilisable pour Sauvegarde PC.

Direction artistique : sombre, moderne, professionnel, premium, technologique,
légèrement futuriste (fond bleu nuit quasi noir, accent bleu électrique, touches
cyan pour les éléments actifs, vert/orange/rouge réservés aux statuts).

Ce module ne modifie AUCUNE page existante : il définit seulement les tokens
(couleurs, typographie, espacements, rayons) et des composants réutilisables
(cartes, badges, indicateurs de statut, barres de progression, anneau circulaire,
bascules, champs, item de sidebar, notifications, dialogues) ainsi que des
utilitaires d'animation. `main.py` pourra ensuite l'importer page par page.

Contraintes Tkinter à connaître :
- Pas de vraies ombres portées ni de coins arrondis natifs : les cartes sont
  dessinées sur un `Canvas` (polygone lissé pour les coins, contours dégradés
  vers le fond de page pour simuler une ombre douce).
- Pas de canal alpha par widget : les micro-animations de survol/focus
  interpolent des couleurs plutôt qu'une opacité. Seules les fenêtres
  indépendantes (Toast, ConfirmDialog) ont un vrai fondu, via
  `wm_attributes("-alpha", ...)`.

Aperçu visuel : `python app/design_system.py`
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

import ttkbootstrap as ttkb
from ttkbootstrap.style.theme import Theme

# =============================================================================
# Thème ttkbootstrap "aurora" — bleu nuit + cyan électrique
# =============================================================================
APP_THEME = "aurora-dark"

Theme(
    name="aurora",
    primary="#368FF5",   # bleu Aurora — action principale
    success="#24DFA3",   # vert menthe — protection / sauvegarde réussie
    info="#44D9F4",      # cyan — éléments actifs / secondaires
    warning="#F5A623",   # orange — avertissement
    danger="#F0426B",    # rouge/magenta doux — erreur
    neutral="#1B3151",
    dark={"background": "#101C32", "foreground": "#F4F7FF"},
).register()


# =============================================================================
# Tokens — couleurs
# =============================================================================
class Color:
    # Fonds
    BG_APP = "#101C32"
    BG_SIDEBAR = "#101A2D"
    BG_CARD = "#1A2D4D"
    BG_CARD_HOVER = "#233C61"
    BG_CARD_SOFT = "#172740"
    BG_INPUT = "#16253F"
    BG_CONSOLE = "#0D172A"

    # Bordures
    BORDER_SUBTLE = "#344D70"
    BORDER_DEFAULT = "#4B6C99"

    # Texte
    TEXT_PRIMARY = "#F4F7FF"
    TEXT_SECONDARY = "#BCCCE2"
    TEXT_MUTED = "#9BB2D0"
    TEXT_ON_ACCENT = "#FFFFFF"

    # Accent (bleu électrique)
    ACCENT = "#368FF5"
    ACCENT_HOVER = "#55A9FF"
    ACCENT_PRESSED = "#226BC1"
    ACCENT_SOFT = "#203A68"

    # Cyan (éléments actifs / secondaires)
    CYAN = "#44D9F4"
    CYAN_SOFT = "#194A63"

    # États
    SUCCESS = "#24DFA3"
    SUCCESS_SOFT = "#073C37"
    WARNING = "#F5A623"
    WARNING_SOFT = "#2E2110"
    DANGER = "#F0426B"
    DANGER_SOFT = "#301321"


# =============================================================================
# Tokens — typographie
# =============================================================================
class Font:
    """Police système fiable sur Windows (Segoe UI). Les tuples sont figés à
    l'import ; appeler `resolve()` après création de la fenêtre racine pour
    basculer sur "Segoe UI Variable Text" si disponible (Windows 11 22H2+)."""

    FAMILY = "Segoe UI"

    DISPLAY = (FAMILY, 32, "bold")      # grand nombre / statut principal (ex. "87 %")
    H1 = (FAMILY, 19, "bold")           # titre de page
    H2 = (FAMILY, 12, "bold")           # titre de section
    BODY_LG = (FAMILY, 12, "normal")    # information principale
    BODY = (FAMILY, 10, "normal")       # texte courant
    BODY_SM = (FAMILY, 9, "normal")     # information secondaire
    LABEL = (FAMILY, 8, "bold")         # labels / badges (souvent MAJUSCULES)
    BUTTON = (FAMILY, 10, "bold")       # boutons

    @classmethod
    def resolve(cls):
        try:
            available = set(tkfont.families())
        except tk.TclError:
            return
        family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in available else "Segoe UI"
        cls.FAMILY = family
        cls.DISPLAY = (family, 32, "bold")
        cls.H1 = (family, 19, "bold")
        cls.H2 = (family, 12, "bold")
        cls.BODY_LG = (family, 12, "normal")
        cls.BODY = (family, 10, "normal")
        cls.BODY_SM = (family, 9, "normal")
        cls.LABEL = (family, 8, "bold")
        cls.BUTTON = (family, 10, "bold")


# =============================================================================
# Tokens — espacements et rayons (grille de base 4px)
# =============================================================================
class Space:
    XS = 4
    SM = 8
    MD = 12
    BASE = 16
    LG = 20
    XL = 24
    XXL = 32
    XXXL = 40


class Radius:
    XS = 6     # petites puces
    SM = 10    # champs, boutons
    MD = 14    # cartes standards
    LG = 18    # cartes héro / dialogues
    PILL = 999  # badges, bascules


DURATION_FAST = 150
DURATION_BASE = 200
DURATION_SLOW = 300


# =============================================================================
# Animation — easing + interpolation
# =============================================================================
def ease_out_cubic(t: float) -> float:
    return 1 - pow(1 - t, 3)


def ease_out_quad(t: float) -> float:
    return 1 - (1 - t) * (1 - t)


def lerp_color(c1: str, c2: str, t: float) -> str:
    """Interpole linéairement entre deux couleurs hexadécimales #rrggbb."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


class Animator:
    """Anime une valeur numérique de `start` à `end` sur `duration` ms (~60 im/s)
    via `widget.after`, sans dépendance externe. Une nouvelle animation annule
    automatiquement la précédente sur le même Animator."""

    def __init__(self, widget):
        self._widget = widget
        self._job = None

    def stop(self):
        if self._job is not None:
            try:
                self._widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def animate(self, start, end, duration=DURATION_BASE, easing=ease_out_cubic, on_update=None, on_done=None):
        self.stop()
        if duration <= 0:
            if on_update:
                on_update(end)
            if on_done:
                on_done()
            return
        steps = max(1, duration // 16)
        frame = {"i": 0}

        def tick():
            frame["i"] += 1
            t = min(1.0, frame["i"] / steps)
            value = start + (end - start) * easing(t)
            try:
                if on_update:
                    on_update(value)
            except tk.TclError:
                return  # widget détruit pendant l'animation
            if t >= 1.0:
                self._job = None
                if on_done:
                    on_done()
            else:
                self._job = self._widget.after(16, tick)

        tick()


def animate_progressbar(pbar: ttkb.Progressbar, target: float, duration=DURATION_SLOW, animator: "Animator | None" = None):
    animator = animator or Animator(pbar)
    start = float(pbar["value"] or 0)
    animator.animate(start, target, duration, on_update=lambda v: pbar.configure(value=v))
    return animator


def animate_meter(meter: ttkb.Meter, target: int, duration=DURATION_SLOW, animator: "Animator | None" = None):
    animator = animator or Animator(meter)
    try:
        start = meter.amountusedvar.get()
    except Exception:
        start = 0
    animator.animate(start, target, duration, on_update=lambda v: meter.configure(amountused=round(v)))
    return animator


def bind_hover_color(widget, base_bg: str, hover_bg: str, duration=DURATION_FAST, apply=None):
    """Micro-animation de survol : interpole la couleur de fond de `widget`
    entre `base_bg` et `hover_bg`. `apply(widget, color)` personnalise
    l'application (par défaut `widget.configure(bg=color)`)."""
    apply = apply or (lambda w, c: w.configure(bg=c))
    animator = Animator(widget)

    widget.bind("<Enter>", lambda e: animator.animate(
        0.0, 1.0, duration, on_update=lambda t: apply(widget, lerp_color(base_bg, hover_bg, t))
    ), add="+")
    widget.bind("<Leave>", lambda e: animator.animate(
        1.0, 0.0, duration, on_update=lambda t: apply(widget, lerp_color(base_bg, hover_bg, t))
    ), add="+")
    return animator


# =============================================================================
# Cartes — coins arrondis + ombre douce simulée
# =============================================================================
def draw_rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def draw_hgradient_rect(canvas: tk.Canvas, x1, y1, x2, y2, color1, color2, steps=None, tags=()):
    """Dessine un dégradé horizontal simple (bande de rectangles fins) — utilisé
    pour les liserés d'accent des cartes/boutons "cyber" (pas d'arrondi ici :
    prévu pour de fines bandes internes)."""
    width = max(1, int(x2 - x1))
    steps = steps or max(2, min(width, 48))
    band = width / steps
    for i in range(steps):
        t = i / max(1, steps - 1)
        sx1 = x1 + i * band
        sx2 = x1 + (i + 1) * band + 1
        canvas.create_rectangle(sx1, y1, sx2, y2, fill=lerp_color(color1, color2, t), outline="", tags=tags)


def draw_vgradient_rect(canvas: tk.Canvas, x1, y1, x2, y2, color1, color2, steps=None, tags=()):
    """Variante verticale de `draw_hgradient_rect` (liseré latéral, bandeau d'en-tête)."""
    height = max(1, int(y2 - y1))
    steps = steps or max(2, min(height, 48))
    band = height / steps
    for i in range(steps):
        t = i / max(1, steps - 1)
        sy1 = y1 + i * band
        sy2 = y1 + (i + 1) * band + 1
        canvas.create_rectangle(x1, sy1, x2, sy2, fill=lerp_color(color1, color2, t), outline="", tags=tags)


class GlowButton(tk.Canvas):
    """Bouton "cyber" dessiné (Canvas) : remplissage plein + halo lumineux animé
    au survol, assombrissement net à l'appui, état désactivé sobre. Utilisé pour
    les actions principales (ex. "Lancer la sauvegarde complète") là où un
    bouton ttk classique manque de présence."""

    _VARIANTS = {
        "primary": (Color.ACCENT, Color.ACCENT_HOVER, Color.ACCENT_PRESSED, Color.CYAN),
        "warning": (Color.WARNING, "#FFC266", "#C97F12", Color.WARNING),
        "success": (Color.SUCCESS, "#5CF0C2", "#159F76", Color.SUCCESS),
    }

    def __init__(self, parent, text, command=None, variant="primary", page_bg=Color.BG_APP,
                 width=280, height=52, icon=None, font=None, state="normal", **kwargs):
        super().__init__(parent, width=width, height=height, bg=page_bg, highlightthickness=0, bd=0, **kwargs)
        self._text = text
        self._icon = icon
        self._command = command
        self._page_bg = page_bg
        self._variant = variant
        self._font = font or Font.BUTTON
        self._state = state
        self._pressed = False
        self._hover_t = 0.0
        self._animator = Animator(self)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()
        self._sync_cursor()

    def _sync_cursor(self):
        try:
            self.configure(cursor="hand2" if self._state == "normal" else "arrow")
        except tk.TclError:
            pass

    def set_state(self, state: str):
        self._state = state
        self._sync_cursor()
        self._draw()

    def _on_enter(self, _e):
        if self._state != "normal":
            return
        self._animator.animate(self._hover_t, 1.0, DURATION_FAST, on_update=self._set_hover)

    def _on_leave(self, _e):
        self._pressed = False
        if self._state != "normal":
            return
        self._animator.animate(self._hover_t, 0.0, DURATION_FAST, on_update=self._set_hover)

    def _set_hover(self, t):
        self._hover_t = t
        self._draw()

    def _on_press(self, _e):
        if self._state != "normal":
            return
        self._pressed = True
        self._draw()

    def _on_release(self, _e):
        if self._state != "normal":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        base, hover, pressed, glow = self._VARIANTS.get(self._variant, self._VARIANTS["primary"])
        radius = min(Radius.MD, h / 2)

        if self._state != "normal":
            fill = Color.BG_CARD_SOFT
            text_fg = Color.TEXT_MUTED
            draw_rounded_rect(self, 1, 1, w - 1, h - 1, radius, fill=fill, outline=Color.BORDER_SUBTLE, width=1)
        else:
            if self._pressed:
                fill = pressed
            else:
                fill = lerp_color(base, hover, self._hover_t)
            text_fg = Color.TEXT_ON_ACCENT
            if self._hover_t > 0.01 and not self._pressed:
                # Halo lumineux : un contour légèrement plus grand, teinté vers le
                # fond, simule une lueur douce sans vrai canal alpha.
                halo = lerp_color(self._page_bg, glow, 0.55 * self._hover_t)
                draw_rounded_rect(self, -2, -2, w + 2, h + 2, radius + 2, fill="", outline=halo, width=3)
            draw_rounded_rect(self, 1, 1, w - 1, h - 1, radius, fill=fill, outline="")

        cx = w / 2
        if self._icon:
            label = f"{self._icon}   {self._text}"
        else:
            label = self._text
        self.create_text(cx, h / 2, text=label, fill=text_fg, font=self._font, anchor="center")


class Card(tk.Frame):
    """Carte native, stable et réactive pendant le redimensionnement.

    Les anciennes cartes imbriquaient un Canvas dans le Canvas défilant de
    l'accueil. Deux redimensionnements simultanés pouvaient garder des tailles
    transitoires et peindre le contenu au mauvais endroit. Cette implémentation
    s'appuie uniquement sur les Frames natives et la propagation Tk.
    """

    def __init__(self, parent, radius=Radius.MD, elevation=1, page_bg=Color.BG_APP,
                 surface=Color.BG_CARD, border=Color.BORDER_SUBTLE, padding=Space.LG,
                 top_accent=False, accent_colors=(Color.ACCENT, Color.CYAN), **kwargs):
        super().__init__(parent, bg=page_bg, highlightthickness=0, bd=0, **kwargs)
        self._radius = radius
        self._surface = surface
        self._border = border
        self._page_bg = page_bg
        self._elevation = max(0, min(3, elevation))
        self._reveal = 1.0  # 0 = bordure/ombre invisibles (matérialisation), 1 = pleinement visible
        # `padding` accepte un nombre (marge uniforme) ou un couple (pad_x, pad_y),
        # comme le `padx`/`pady` de pack.
        self._pad_x, self._pad_y = padding if isinstance(padding, (tuple, list)) else (padding, padding)

        outer = tk.Frame(self, bg=surface)
        outer.pack(fill=tk.BOTH, expand=True, padx=self._pad_x, pady=self._pad_y)

        if top_accent:
            self._accent_strip = tk.Canvas(outer, height=3, bg=surface, highlightthickness=0, bd=0)
            self._accent_strip.pack(fill=tk.X, side=tk.TOP)
            c1, c2 = accent_colors
            self._accent_strip.bind(
                "<Configure>",
                lambda e, c1=c1, c2=c2: (
                    self._accent_strip.delete("all"),
                    draw_hgradient_rect(self._accent_strip, 0, 0, e.width, 3, c1, c2),
                ),
            )

        self.body = tk.Frame(
            outer, bg=surface, bd=0,
            highlightthickness=1 if self._elevation else 0,
            highlightbackground=border, highlightcolor=border,
        )
        self.body.pack(fill=tk.BOTH, expand=True)

    def set_border(self, color: str):
        """Change la bordure sans déclencher de redessin ni de reflow."""
        self._border = color
        self.body.configure(highlightbackground=color, highlightcolor=color)

    def set_reveal(self, t: float):
        """Anime seulement la bordure : la géométrie reste inchangée."""
        self._reveal = t
        color = lerp_color(self._page_bg, self._border, t)
        self.body.configure(highlightbackground=color, highlightcolor=color)


def reveal_card(card: Card, delay: int = 0, duration: int = DURATION_BASE):
    """Fait apparaître progressivement la bordure d'une carte.

    Le contenu et la géométrie sont immédiatement disponibles : l'animation ne
    concerne que la couleur de bordure et ne peut donc pas perturber la mise en
    page pendant un redimensionnement.
    """
    def start():
        card.update_idletasks()
        card.set_reveal(0.0)

        def on_done():
            card.update_idletasks()

        Animator(card).animate(0.0, 1.0, duration, on_update=card.set_reveal, on_done=on_done)

    card.after(delay, start)


# =============================================================================
# Badge (pastille pill)
# =============================================================================
class Badge(tk.Canvas):
    """Pastille arrondie pour un statut court (succès / avertissement / erreur /
    info / accent / neutre)."""

    _VARIANTS = {
        "success": (Color.SUCCESS_SOFT, Color.SUCCESS),
        "warning": (Color.WARNING_SOFT, Color.WARNING),
        "danger": (Color.DANGER_SOFT, Color.DANGER),
        "info": (Color.CYAN_SOFT, Color.CYAN),
        "accent": (Color.ACCENT_SOFT, Color.ACCENT),
        "neutral": (Color.BG_CARD_SOFT, Color.TEXT_SECONDARY),
    }

    def __init__(self, parent, text, variant="neutral", page_bg=Color.BG_CARD, **kwargs):
        bg, fg = self._VARIANTS.get(variant, self._VARIANTS["neutral"])
        super().__init__(parent, bg=page_bg, highlightthickness=0, bd=0, **kwargs)
        self._text = text
        self._bg = bg
        self._fg = fg
        self._draw()

    def _draw(self):
        self.delete("all")
        pad_x, pad_y = Space.SM, 4
        probe = self.create_text(0, 0, text=self._text, font=Font.LABEL, anchor="nw")
        bbox = self.bbox(probe)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        w, h = tw + 2 * pad_x, th + 2 * pad_y
        self.delete("all")
        self.configure(width=w, height=h)
        draw_rounded_rect(self, 0, 0, w, h, h / 2, fill=self._bg, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg, font=Font.LABEL, anchor="center")

    def set_text(self, text, variant=None):
        self._text = text
        if variant:
            self._bg, self._fg = self._VARIANTS.get(variant, (self._bg, self._fg))
        self._draw()


# =============================================================================
# Indicateur de statut — la FORME du glyphe change selon l'état, pas que la couleur
# =============================================================================
class StatusIndicator(tk.Canvas):
    """Pastille glyphe pour un état : succès (coche), avertissement (!),
    erreur (croix), en cours (anneau qui tourne), prêt (tiret). Reste lisible
    sans dépendre uniquement de la couleur (accessibilité daltonisme)."""

    _STYLES = {
        "success": (Color.SUCCESS, "check"),
        "warning": (Color.WARNING, "bang"),
        "danger": (Color.DANGER, "cross"),
        "running": (Color.ACCENT, "spinner"),
        "idle": (Color.TEXT_MUTED, "dash"),
    }

    def __init__(self, parent, state="idle", size=20, page_bg=Color.BG_CARD, **kwargs):
        super().__init__(parent, width=size, height=size, bg=page_bg, highlightthickness=0, bd=0, **kwargs)
        self._size = size
        self._angle = 0
        self._spin_job = None
        self.set_state(state)

    def set_state(self, state):
        self._stop_spin()
        self._color, self._glyph = self._STYLES.get(state, self._STYLES["idle"])
        self._draw()
        if self._glyph == "spinner":
            self._spin()

    def _draw(self, angle=0):
        self.delete("all")
        s = self._size
        cx = cy = s / 2
        r = s / 2 - 1
        if self._glyph == "spinner":
            self.create_oval(1, 1, s - 1, s - 1, outline=Color.BORDER_DEFAULT, width=2)
            self.create_arc(1, 1, s - 1, s - 1, start=angle, extent=100, style="arc", outline=self._color, width=2)
            return
        self.create_oval(1, 1, s - 1, s - 1, fill=self._color, outline="")
        fg = Color.BG_APP
        if self._glyph == "check":
            self.create_line(
                cx - r * 0.5, cy, cx - r * 0.1, cy + r * 0.4, cx + r * 0.55, cy - r * 0.35,
                fill=fg, width=2, capstyle="round", joinstyle="round",
            )
        elif self._glyph == "bang":
            self.create_line(cx, cy - r * 0.35, cx, cy + r * 0.12, fill=fg, width=2, capstyle="round")
            self.create_oval(cx - 1.2, cy + r * 0.35, cx + 1.2, cy + r * 0.35 + 2.4, fill=fg, outline="")
        elif self._glyph == "cross":
            self.create_line(cx - r * 0.4, cy - r * 0.4, cx + r * 0.4, cy + r * 0.4, fill=fg, width=2, capstyle="round")
            self.create_line(cx - r * 0.4, cy + r * 0.4, cx + r * 0.4, cy - r * 0.4, fill=fg, width=2, capstyle="round")
        elif self._glyph == "dash":
            self.create_line(cx - r * 0.4, cy, cx + r * 0.4, cy, fill=fg, width=2, capstyle="round")

    def _spin(self):
        self._angle = (self._angle - 24) % 360
        self._draw(self._angle)
        self._spin_job = self.after(60, self._spin)

    def _stop_spin(self):
        if self._spin_job is not None:
            try:
                self.after_cancel(self._spin_job)
            except tk.TclError:
                pass
            self._spin_job = None

    def destroy(self):
        self._stop_spin()
        super().destroy()


# =============================================================================
# Anneau de progression circulaire (remplace/rethème ttkbootstrap Meter)
# =============================================================================
class CircularProgress(tk.Canvas):
    """Anneau de progression circulaire animé, avec libellés centrés (valeur +
    sous-texte). Utilisé pour le statut principal du tableau de bord."""

    def __init__(self, parent, size=160, thickness=14, page_bg=Color.BG_APP,
                 track=Color.BG_CARD_SOFT, color=Color.ACCENT, **kwargs):
        super().__init__(parent, width=size, height=size, bg=page_bg, highlightthickness=0, bd=0, **kwargs)
        self._size = size
        self._thickness = thickness
        self._track = track
        self._color = color
        self._value = 0.0
        self._animator = Animator(self)
        self._center_text = None
        self._sub_text = None
        self._draw(0)

    def _draw(self, value):
        self.delete("all")
        s, t = self._size, self._thickness
        pad = t / 2 + 2
        cx = cy = s / 2
        # Repères techniques discrets : l'anneau reste lisible mais prend la
        # présence d'un indicateur de protection "cyber" plutôt qu'une simple jauge.
        outer = 3
        self.create_oval(outer, outer, s - outer, s - outer, outline=Color.BORDER_SUBTLE, width=1)
        for degree in range(0, 360, 30):
            angle = math.radians(degree - 90)
            r1 = s / 2 - 6
            r2 = r1 - (5 if degree % 90 == 0 else 3)
            self.create_line(
                cx + math.cos(angle) * r1, cy + math.sin(angle) * r1,
                cx + math.cos(angle) * r2, cy + math.sin(angle) * r2,
                fill=Color.BORDER_DEFAULT, width=1,
            )
        self.create_oval(pad, pad, s - pad, s - pad, outline=self._track, width=t)
        extent = -3.6 * value
        if abs(extent) > 0.5:
            self.create_arc(pad, pad, s - pad, s - pad, start=90, extent=extent,
                             style="arc", outline=self._color, width=t)
            # Deux points de terminaison rendent la progression identifiable
            # sans dépendre de la couleur seule.
            end_angle = math.radians(90 + extent)
            radius = (s - 2 * pad) / 2
            x = cx + math.cos(end_angle) * radius
            y = cy - math.sin(end_angle) * radius
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=self._color, outline="")
        self.create_oval(pad + t + 3, pad + t + 3, s - pad - t - 3, s - pad - t - 3,
                         outline=Color.BORDER_SUBTLE, width=1)
        if self._center_text is not None:
            self.create_text(s / 2, s / 2 - 8, text=self._center_text, fill=Color.TEXT_PRIMARY, font=Font.DISPLAY)
        if self._sub_text is not None:
            self.create_text(s / 2, s / 2 + 26, text=self._sub_text, fill=Color.TEXT_SECONDARY, font=Font.BODY_SM)

    def set_color(self, color):
        self._color = color
        self._draw(self._value)

    def animate_to(self, target, duration=DURATION_SLOW, center_text=None, sub_text=None):
        if center_text is not None:
            self._center_text = center_text
        if sub_text is not None:
            self._sub_text = sub_text
        start = self._value

        def on_update(v):
            self._value = v
            self._draw(v)

        self._animator.animate(start, target, duration, on_update=on_update)


# =============================================================================
# Barre de progression (wrapper thémé)
# =============================================================================
def progress_bar(parent, determinate=True, bootstyle="primary", length=200):
    mode = "determinate" if determinate else "indeterminate"
    style = bootstyle if determinate else f"{bootstyle}-striped"
    return ttkb.Progressbar(parent, mode=mode, bootstyle=style, length=length, maximum=100)


# =============================================================================
# Bascule (toggle) et champ (field)
# =============================================================================
def toggle_switch(parent, variable, text="", command=None, bootstyle="round-toggle"):
    return ttkb.Checkbutton(parent, text=text, variable=variable, bootstyle=bootstyle, command=command)


class Field(tk.Frame):
    """Champ de saisie à bordure arrondie, avec halo d'accent animé au focus."""

    def __init__(self, parent, textvariable=None, width=24, page_bg=Color.BG_APP,
                 show=None, state="normal", **kwargs):
        super().__init__(parent, bg=page_bg, highlightthickness=0)
        self._current_border = Color.BORDER_DEFAULT
        self._animator = Animator(self)

        self.canvas = tk.Canvas(self, bg=page_bg, highlightthickness=0, bd=0, height=34)
        self.canvas.pack(fill=tk.X, expand=True)

        self.entry = tk.Entry(
            self.canvas, textvariable=textvariable, bg=Color.BG_INPUT, fg=Color.TEXT_PRIMARY,
            insertbackground=Color.TEXT_PRIMARY, relief="flat", font=Font.BODY, width=width,
            show=show, state=state, disabledbackground=Color.BG_INPUT,
            disabledforeground=Color.TEXT_SECONDARY, readonlybackground=Color.BG_INPUT, **kwargs
        )
        self._window = self.canvas.create_window(12, 17, anchor="w", window=self.entry)
        self.canvas.bind("<Configure>", self._redraw)
        self.entry.bind("<FocusIn>", lambda e: self._animate_border(Color.ACCENT))
        self.entry.bind("<FocusOut>", lambda e: self._animate_border(Color.BORDER_DEFAULT))
        self._redraw()

    def _redraw(self, event=None):
        w = self.canvas.winfo_width() or 200
        h = 34
        self.canvas.delete("field_bg")
        draw_rounded_rect(self.canvas, 1, 1, w - 1, h - 1, Radius.SM,
                           fill=Color.BG_INPUT, outline=self._current_border, width=1, tags="field_bg")
        self.canvas.tag_lower("field_bg")
        self.canvas.coords(self._window, 12, h / 2)

    def _animate_border(self, target):
        start = self._current_border
        self._animator.animate(0.0, 1.0, DURATION_FAST,
                                on_update=lambda t: self._set_border(lerp_color(start, target, t)))

    def _set_border(self, color):
        self._current_border = color
        self._redraw()


def combobox(parent, textvariable, values, bootstyle="primary", **kwargs):
    return ttkb.Combobox(parent, textvariable=textvariable, values=values, bootstyle=bootstyle, state="readonly", **kwargs)


# =============================================================================
# Sidebar — item de navigation avec barre d'accent animée
# =============================================================================
def build_sidebar_nav_item(sidebar, icon, label, command, page_bg=Color.BG_SIDEBAR):
    """Construit une ligne de navigation : barre d'accent (visible seulement sur
    l'item actif, transition douce) + icône + libellé. Renvoie (row, accent_bar,
    button) — l'appelant gère l'état actif/inactif via `set_nav_item_active`."""
    row = tk.Frame(sidebar, bg=page_bg)
    row.pack(fill=tk.X, padx=Space.MD, pady=2)

    accent = tk.Frame(row, bg=page_bg, width=3)
    accent.pack(side=tk.LEFT, fill=tk.Y)

    btn = ttkb.Button(row, text=f"   {icon}   {label}", bootstyle=f"{page_bg}", command=command)
    btn.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=10)

    accent._ds_animator = Animator(accent)
    return row, accent, btn


def set_nav_item_active(accent_bar, button, active: bool, page_bg=Color.BG_SIDEBAR):
    animator = getattr(accent_bar, "_ds_animator", None) or Animator(accent_bar)
    target = Color.ACCENT if active else page_bg
    start = accent_bar.cget("bg")
    animator.animate(0.0, 1.0, DURATION_FAST, on_update=lambda t: accent_bar.configure(bg=lerp_color(start, target, t)))
    button.configure(bootstyle=("primary" if active else page_bg))


# =============================================================================
# Transition de page (glissement doux entre deux frames empilées)
# =============================================================================
def crossfade_pages(new_frame, duration=DURATION_BASE):
    """Transition douce lors d'un changement de page : `new_frame` glisse
    légèrement depuis la droite avant de reprendre sa place normale dans la
    grille. (Les widgets internes n'ayant pas de canal alpha, l'effet de
    "fondu" est simulé par ce léger déplacement plutôt qu'une vraie opacité.)"""
    container = new_frame.master
    animator = Animator(new_frame)

    def on_update(t):
        offset = int((1 - t) * 18)
        new_frame.place(in_=container, x=offset, y=0, relwidth=1, relheight=1)

    def on_done():
        new_frame.place_forget()
        new_frame.grid(row=0, column=0, sticky="nsew")
        new_frame.tkraise()

    new_frame.tkraise()
    new_frame.place(in_=container, x=18, y=0, relwidth=1, relheight=1)
    animator.animate(0.0, 1.0, duration, easing=ease_out_cubic, on_update=on_update, on_done=on_done)


# =============================================================================
# Notification transitoire (Toast) — vrai fondu de fenêtre
# =============================================================================
class Toast:
    """Notification transitoire (coin bas-droit), avec un vrai fondu d'entrée/
    sortie (alpha de fenêtre — possible seulement au niveau `Toplevel`)."""

    _VARIANT_COLORS = {
        "success": Color.SUCCESS, "warning": Color.WARNING,
        "danger": Color.DANGER, "info": Color.CYAN,
    }

    _VARIANT_STATE = {"success": "success", "warning": "warning", "danger": "danger", "info": "running"}

    def __init__(self, root, message: str, variant: str = "info", duration: int = 3000, title: str | None = None):
        accent = self._VARIANT_COLORS.get(variant, Color.CYAN)

        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        try:
            self.top.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        card = tk.Frame(self.top, bg=Color.BG_CARD, highlightthickness=1, highlightbackground=Color.BORDER_DEFAULT)
        card.pack(fill=tk.BOTH, expand=True)
        tk.Frame(card, bg=accent, width=4).pack(side=tk.LEFT, fill=tk.Y)
        inner = tk.Frame(card, bg=Color.BG_CARD, padx=Space.MD, pady=Space.SM)
        inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        head = tk.Frame(inner, bg=Color.BG_CARD)
        head.pack(anchor="w", fill=tk.X)
        state = self._VARIANT_STATE.get(variant, "idle")
        if state != "running":
            StatusIndicator(head, state=state, size=16, page_bg=Color.BG_CARD).pack(side=tk.LEFT)
        tk.Label(
            head, text=title or {"success": "Terminé", "warning": "Attention", "danger": "Erreur"}.get(variant, "Info"),
            bg=Color.BG_CARD, fg=Color.TEXT_PRIMARY, font=Font.H2,
        ).pack(side=tk.LEFT, padx=(6 if state != "running" else 0, 0))
        tk.Label(inner, text=message, bg=Color.BG_CARD, fg=Color.TEXT_SECONDARY, font=Font.BODY,
                 wraplength=280, justify=tk.LEFT).pack(anchor="w", pady=(4, 0))

        self.top.update_idletasks()
        w = 340
        h = max(60, card.winfo_reqheight())
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.top.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 64}")

        self._animator = Animator(self.top)
        Animator(self.top).animate(0.0, 0.96, DURATION_BASE, on_update=self._set_alpha)
        self.top.after(duration, self._hide)

    def _set_alpha(self, v):
        try:
            self.top.attributes("-alpha", v)
        except tk.TclError:
            pass

    def _hide(self):
        Animator(self.top).animate(0.96, 0.0, DURATION_BASE, on_update=self._set_alpha, on_done=self.top.destroy)


# =============================================================================
# Dialogue de confirmation — carte arrondie + vrai fondu de fenêtre
# =============================================================================
class ConfirmDialog:
    """Boîte de dialogue de confirmation stylée (alternative à
    `messagebox.askyesno`) : carte arrondie, fondu d'apparition réel."""

    def __init__(self, parent, title, message, variant="warning",
                 confirm_text="Confirmer", cancel_text="Annuler", show_cancel=True):
        self.result = False
        style = {"warning": "warning", "danger": "danger", "success": "success"}.get(variant, "primary")

        self.top = tk.Toplevel(parent)
        self.top.withdraw()
        self.top.title(title)
        self.top.configure(bg=Color.BG_APP)
        self.top.transient(parent)
        self.top.resizable(False, False)
        try:
            self.top.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        card = Card(self.top, radius=Radius.LG, elevation=2, page_bg=Color.BG_APP, padding=Space.XL)
        card.pack(fill=tk.BOTH, expand=True, padx=Space.LG, pady=Space.LG)

        title_row = tk.Frame(card.body, bg=Color.BG_CARD)
        title_row.pack(anchor="w", fill=tk.X)
        glyph_state = {"warning": "warning", "danger": "danger", "success": "success"}.get(variant, "idle")
        StatusIndicator(title_row, state=glyph_state, size=22, page_bg=Color.BG_CARD).pack(side=tk.LEFT)
        tk.Label(title_row, text=title, bg=Color.BG_CARD, fg=Color.TEXT_PRIMARY, font=Font.H1).pack(
            side=tk.LEFT, padx=(Space.SM, 0)
        )
        tk.Label(card.body, text=message, bg=Color.BG_CARD, fg=Color.TEXT_SECONDARY, font=Font.BODY,
                 wraplength=360, justify=tk.LEFT).pack(anchor="w", pady=(Space.SM, Space.LG))

        btn_row = ttkb.Frame(card.body)
        btn_row.pack(anchor="e")
        if show_cancel:
            ttkb.Button(btn_row, text=cancel_text, bootstyle="secondary-outline", command=self._cancel).pack(
                side=tk.LEFT, padx=(0, Space.SM)
            )
        confirm_btn = ttkb.Button(btn_row, text=confirm_text, bootstyle=style, command=self._confirm)
        confirm_btn.pack(side=tk.LEFT)

        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.bind("<Return>", lambda _e: self._confirm())
        self.top.bind("<Escape>", lambda _e: self._cancel())
        self.top.grab_set()
        self.top.update_idletasks()
        self._center_on(parent)
        self.top.deiconify()
        confirm_btn.focus_set()
        Animator(self.top).animate(0.0, 1.0, DURATION_BASE, on_update=self._set_alpha)

    def _set_alpha(self, v):
        try:
            self.top.attributes("-alpha", v)
        except tk.TclError:
            pass

    def _center_on(self, parent):
        parent.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        self.top.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _confirm(self):
        self.result = True
        self.top.destroy()

    def _cancel(self):
        self.result = False
        self.top.destroy()

    @classmethod
    def ask(cls, parent, title, message, variant="warning", confirm_text="Confirmer", cancel_text="Annuler",
            show_cancel=True):
        dialog = cls(parent, title, message, variant, confirm_text, cancel_text, show_cancel)
        parent.wait_window(dialog.top)
        return dialog.result


# =============================================================================
# Aperçu — python app/design_system.py
# =============================================================================
if __name__ == "__main__":
    app = ttkb.Window(title="Aurora — Design System", themename=APP_THEME, size=(1180, 820))
    Font.resolve()
    app.configure(bg=Color.BG_APP)

    root = ttkb.Frame(app, padding=Space.XL)
    root.pack(fill=tk.BOTH, expand=True)

    ttkb.Label(root, text="Design System « Aurora »", font=Font.H1, bootstyle="light").pack(anchor="w")
    ttkb.Label(
        root, text="Sombre · moderne · premium · légèrement futuriste — aperçu des tokens et composants.",
        font=Font.BODY, bootstyle="secondary",
    ).pack(anchor="w", pady=(2, Space.XL))

    row1 = ttkb.Frame(root)
    row1.pack(fill=tk.X)

    # --- Carte statut principal avec anneau circulaire ---
    status_card = Card(row1, elevation=2, padding=Space.XL)
    status_card.pack(side=tk.LEFT, padx=(0, Space.LG))
    ttkb.Label(status_card.body, text="Votre PC est protégé", font=Font.H2, bootstyle=f"inverse-{Color.BG_CARD}").pack(anchor="w")
    ring = CircularProgress(status_card.body, size=150, thickness=13, page_bg=Color.BG_CARD)
    ring.pack(pady=Space.MD)
    ring.animate_to(87, duration=900, center_text="87 %", sub_text="Sauvegarde en cours")
    reveal_card(status_card, delay=0)

    # --- Carte badges + indicateurs de statut ---
    badges_card = Card(row1, elevation=1, padding=Space.XL)
    badges_card.pack(side=tk.LEFT, padx=(0, Space.LG), fill=tk.Y)
    ttkb.Label(badges_card.body, text="États", font=Font.H2, bootstyle=f"inverse-{Color.BG_CARD}").pack(anchor="w", pady=(0, Space.SM))
    for state, label in [("success", "Terminée"), ("running", "En cours"), ("warning", "3 jours"), ("danger", "Échec"), ("idle", "Prête")]:
        line = ttkb.Frame(badges_card.body)
        line.pack(anchor="w", pady=3, fill=tk.X)
        StatusIndicator(line, state=state, page_bg=Color.BG_CARD).pack(side=tk.LEFT)
        ttkb.Label(line, text=label, font=Font.BODY, bootstyle=f"inverse-{Color.BG_CARD}").pack(side=tk.LEFT, padx=(Space.SM, 0))
    badge_row = ttkb.Frame(badges_card.body)
    badge_row.pack(anchor="w", pady=(Space.SM, 0))
    for text, variant in [("SUCCÈS", "success"), ("ATTENTION", "warning"), ("ERREUR", "danger"), ("ACTIF", "accent")]:
        Badge(badge_row, text, variant=variant, page_bg=Color.BG_CARD).pack(side=tk.LEFT, padx=(0, 6))
    reveal_card(badges_card, delay=60)

    # --- Carte formulaire (champ + bascule + boutons) ---
    form_card = Card(row1, elevation=1, padding=Space.XL)
    form_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    ttkb.Label(form_card.body, text="Composants", font=Font.H2, bootstyle=f"inverse-{Color.BG_CARD}").pack(anchor="w", pady=(0, Space.SM))
    Field(form_card.body, page_bg=Color.BG_CARD, width=26).pack(anchor="w", pady=4)
    toggle_switch(form_card.body, tk.BooleanVar(value=True), text="Sauvegarde automatique").pack(anchor="w", pady=(Space.SM, Space.SM))
    btn_row = ttkb.Frame(form_card.body)
    btn_row.pack(anchor="w", pady=(Space.SM, 0))
    ttkb.Button(btn_row, text="Lancer une sauvegarde", bootstyle="primary").pack(side=tk.LEFT)
    ttkb.Button(btn_row, text="Restaurer", bootstyle="secondary-outline").pack(side=tk.LEFT, padx=(Space.SM, 0))
    reveal_card(form_card, delay=120)

    # --- Barre de progression déterminée (démo animée) ---
    row2 = ttkb.Frame(root)
    row2.pack(fill=tk.X, pady=(Space.LG, 0))
    progress_card = Card(row2, elevation=1, padding=Space.XL)
    progress_card.pack(fill=tk.X)
    ttkb.Label(progress_card.body, text="Création de la sauvegarde...", font=Font.BODY_LG, bootstyle=f"inverse-{Color.BG_CARD}").pack(anchor="w")
    bar = progress_bar(progress_card.body, determinate=True, length=400)
    bar.pack(fill=tk.X, pady=(Space.SM, 4))
    ttkb.Label(progress_card.body, text="18,4 Go / 22,4 Go · 2 483 fichiers · Temps restant estimé : 3 min",
               font=Font.BODY_SM, bootstyle="secondary").pack(anchor="w")
    animate_progressbar(bar, 82, duration=1400)
    reveal_card(progress_card, delay=180)

    # --- Notifications & dialogues de démo ---
    demo_row = ttkb.Frame(root)
    demo_row.pack(anchor="w", pady=(Space.LG, 0))
    ttkb.Button(
        demo_row, text="Afficher une notification", bootstyle="info-outline",
        command=lambda: Toast(app, "Sauvegarde terminée avec succès.", variant="success"),
    ).pack(side=tk.LEFT)
    ttkb.Button(
        demo_row, text="Afficher un dialogue", bootstyle="secondary-outline",
        command=lambda: ConfirmDialog.ask(
            app, "Confirmer la restauration",
            "Cette action va écraser le profil actuel. Continuer ?", variant="warning",
        ),
    ).pack(side=tk.LEFT, padx=(Space.SM, 0))

    app.mainloop()
