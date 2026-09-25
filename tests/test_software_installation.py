"""Exercise clean-machine installation without executing real installers."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import software_backup as sb
import software_engine as engine


class SoftwareInstallationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.old = self.root/'old-profile'
        self.new = self.root/'new-profile'
        self.source = self.old/'ISIS'
        self.source.mkdir(parents=True)
        (self.source/'settings.ini').write_text('saved settings')
        self.program = dict(name='ISIS x64', version='1.0', publisher='Example', product_key='{TEST}')
        self.info = dict(ProductName='ISIS', ProductVersion='1.0', Manufacturer='Example', ProductCode='{TEST}', cabinets=[])
        self.msi = self.root/'setup.msi'
        self.msi.write_bytes(b'fake installer; never execute')
        self.plan = dict(engine=2, program=self.program, registry=[], services=[], errors=[],
                         files=[dict(base='roaming', relative='ISIS', source=str(self.source), kind='data')],
                         installer=dict(method='manual', label='Missing installer'))
        roots = patch.object(engine, 'all_roots', return_value={'roaming': self.new})
        roots.start()
        self.addCleanup(roots.stop)

    def snapshot(self, method='msi'):
        plan = copy.deepcopy(self.plan)
        if method == 'msi':
            with patch.object(engine, 'msi_info', return_value=self.info):
                plan['installer'] = engine.supplied_installer(self.program, self.msi)
        elif method == 'winget':
            plan['installer'] = dict(method='winget', id='Example.ISIS', version='1.0', label='Winget')
        elif method == 'exe':
            exe = self.root/'setup.exe'
            exe.write_bytes(b'fake exe; never execute')
            plan['installer'] = engine.supplied_installer(self.program, exe)
        return Path(engine.save_snapshot(plan, self.root/'backup', lambda _: None)['folder'])

    def assert_no_settings(self):
        self.assertFalse((self.new/'ISIS/settings.ini').exists())

    def test_clean_machine_installs_before_restoring_settings(self):
        folder = self.snapshot()
        def install(args, timeout):
            self.assert_no_settings()
            self.assertEqual(args[0], 'msiexec.exe')
            self.assertIn('/norestart', args)
            self.assertTrue(Path(args[2]).is_relative_to(folder))
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(engine, 'matching_installed', side_effect=[None, self.program]), \
             patch.object(engine, 'msi_info', return_value=self.info), \
             patch.object(engine, 'run', side_effect=install) as runner:
            engine.restore_snapshot(folder, lambda _: None)
        runner.assert_called_once()
        self.assertEqual((self.new/'ISIS/settings.ini').read_text(), 'saved settings')

    def test_installed_software_does_not_run_installer(self):
        folder = self.snapshot('manual')
        with patch.object(engine, 'matching_installed', return_value=self.program), patch.object(engine, 'run') as runner:
            engine.restore_snapshot(folder, lambda _: None)
        runner.assert_not_called()
        self.assertEqual((self.new/'ISIS/settings.ini').read_text(), 'saved settings')

    def test_missing_installer_stops_before_settings(self):
        folder = self.snapshot('manual')
        with patch.object(engine, 'matching_installed', return_value=None), patch.object(engine, 'run') as runner:
            with self.assertRaisesRegex(RuntimeError, 'ajoutez son installeur'):
                engine.restore_snapshot(folder, lambda _: None)
        runner.assert_not_called()
        self.assert_no_settings()
        report = json.loads(next(folder.glob('Avant-restauration-*/rapport-restauration.json')).read_text())
        self.assertEqual(report['restored_files'], 0)
        self.assertEqual(report['stage'], 'installation_required')

    def test_failure_cancellation_and_restart_stop_settings(self):
        for code in (1603, 1602, 3010, 1641):
            with self.subTest(code=code):
                folder = self.snapshot()
                with patch.object(engine, 'matching_installed', return_value=None), \
                     patch.object(engine, 'msi_info', return_value=self.info), \
                     patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], code, '', 'diagnostic')):
                    with self.assertRaises(RuntimeError):
                        engine.restore_snapshot(folder, lambda _: None)
                self.assert_no_settings()
                self.assertIn('diagnostic', (folder/'installation-sortie.txt').read_text())

    def test_success_exit_without_registered_software_stops_settings(self):
        folder = self.snapshot('exe')
        with patch.object(engine, 'matching_installed', return_value=None), \
             patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'non confirmée'):
                engine.restore_snapshot(folder, lambda _: None)
        self.assert_no_settings()

    def test_winget_installs_exact_version(self):
        folder = self.snapshot('winget')
        with patch.object(engine, 'matching_installed', side_effect=[None, self.program]), \
             patch.object(engine.shutil, 'which', return_value='winget.exe'), \
             patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as runner:
            engine.restore_snapshot(folder, lambda _: None)
        args = runner.call_args.args[0]
        self.assertEqual(args[args.index('--id')+1], 'Example.ISIS')
        self.assertEqual(args[args.index('--version')+1], '1.0')
        self.assertIn('--silent', args)
        self.assertEqual((self.new/'ISIS/settings.ini').read_text(), 'saved settings')

    def test_missing_winget_has_actionable_failure(self):
        folder = self.snapshot('winget')
        with patch.object(engine, 'matching_installed', return_value=None), \
             patch.object(engine.shutil, 'which', return_value=None), patch.object(engine, 'run') as runner:
            with self.assertRaisesRegex(RuntimeError, 'Winget est absent'):
                engine.restore_snapshot(folder, lambda _: None)
        runner.assert_not_called()
        self.assert_no_settings()

    def test_attach_exe_to_existing_snapshot_and_restore(self):
        folder = self.snapshot('manual')
        exe = self.root/'install ISIS.exe'
        exe.write_bytes(b'fake exe')
        engine.attach_installer(folder, exe)
        exe.unlink()  # Restoration must rely entirely on the copied installer.
        self.assertEqual(sb.snapshots(folder)[0]['installer'], 'exe')
        self.assertEqual(len(sb.snapshots(folder.parent)), 1)
        with patch.object(engine, 'matching_installed', side_effect=[None, self.program]), \
             patch.object(engine, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as runner:
            engine.restore_snapshot(folder, lambda _: None)
        self.assertEqual(len(runner.call_args.args[0]), 1)
        self.assertTrue(Path(runner.call_args.args[0][0]).is_relative_to(folder))
        self.assertEqual((self.new/'ISIS/settings.ini').read_text(), 'saved settings')

    def test_altered_installer_never_executes(self):
        folder = self.snapshot()
        next(folder.glob('installer/**/*.msi')).write_bytes(b'changed')
        with patch.object(engine, 'run') as runner:
            with self.assertRaisesRegex(ValueError, 'altéré'):
                engine.restore_snapshot(folder, lambda _: None)
        runner.assert_not_called()
        self.assert_no_settings()

    def test_failed_installer_replacement_preserves_previous_manifest(self):
        folder = self.snapshot()
        original = (folder/'manifest.json').read_bytes()
        with patch.object(engine, 'msi_info', return_value=self.info), \
             patch.object(engine.shutil, 'copy2', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                engine.attach_installer(folder, self.msi)
        self.assertEqual((folder/'manifest.json').read_bytes(), original)
        engine.check_manifest(folder)

    def test_mismatched_msi_rejected(self):
        wrong = dict(self.info, ProductCode='{OTHER}', ProductName='Other product')
        with patch.object(engine, 'msi_info', return_value=wrong):
            with self.assertRaisesRegex(ValueError, 'ne correspond pas'):
                engine.supplied_installer(self.program, self.msi)

    def test_registry_parameters_follow_installation(self):
        folder = self.snapshot()
        data = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        key = dict(hive='HKEY_CURRENT_USER', key='Software\\ISIS', view=64)
        data['registry'] = [key]
        data['registry_values'] = [dict(key, name='Theme', type=1, value='dark')]
        engine.write_json(folder/'manifest.json', data)
        registry = MagicMock()
        registry.OpenKey.side_effect = FileNotFoundError
        order = []
        registry.SetValueEx.side_effect = lambda *args: order.append('settings')
        def install(*args):
            order.append('install')
            return subprocess.CompletedProcess([], 0, '', '')
        with patch.object(sb, 'winreg', registry), \
             patch.object(engine, 'matching_installed', side_effect=[None, self.program]), \
             patch.object(engine, 'msi_info', return_value=self.info), \
             patch.object(engine, 'run', side_effect=install):
            result = engine.restore_snapshot(folder, lambda _: None)
        self.assertEqual(order, ['install', 'settings'])
        self.assertEqual(registry.SetValueEx.call_args.args[1:], ('Theme', 0, 1, 'dark'))
        self.assertFalse(result['partial'])

    def test_msi_external_cabinet_layout_is_preserved(self):
        cab = self.root/'media'/'data.cab'
        cab.parent.mkdir()
        cab.write_bytes(b'cabinet')
        info = dict(self.info, cabinets=['media/data.cab'])
        with patch.object(engine, 'msi_info', return_value=info):
            installer = engine.supplied_installer(self.program, self.msi)
        folder = self.root/'media-backup'
        copied = engine.copy_installer(installer, folder)
        msi = folder/copied['payloads'][0]['payload']
        self.assertEqual((msi.parent/'media/data.cab').read_bytes(), b'cabinet')


if __name__ == '__main__':
    unittest.main()
