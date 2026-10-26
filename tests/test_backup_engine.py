import configparser
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import backup_engine as engine
import backup_tasks as bt


class EngineTest(unittest.TestCase):
    """Fake user profile in a temporary folder; destination on the same NTFS volume."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'Utilisateur'
        self.known = {name: self.home / name for name in bt.STANDARD_FOLDERS}
        for path in self.known.values():
            path.mkdir(parents=True)
        for name, value in (('USERPROFILE', self.home), ('APPDATA', self.home / 'AppData' / 'Roaming'),
                            ('LOCALAPPDATA', self.home / 'AppData' / 'Local')):
            patcher = patch.object(bt, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in ((bt, 'get_known_folder'), (bt, 'running_processes'), (bt, 'outlook_data_files')):
            replacement = {'get_known_folder': lambda name: self.known[name],
                           'running_processes': lambda names: [],
                           'outlook_data_files': lambda: []}[value]
            patcher = patch.object(target, value, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.documents = self.known['Documents']
        (self.documents / 'lettre.txt').write_text('bonjour', encoding='utf-8')
        (self.documents / 'Projets').mkdir()
        (self.documents / 'Projets' / 'plan.txt').write_text('plan', encoding='utf-8')
        self.dest = self.root / 'Disque externe'
        self.dest.mkdir()
        self.config = json.loads(json.dumps(bt.DEFAULT_CONFIG))
        self.config.update(selected_folders=['Documents'], exclusions=[])
        self.logs = []
        bt.CANCEL.clear()
        self.addCleanup(bt.CANCEL.clear)

    def job(self):
        return engine.Job(self.logs.append)

    def backup(self, categories=('files',), dest=None):
        return engine.run_backup(dest or self.dest, self.config, list(categories), self.job(), 'test')

    def version(self, result):
        folder = Path(result['folder'])
        return folder, json.loads((folder / engine.SUMMARY).read_text(encoding='utf-8')), \
            json.loads((folder / engine.INDEX).read_text(encoding='utf-8'))


class BackupTests(EngineTest):
    def test_first_backup_creates_dated_version_with_index(self):
        result = self.backup()
        folder, summary, index = self.version(result)
        self.assertTrue(folder.name.startswith('SauvegardePC_'))
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual((folder / 'Fichiers personnels/Documents/Projets/plan.txt').read_text(encoding='utf-8'), 'plan')
        self.assertEqual(set(index), {'Fichiers personnels/Documents/lettre.txt',
                                      'Fichiers personnels/Documents/Projets/plan.txt'})
        self.assertEqual(index['Fichiers personnels/Documents/lettre.txt'][2],
                         engine.file_digest(self.documents / 'lettre.txt'))
        self.assertTrue((folder / engine.REPORT).exists())
        self.assertTrue(any('Vérification : 2/2' in line for line in self.logs))

    def test_second_backup_links_unchanged_files_and_copies_changes(self):
        first, _, _ = self.version(self.backup())
        time.sleep(1.1)  # version folders are named to the second
        (self.documents / 'lettre.txt').write_text('bonjour, version 2', encoding='utf-8')
        second, summary, _ = self.version(self.backup())
        self.assertEqual(summary['stats']['linked_files'], 1)
        self.assertEqual(summary['stats']['copied_files'], 1)
        unchanged = 'Fichiers personnels/Documents/Projets/plan.txt'
        self.assertTrue(os.path.samefile(first / unchanged, second / unchanged))
        self.assertEqual((first / 'Fichiers personnels/Documents/lettre.txt').read_text(encoding='utf-8'), 'bonjour')
        self.assertEqual((second / 'Fichiers personnels/Documents/lettre.txt').read_text(encoding='utf-8'),
                         'bonjour, version 2')

    def test_destination_inside_documents_is_not_copied_again(self):
        inside = self.documents / 'Sauvegardes'
        inside.mkdir()
        self.backup(dest=inside)
        time.sleep(1.1)
        folder, summary, index = self.version(self.backup(dest=inside))
        self.assertEqual(summary['stats']['files'], 2)
        self.assertFalse(any('Sauvegardes' in rel for rel in index))

    def test_custom_folder_on_destination_drive_root_is_still_backed_up(self):
        photos = self.dest / 'Photos'
        photos.mkdir()
        (photos / 'vacances.jpg').write_bytes(b'jpg')
        self.config['custom_folders'] = [str(photos)]
        _, _, index = self.version(self.backup())
        self.assertIn('Divers (dossiers personnalisés)/Photos/vacances.jpg', index)

    def test_exclusions_and_maximum_size(self):
        (self.documents / 'node_modules').mkdir()
        (self.documents / 'node_modules' / 'lib.js').write_text('x')
        (self.documents / 'disque.ISO').write_bytes(b'0')
        (self.documents / 'gros.bin').write_bytes(b'0' * (2 * 1024 * 1024))
        self.config.update(exclusions=['node_modules', '*.iso'], max_file_size_mb=1)
        _, summary, index = self.version(self.backup())
        self.assertEqual(sorted(Path(rel).name for rel in index), ['lettre.txt', 'plan.txt'])
        self.assertEqual(summary['skipped']['excluded'], 2)
        self.assertEqual(summary['skipped']['large'], 1)

    def test_onedrive_online_only_files_are_skipped_by_default(self):
        online = self.documents / 'en-ligne.docx'
        online.write_text('cloud')
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(online), 0x1000))  # OFFLINE
        _, summary, index = self.version(self.backup())
        self.assertNotIn('Fichiers personnels/Documents/en-ligne.docx', index)
        self.assertEqual(summary['skipped']['cloud'], 1)
        time.sleep(1.1)
        self.config['onedrive_cloud'] = 'download'
        _, _, index = self.version(self.backup())
        self.assertIn('Fichiers personnels/Documents/en-ligne.docx', index)

    def test_junctions_are_not_followed(self):
        target = self.root / 'Ailleurs'
        target.mkdir()
        (target / 'secret.txt').write_text('x')
        os.system(f'mklink /J "{self.documents / "Lien"}" "{target}" >NUL')
        self.assertTrue((self.documents / 'Lien').exists())
        _, summary, index = self.version(self.backup())
        self.assertFalse(any('secret' in rel for rel in index))
        self.assertEqual(summary['skipped']['links'], 1)

    def test_retention_keeps_the_most_recent_versions(self):
        self.config['keep_versions'] = 2
        folders = []
        for n in range(3):
            (self.documents / 'lettre.txt').write_text(f'v{n}', encoding='utf-8')
            folders.append(Path(self.backup()['folder']))
            time.sleep(1.1)
        self.assertFalse(folders[0].exists())
        self.assertTrue(folders[1].exists() and folders[2].exists())
        self.assertEqual((folders[2] / 'Fichiers personnels/Documents/lettre.txt').read_text(encoding='utf-8'), 'v2')

    def test_cancelled_backup_is_marked_and_ignored_as_reference(self):
        class CancellingJob(engine.Job):
            def advance(self, files=1, nbytes=0, detail=None):
                super().advance(files, nbytes, detail)
                if self.phase == 'Copie' and files:
                    bt.CANCEL.set()
        with self.assertRaises(bt.Cancelled):
            engine.run_backup(self.dest, self.config, ['files'], CancellingJob(self.logs.append), 'test')
        versions = engine.list_versions(self.dest)
        self.assertEqual(versions[0]['status'], 'cancelled')
        self.assertEqual(engine.previous_version(self.dest, engine.computer_name()), (None, {}))

    def test_insufficient_space_is_detected_before_copying(self):
        usage = type('Usage', (), {'free': 10, 'total': 100, 'used': 90})()
        with patch.object(engine.shutil, 'disk_usage', return_value=usage):
            with self.assertRaisesRegex(OSError, 'Espace insuffisant'):
                self.backup()
        folder = Path(engine.list_versions(self.dest)[0]['folder'])
        self.assertFalse((folder / 'Fichiers personnels').exists())

    def test_estimate_counts_only_changed_bytes(self):
        before = engine.estimate(self.dest, self.config, ['files'])
        self.assertEqual(before['files'], 2)
        self.assertEqual(before['to_copy_bytes'], before['total_bytes'])
        self.backup()
        after = engine.estimate(self.dest, self.config, ['files'])
        self.assertEqual(after['to_copy_bytes'], 0)
        self.assertTrue(after['enough_space'])

    def test_verify_version_detects_altered_and_missing_files(self):
        folder, _, _ = self.version(self.backup())
        self.assertFalse(engine.verify_version(folder, self.job())['partial'])
        (folder / 'Fichiers personnels/Documents/lettre.txt').write_text('abîmé', encoding='utf-8')
        (folder / 'Fichiers personnels/Documents/Projets/plan.txt').unlink()
        result = engine.verify_version(folder, self.job())
        self.assertTrue(result['partial'])
        report = Path(result['report']).read_text(encoding='utf-8')
        self.assertIn('Contenu altéré', report)
        self.assertIn('Fichier manquant', report)


class RestoreTests(EngineTest):
    def test_restore_personal_files_keeps_different_existing_files(self):
        folder, _, _ = self.version(self.backup())
        (self.documents / 'Projets' / 'plan.txt').unlink()
        (self.documents / 'lettre.txt').write_text('modifiée depuis', encoding='utf-8')
        result = engine.restore(folder, ['files'], self.job())
        self.assertFalse(result['partial'])
        self.assertEqual((self.documents / 'Projets' / 'plan.txt').read_text(encoding='utf-8'), 'plan')
        self.assertEqual((self.documents / 'lettre.txt').read_text(encoding='utf-8'), 'bonjour')
        kept = list(self.documents.glob('lettre (avant restauration *).txt'))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding='utf-8'), 'modifiée depuis')

    def test_identical_files_are_skipped(self):
        folder, _, _ = self.version(self.backup())
        engine.restore(folder, ['files'], self.job())
        self.assertTrue(any('2 déjà identique(s)' in line for line in self.logs))
        self.assertEqual(list(self.documents.glob('*avant restauration*')), [])

    def test_firefox_profile_is_restored_and_declared_as_default(self):
        firefox = bt.firefox_dir()
        profile = firefox / 'Profiles' / 'ab12.default-release'
        profile.mkdir(parents=True)
        (profile / 'places.sqlite').write_bytes(b'favoris')
        (profile / 'cache2').mkdir()
        (profile / 'cache2' / 'entry').write_bytes(b'cache')
        (firefox / 'profiles.ini').write_text(
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/ab12.default-release\nDefault=1\n',
            encoding='utf-8')
        folder, _, index = self.version(self.backup(['browsers']))
        self.assertIn('Navigateurs/Mozilla Firefox/ab12.default-release/places.sqlite', index)
        self.assertFalse(any('cache2' in rel for rel in index))
        engine.remove_tree(firefox)
        # Fresh Firefox install that already created its own empty profile.
        (firefox / 'Profiles' / 'zz99.default-release').mkdir(parents=True)
        (firefox / 'profiles.ini').write_text(
            '[Install4F96D1932A9F858E]\nDefault=Profiles/zz99.default-release\nLocked=1\n\n'
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/zz99.default-release\nDefault=1\n',
            encoding='utf-8')
        self.assertEqual(engine.browsers_in(folder), ['Mozilla Firefox'])
        engine.restore(folder, ['browsers'], self.job(), browser='Mozilla Firefox')
        self.assertEqual((firefox / 'Profiles' / 'ab12.default-release' / 'places.sqlite').read_bytes(), b'favoris')
        parser = configparser.RawConfigParser()
        parser.read(firefox / 'profiles.ini', encoding='utf-8')
        self.assertEqual(parser.get('Install4F96D1932A9F858E', 'Default'), 'Profiles/ab12.default-release')

    def test_chromium_profile_in_use_is_kept_aside_and_running_browser_blocks(self):
        chrome = bt.chromium_browsers()['Google Chrome'][0]
        (chrome / 'Default').mkdir(parents=True)
        (chrome / 'Default' / 'Bookmarks').write_text('anciens favoris')
        folder, _, _ = self.version(self.backup(['browsers']))
        (chrome / 'Default' / 'Bookmarks').write_text('profil neuf')
        with patch.object(bt, 'running_processes', return_value=['chrome.exe']):
            result = engine.restore(folder, ['browsers'], self.job())
        self.assertTrue(result['partial'])
        self.assertEqual((chrome / 'Default' / 'Bookmarks').read_text(), 'profil neuf')
        engine.restore(folder, ['browsers'], self.job())
        self.assertEqual((chrome / 'Default' / 'Bookmarks').read_text(), 'anciens favoris')
        kept = list(chrome.parent.glob('User Data (avant restauration *)'))
        self.assertEqual((kept[0] / 'Default' / 'Bookmarks').read_text(), 'profil neuf')

    def test_thunderbird_backup_and_restore(self):
        thunderbird = bt.thunderbird_dir()
        profile = thunderbird / 'Profiles' / 'q1w2.default-release'
        (profile / 'Mail' / 'Local Folders').mkdir(parents=True)
        (profile / 'Mail' / 'Local Folders' / 'Inbox').write_text('message')
        (thunderbird / 'profiles.ini').write_text(
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/q1w2.default-release\nDefault=1\n',
            encoding='utf-8')
        folder, _, index = self.version(self.backup(['email']))
        self.assertIn('Email/Thunderbird/q1w2.default-release/Mail/Local Folders/Inbox', index)
        engine.remove_tree(thunderbird)
        engine.restore(folder, ['email'], self.job())
        self.assertEqual((profile / 'Mail' / 'Local Folders' / 'Inbox').read_text(), 'message')
        self.assertIn('Path=Profiles/q1w2.default-release', (thunderbird / 'profiles.ini').read_text(encoding='utf-8'))

    def test_legacy_backup_is_listed_and_restored(self):
        legacy = self.root / 'Ancienne'
        (legacy / 'Fichiers personnels' / 'Documents').mkdir(parents=True)
        (legacy / 'Fichiers personnels' / 'Documents' / 'ancien.txt').write_text('ancien')
        (legacy / 'Divers (dossiers personnalisés)' / 'Jeux').mkdir(parents=True)
        (legacy / 'Divers (dossiers personnalisés)' / 'Jeux' / 'save.dat').write_text('save')
        versions = engine.resolve_restore_source(legacy)
        self.assertEqual(len(versions), 1)
        self.assertTrue(versions[0]['legacy'])
        self.assertEqual(engine.detect_categories(legacy), ['files'])
        engine.restore(legacy, ['files'], self.job())
        self.assertEqual((self.documents / 'ancien.txt').read_text(), 'ancien')
        self.assertEqual((self.home / 'Dossiers restaurés' / 'Jeux' / 'save.dat').read_text(), 'save')

    def test_restore_source_can_be_destination_or_version(self):
        folder, _, _ = self.version(self.backup())
        self.assertEqual([v['folder'] for v in engine.resolve_restore_source(self.dest)], [str(folder)])
        self.assertEqual([v['folder'] for v in engine.resolve_restore_source(folder)], [str(folder)])
        self.assertEqual(engine.detect_categories(folder), ['files'])

    def test_restore_commands_use_backup_exports(self):
        folder, _, _ = self.version(self.backup())
        (folder / 'WiFi').mkdir()
        (folder / 'WiFi' / 'Wi-Fi-Maison.xml').write_text('<WLANProfile/>')
        calls = []
        with patch.object(bt, 'run_command', side_effect=lambda args, *a, **k: calls.append(args) or (True, '')):
            engine.restore(folder, ['wifi'], self.job())
        self.assertEqual(calls[0][:3], ['netsh', 'wlan', 'add'])
        self.assertIn(f'filename={folder / "WiFi" / "Wi-Fi-Maison.xml"}', calls[0])


if __name__ == '__main__':
    unittest.main()
