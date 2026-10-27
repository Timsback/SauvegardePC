import configparser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import backup_tasks as bt


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = []
        bt.CANCEL.clear()
        self.addCleanup(bt.CANCEL.clear)

    def log(self, message):
        self.logs.append(str(message))


class MozillaProfileTests(TempDirTest):
    def setUp(self):
        super().setUp()
        patcher = patch.object(bt, 'APPDATA', self.root / 'Roaming')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = self.root / 'Roaming' / 'Thunderbird'

    def test_profiles_ini_relative_and_absolute_paths(self):
        relative = self.base / 'Profiles' / 'abcd.default-release'
        absolute = self.root / 'Ailleurs' / 'pro.profil'
        for path in (relative, absolute):
            path.mkdir(parents=True)
        (self.base / 'profiles.ini').write_text(
            '[Install1234]\nDefault=Profiles/abcd.default-release\n\n'
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abcd.default-release\n\n'
            f'[Profile1]\nName=pro\nIsRelative=0\nPath={absolute}\n\n'
            '[Profile2]\nName=supprime\nIsRelative=1\nPath=Profiles/absent.default\n', encoding='utf-8')
        self.assertEqual(bt.thunderbird_profiles(), [relative, absolute])

    def test_fallback_on_profiles_folder_without_ini(self):
        profile = self.base / 'Profiles' / 'x1y2.default-release'
        profile.mkdir(parents=True)
        self.assertEqual(bt.thunderbird_profiles(), [profile])

    def test_default_profile_from_saved_ini(self):
        text = ('[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a.old\nDefault=1\n\n'
                '[Install99]\nDefault=Profiles/b.default-release\nLocked=1\n')
        self.assertEqual(bt.mozilla_default_profile(text), 'b.default-release')
        self.assertEqual(bt.mozilla_default_profile('[Profile0]\nPath=Profiles/a.old\nDefault=1\n'), 'a.old')

    def test_register_restored_profile_as_default(self):
        app = self.root / 'Firefox'
        (app / 'Profiles').mkdir(parents=True)
        (app / 'profiles.ini').write_text(
            '[Install308046B0AF4A39CB]\nDefault=Profiles/new.default-release\nLocked=1\n\n'
            '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/new.default-release\nDefault=1\n\n'
            '[General]\nStartWithLastProfile=1\nVersion=2\n', encoding='utf-8')
        (app / 'installs.ini').write_text('[308046B0AF4A39CB]\nDefault=Profiles/new.default-release\nLocked=1\n',
                                          encoding='utf-8')
        bt.register_mozilla_profiles(app, ['old.default-release'], 'old.default-release')
        parser = configparser.RawConfigParser()
        parser.read(app / 'profiles.ini', encoding='utf-8')
        paths = {parser.get(s, 'Path'): parser.get(s, 'Default', fallback='0')
                 for s in parser.sections() if s.startswith('Profile')}
        self.assertEqual(paths, {'Profiles/new.default-release': '0', 'Profiles/old.default-release': '1'})
        self.assertEqual(parser.get('Install308046B0AF4A39CB', 'Default'), 'Profiles/old.default-release')
        self.assertIn('Default=Profiles/old.default-release', (app / 'installs.ini').read_text(encoding='utf-8'))


class ConfigTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.file = self.root / 'SauvegardePC' / 'config.json'
        for name, value in (('CONFIG_DIR', self.file.parent), ('CONFIG_FILE', self.file)):
            patcher = patch.object(bt, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_save_then_load_without_temporary_file(self):
        bt.save_config(dict(bt.DEFAULT_CONFIG, custom_folders=['C:/Données']))
        self.assertEqual(bt.load_config()['custom_folders'], ['C:/Données'])
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ['config.json'])

    def test_failed_write_keeps_previous_configuration(self):
        bt.save_config(dict(bt.DEFAULT_CONFIG, custom_folders=['A']))
        with patch.object(bt.os, 'replace', side_effect=OSError('disque plein')):
            with self.assertRaises(OSError):
                bt.save_config(dict(bt.DEFAULT_CONFIG, custom_folders=['B']))
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['custom_folders'], ['A'])

    def test_corrupted_file_is_kept_aside(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"custom_folders": ["A"', encoding='utf-8')
        self.assertEqual(bt.load_config(), bt.DEFAULT_CONFIG)
        self.assertTrue((self.file.parent / 'config.corrompu.json').exists())

    def test_defaults_are_not_shared_between_loads(self):
        config = bt.load_config()
        config['exclusions'].append('*.iso')
        self.assertNotIn('*.iso', bt.load_config()['exclusions'])


class CommandTests(TempDirTest):
    def test_windows_key_is_masked_in_log(self):
        key = 'ABCDE-FGHIJ-KLMNO-PQRST-UVWXY'
        with patch.object(bt, 'run_command', return_value=(True, key + '\n')) as run:
            self.assertEqual(bt.get_windows_key(self.log), key)
        self.assertFalse(run.call_args.kwargs['log_output'])
        self.assertFalse(any('ABCDE' in line or 'PQRST' in line for line in self.logs))
        self.assertTrue(any('•••••-•••••-•••••-•••••-UVWXY' in line for line in self.logs))

    def test_sensitive_output_is_not_logged(self):
        ok, output = bt.run_command([sys.executable, '-c', 'print("SECRET-KEY")'], self.log, log_output=False)
        self.assertTrue(ok)
        self.assertIn('SECRET-KEY', output)
        self.assertFalse(any('SECRET-KEY' in line for line in self.logs))

    def test_accents_kept_whatever_the_tool_encoding(self):
        # winget/netsh: UTF-8 ; dism: OEM (cp850) ; pnputil: ANSI (cp1252).
        text = 'Le package du pilote a été correctement exporté. L’opération a réussi.'.replace('’', "'")
        for encoding in ('utf-8', 'cp850', 'cp1252'):
            with self.subTest(encoding=encoding):
                self.assertEqual(bt.decode_output(text.encode(encoding)), text)
        script = 'import sys; sys.stdout.buffer.write("Opération réussie".encode("cp850"))'
        ok, output = bt.run_command([sys.executable, '-c', script], self.log)
        self.assertEqual(output, 'Opération réussie')
        self.assertIn('  │ Opération réussie', self.logs)

    def test_command_timeout_does_not_block(self):
        started = time.monotonic()
        ok, _ = bt.run_command([sys.executable, '-c', 'import time; time.sleep(30)'], self.log, timeout=1)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(any('interrompue' in line for line in self.logs))

    def test_cancel_stops_running_command(self):
        bt.CANCEL.set()
        started = time.monotonic()
        with self.assertRaises(bt.Cancelled):
            bt.run_command([sys.executable, '-c', 'import time; time.sleep(30)'], self.log, timeout=60)
        self.assertLess(time.monotonic() - started, 15)

    def test_command_never_waits_for_input(self):
        ok, output = bt.run_command([sys.executable, '-c', 'input("Y/N ? ")'], self.log, timeout=10)
        self.assertFalse(ok)
        self.assertIn('EOFError', output)

    def test_winget_commands_are_non_interactive(self):
        calls = []
        with patch.object(bt, 'run_command', side_effect=lambda args, *a, **k: calls.append(args) or (True, '')):
            bt.export_installed_programs(self.root, self.log)
            (self.root / 'Programmes' / 'programmes.json').write_text('{}')
            bt.import_installed_programs(self.root, self.log)
        self.assertEqual(len(calls), 3)
        for args in calls:
            self.assertIn('--accept-source-agreements', args)
            self.assertIn('--disable-interactivity', args)
        self.assertIn('--accept-package-agreements', calls[2])


class FilterAndDiskTests(TempDirTest):
    def test_file_filter_patterns_are_case_insensitive(self):
        rules = bt.FileFilter(['node_modules', '*.ISO', '~$*'])
        self.assertTrue(rules.excluded('Node_Modules'))
        self.assertTrue(rules.excluded('ubuntu.iso'))
        self.assertTrue(rules.excluded('~$rapport.docx'))
        self.assertFalse(rules.excluded('rapport.docx'))

    def test_system_drive_is_same_disk(self):
        self.assertTrue(bt.same_disk_as_system('C:\\Sauvegardes'))
        self.assertFalse(bt.same_disk_as_system('\\\\serveur\\partage'))

    def test_filesystem_name_of_temp_folder(self):
        self.assertIn(bt.filesystem_name(self.root), ('NTFS', 'ReFS', 'exFAT', 'FAT32'))

    def test_operation_lock_is_exclusive_across_threads(self):
        import threading
        first = bt.OperationLock()
        self.assertTrue(first.acquire())
        result = []
        thread = threading.Thread(target=lambda: result.append(bt.OperationLock().acquire()))
        thread.start()
        thread.join()
        first.release()
        self.assertEqual(result, [False])

    def test_mask_key(self):
        self.assertEqual(bt.mask_key('AB12'), '••••')
        self.assertEqual(bt.mask_key('ABCDE-12345'), '•••••-12345')


if __name__ == '__main__':
    unittest.main()
