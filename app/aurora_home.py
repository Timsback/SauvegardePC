"""Liaison entre le tableau de bord Aurora et les données de l'application."""

import design_system as ds
from aurora_canvas import AuroraDashboard, destination_details


def build(app, page):
    app.home_dashboard = AuroraDashboard(page, app)
    app.home_dashboard.pack(fill="both", expand=True)
    app.home_category_vars = app.home_dashboard.category_vars
    app.run_all_button = app.home_dashboard.button


def update_destination(app):
    if hasattr(app, "home_dashboard"):
        app.home_dashboard.set_destination(*destination_details(app.dest_var.get().strip()))


def update_status(app):
    if not hasattr(app, "home_dashboard"):
        return
    if app.active_tasks:
        status = ("Sauvegarde en cours", "Suivez la progression dans le journal.", "↻", ds.Color.CYAN)
    elif app.dest_var.get().strip():
        status = ("Prêt à sauvegarder", "Votre destination est sélectionnée.", "✓", ds.Color.SUCCESS)
    else:
        status = ("Choisissez une destination", "Sélectionnez un dossier avant de commencer.", "!", ds.Color.WARNING)
    app.home_dashboard.set_status(*status)
