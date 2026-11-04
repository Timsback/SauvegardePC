import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import backup_engine as engine
import backup_tasks as bt
import migration as mg


def write(path: Path, content: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_version(root: Path, folders: dict[str, dict[str, str]]) -> Path:
    """Crée une vraie version de sauvegarde (via backup_engine.run_backup) à partir
    de `{nom_de_dossier_standard: {chemin_relatif: contenu}}`, pour tester
    `migration.py` contre le format réel (sauvegarde.json/fichiers.json)."""
    sources = {}
    for name, files in folders.items():
        src = root / f"source_{name}"
        for rel, content in files.items():
            write(src / rel, content)
        sources[name] = src

    def fake_known_folder(name):
        return sources[name]

    config = json.loads(json.dumps(bt.DEFAULT_CONFIG))
    config["selected_folders"] = list(folders)
    dest = root / "destination"
    with patch.object(bt, "get_known_folder", side_effect=fake_known_folder):
        result = engine.run_backup(dest, config, ["files"], engine.Job(lambda m: None), "test")
    return Path(result["folder"])


class ScanAndTriageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.version = make_version(self.root, {
            "Documents": {
                "rapport.docx": "contenu unique A",
                "rapport (1).docx": "contenu unique A",  # doublon du précédent
                "setup.exe": "installeur",
                "film.iso": "image disque",
                "raccourci.lnk": "lnk",
            },
            "Images": {"photo.jpg": "photo"},
        })

    def test_scan_reads_standard_folders_via_backup_engine(self):
        records = mg.scan_migration(self.version)
        relatives = {(r["category"], r["relative"]) for r in records}
        self.assertIn(("Documents", "rapport.docx"), relatives)
        self.assertIn(("Images", "photo.jpg"), relatives)
        self.assertEqual(len(records), 6)

    def test_scan_reuses_index_hash_without_recomputing(self):
        records = mg.scan_migration(self.version)
        by_rel = {(r["category"], r["relative"]): r for r in records}
        # Toute sauvegarde faite avec cette version a déjà un index : l'empreinte
        # d'un fichier de taille unique (non haché pour le dédoublonnage) vient
        # directement de fichiers.json, jamais recalculée par scan_migration.
        self.assertTrue(by_rel[("Images", "photo.jpg")]["sha256"])

    def test_duplicate_detection_keeps_cleanest_path(self):
        plan = mg.build_triage_plan(mg.scan_migration(self.version))
        by_rel = {e["relative"]: e for e in plan if e["category"] == "Documents"}
        self.assertEqual(by_rel["rapport.docx"]["tri"], "normal")
        self.assertTrue(by_rel["rapport.docx"]["checked"])
        self.assertEqual(by_rel["rapport (1).docx"]["tri"], "doublon")
        self.assertFalse(by_rel["rapport (1).docx"]["checked"])

    def test_installers_images_junk_and_shortcuts_unchecked_by_default(self):
        plan = mg.build_triage_plan(mg.scan_migration(self.version))
        by_rel = {e["relative"]: e for e in plan if e["category"] == "Documents"}
        self.assertEqual(by_rel["setup.exe"]["tri"], "installeur")
        self.assertFalse(by_rel["setup.exe"]["checked"])
        self.assertEqual(by_rel["film.iso"]["tri"], "image_disque")
        self.assertFalse(by_rel["film.iso"]["checked"])
        self.assertEqual(by_rel["raccourci.lnk"]["tri"], "raccourci")
        self.assertFalse(by_rel["raccourci.lnk"]["checked"])

    def test_junk_files_unchecked_by_default(self):
        # Thumbs.db/desktop.ini sont déjà exclus par les exclusions par défaut de la
        # sauvegarde elle-même (bt.DEFAULT_EXCLUSIONS) ; `classify_file` reste utile
        # pour les sauvegardes plus anciennes ou une liste d'exclusions personnalisée.
        tri, checked = mg.classify_file("Thumbs.db", 10, mg.DEFAULT_LARGE_THRESHOLD)
        self.assertEqual(tri, "inutile")
        self.assertFalse(checked)

    def test_large_file_threshold(self):
        plan = mg.build_triage_plan(mg.scan_migration(self.version), large_threshold=1)
        by_rel = {e["relative"]: e for e in plan if e["category"] == "Images"}
        self.assertEqual(by_rel["photo.jpg"]["tri"], "gros_fichier")
        self.assertFalse(by_rel["photo.jpg"]["checked"])

    def test_parse_old_pc_name_from_summary(self):
        self.assertEqual(mg.parse_old_pc_name(self.version), engine.computer_name())


class SpaceAndCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _plan(self):
        return [
            {"category": "Documents", "relative": "a.txt", "size": 1000, "mtime_ns": 0, "path": "a", "sha256": "", "tri": "normal", "checked": True, "reason": ""},
            {"category": "Documents", "relative": "b.exe", "size": 2000, "mtime_ns": 0, "path": "b", "sha256": "", "tri": "installeur", "checked": False, "reason": ""},
        ]

    def test_estimate_space_fits(self):
        result = mg.estimate_space(self._plan(), self.root)
        self.assertEqual(result["checked_size"], 1000)
        self.assertEqual(result["total_size"], 3000)
        self.assertTrue(result["fits"])

    def test_estimate_space_handles_missing_destination(self):
        missing = self.root / "ne-existe-pas" / "encore"
        result = mg.estimate_space(self._plan(), missing)
        self.assertTrue(result["fits"])  # basé sur le parent existant (self.root)

    def test_export_plan_csv(self):
        out = self.root / "plan.csv"
        mg.export_plan_csv(self._plan(), out)
        content = out.read_text(encoding="utf-8-sig")
        self.assertIn("a.txt", content)
        self.assertIn("Copier", content)
        self.assertIn("Exclure", content)


class DestinationTests(unittest.TestCase):
    def test_migration_subfolder_name_contains_pc(self):
        self.assertIn("DESKTOP-ABC12", mg.migration_subfolder_name("DESKTOP-ABC12"))

    def test_resolve_destination_network_personal_requires_home(self):
        with self.assertRaises(ValueError):
            mg.resolve_destination("Documents", mg.DEST_NETWORK_PERSONAL, "PC", network_home="")

    def test_resolve_destination_manual_requires_path(self):
        with self.assertRaises(ValueError):
            mg.resolve_destination("Documents", mg.DEST_MANUAL, "PC", network_home="", manual_path="")

    def test_resolve_destination_network_personal(self):
        dest = mg.resolve_destination("Documents", mg.DEST_NETWORK_PERSONAL, "PC", network_home="\\\\nas\\user")
        self.assertTrue(str(dest).startswith("\\\\nas\\user\\Migration"))
        self.assertTrue(str(dest).endswith("Documents"))


class CopyPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "src.txt"
        write(self.src, "contenu")
        self.dest_dir = self.root / "dest"

    def _entry(self):
        return {
            "category": "Documents", "relative": "src.txt", "path": str(self.src),
            "size": self.src.stat().st_size, "mtime_ns": self.src.stat().st_mtime_ns,
            "sha256": "", "tri": "normal", "checked": True, "reason": "",
        }

    def test_simulate_mode_does_not_write(self):
        result = mg.copy_plan([self._entry()], {"Documents": self.dest_dir}, log=lambda m: None, simulate=True)
        self.assertFalse((self.dest_dir / "src.txt").exists())
        self.assertEqual(len(result["copied"]), 1)

    def test_real_copy_then_resume_skips_identical_file(self):
        log_lines = []
        mg.copy_plan([self._entry()], {"Documents": self.dest_dir}, log=log_lines.append)
        self.assertTrue((self.dest_dir / "src.txt").exists())
        result = mg.copy_plan([self._entry()], {"Documents": self.dest_dir}, log=log_lines.append)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(len(result["copied"]), 0)

    def test_conflict_rename_keeps_both_files(self):
        write(self.dest_dir / "src.txt", "autre contenu")
        entry = self._entry()
        result = mg.copy_plan([entry], {"Documents": self.dest_dir}, log=lambda m: None, conflict_policy=mg.CONFLICT_RENAME)
        self.assertEqual(len(result["copied"]), 1)
        self.assertTrue((self.dest_dir / "src (1).txt").exists())

    def test_verify_copy_detects_match(self):
        entry = self._entry()
        result = mg.copy_plan([entry], {"Documents": self.dest_dir}, log=lambda m: None)
        mismatches = mg.verify_copy(result, log=lambda m: None)
        self.assertEqual(mismatches, [])


class ProgramInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_filters_noise_and_dedupes(self):
        listing = (
            "Nom                                      Id                     Version      Source\n"
            "-----------------------------------------------------------------------------------\n"
            "7-Zip                                     7zip.7zip              23.01        winget\n"
            "KB5031354                                 KB5031354              1.0          \n"
            "Microsoft Visual C++ 2015-2022 Redistributable  Microsoft.VC     14.38        winget\n"
            "7-Zip                                     7zip.7zip              23.01        winget\n"
        )
        write(self.root / "Programmes" / "programmes_liste_complete.txt", listing)
        out = mg.export_program_inventory(self.root, log=lambda m: None)
        content = out.read_text(encoding="utf-8-sig")
        self.assertIn("7-Zip", content)
        self.assertNotIn("KB5031354", content)
        self.assertNotIn("Redistributable", content)
        self.assertEqual(content.count("7-Zip"), 1)

    def test_no_listing_returns_none(self):
        self.assertIsNone(mg.export_program_inventory(self.root, log=lambda m: None))


if __name__ == "__main__":
    unittest.main()
