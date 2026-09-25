import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import software_backup as sb


class SoftwareBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bases = {b: self.root/b for b in ('home', 'roaming', 'local', 'programdata')}
        for path in self.bases.values():
            path.mkdir()
        self.patcher = patch.object(sb, 'roots', return_value=self.bases)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.legacy = patch.object(sb, 'analyze', side_effect=sb.analyze_legacy)
        self.legacy.start()
        self.addCleanup(self.legacy.stop)
        self.program = dict(name='Notepad++', version='test', publisher='test', website='')
        self.data = self.bases['roaming'] / 'Notepad++'
        self.data.mkdir()
        (self.data/'config.xml').write_text('original')
        self.destination = self.root/'backup'

    def snapshot(self):
        sb.backup(sb.analyze(self.program), self.destination, lambda _: None)
        return Path(sb.snapshots(self.destination)[0]['id'])

    def test_roundtrip_and_preserve_existing(self):
        folder = self.snapshot()
        (self.data/'config.xml').write_text('current')
        (self.data/'unrelated.txt').write_text('keep')
        sb.restore(folder, lambda _: None)
        self.assertEqual((self.data/'config.xml').read_text(), 'original')
        self.assertEqual((self.data/'unrelated.txt').read_text(), 'keep')
        recovery = next(folder.glob('Avant-restauration-*'))
        self.assertEqual((recovery/'0').read_text(), 'current')

    def test_named_snapshots_stay_distinct_and_discoverable(self):
        first = self.snapshot()
        self.snapshot()
        snapshots = sb.snapshots(self.destination)
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(all(Path(s['id']).name.startswith('Notepad++-') for s in snapshots))
        # Older folders remain discoverable independently of their names.
        first.rename(first.with_name('20260925-145933-81d8a533'))
        self.assertEqual(len(sb.snapshots(self.destination)), 2)

    def test_windows_safe_snapshot_names(self):
        for name in ['ISIS', 'Éditeur de données', 'CON', '../A/B:C*D?E"F<G>H|I\n', '.', '', 'A'*300]:
            with self.subTest(name=name):
                folder = self.destination/sb.snapshot_folder_name(dict(name=name))
                folder.mkdir(parents=True)
                self.assertEqual(folder.parent, self.destination)
                self.assertLessEqual(len(folder.name), 105)

    def test_engine_snapshot_uses_program_name(self):
        plan = dict(engine=2, program=dict(name='ISIS'), files=[], registry=[],
                    installer=dict(method='manual', label='Réinstallation manuelle'))
        result = sb.backup(plan, self.destination, lambda _: None)
        self.assertTrue(Path(result['folder']).name.startswith('ISIS-'))
        self.assertEqual(sb.snapshots(self.destination)[0]['name'], 'ISIS')

    def test_new_windows_user(self):
        folder = self.snapshot()
        new_roaming = self.root/'new-user'/'Roaming'
        new_roaming.mkdir(parents=True)
        self.bases['roaming'] = new_roaming
        sb.restore(folder, lambda _: None)
        self.assertEqual((new_roaming/'Notepad++/config.xml').read_text(), 'original')

    def test_corruption_aborts_before_writing(self):
        folder = self.snapshot()
        (folder/'data/0/config.xml').write_text('corrupted')
        (self.data/'config.xml').write_text('current')
        with self.assertRaises(ValueError):
            sb.restore(folder, lambda _: None)
        self.assertEqual((self.data/'config.xml').read_text(), 'current')

    def test_manifest_cannot_escape_allowed_application(self):
        folder = self.snapshot()
        path = folder/'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['payload'][0]['relative'] = 'Notepad++/../../victim.txt'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            sb.restore(folder, lambda _: None)

    def test_unknown_registry_rejected(self):
        folder = self.snapshot()
        path = folder/'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['registry_values'] = [dict(key='Software\\Microsoft\\Windows\\CurrentVersion\\Run')]
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            sb.restore(folder, lambda _: None)

    def test_nested_destination_rejected(self):
        with self.assertRaises(ValueError):
            sb.backup(sb.analyze(self.program), self.data/'backup', lambda _: None)

    def test_locked_file_produces_partial_manifest(self):
        with patch.object(sb.shutil, 'copy2', side_effect=PermissionError('locked')):
            with self.assertRaises(RuntimeError):
                self.snapshot()
        snapshot = sb.snapshots(self.destination)[0]
        self.assertEqual(snapshot['status'], 'partial')
        self.assertEqual(snapshot['count'], 0)

    def test_unknown_application_uses_exact_name_only(self):
        (self.bases['roaming']/'Vendor').mkdir()
        plan = sb.analyze(dict(name='Unlisted application', publisher='Vendor'))
        self.assertEqual(plan['files'], [])
        self.assertIsNone(plan['recipe'])

    def test_registry_roundtrip_without_touching_real_registry(self):
        program = dict(name='7-Zip', version='test')
        plan = dict(program=program, recipe='7zip', files=[], registry=['Software\\7-Zip'], notes=[])
        values = [dict(key='Software\\7-Zip', name='Setting', value='abcd', type=3)]
        fake = MagicMock()
        fake.REG_BINARY, fake.REG_NONE = 3, 0
        with patch.object(sb, 'winreg', fake), patch.object(sb, 'registry_read', return_value=values), patch.object(sb, 'analyze_legacy', return_value=plan):
            sb.backup(plan, self.destination, lambda _: None)
            folder = Path(sb.snapshots(self.destination)[0]['id'])
            sb.restore(folder, lambda _: None)
        self.assertEqual(fake.SetValueEx.call_args.args[1:], ('Setting', 0, 3, bytes.fromhex('abcd')))


if __name__ == '__main__':
    unittest.main()
