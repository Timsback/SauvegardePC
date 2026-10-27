from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import backup_engine as engine
import backup_tasks as bt
import scheduler
import vault
import web_main
from web_main import BackupAPI


def make_api(dest):
    api = BackupAPI.__new__(BackupAPI)
    api.config = json.loads(json.dumps(bt.DEFAULT_CONFIG))
    api.config.update(last_destination=str(dest), selected_categories=['files'], selected_folders=['Documents'],
                      auto_check_updates=True)
    api.lock = threading.RLock()
    api.events, api.session_log, api.operation_log, api.event_seq = [], [], [], 0
    api.busy, api.task, api.progress, api.progress_label = False, '', 0, ''
    api.job, api.cancelable, api.estimate, api.versions, api.dest_info = None, False, None, [], {}
    api.restore_versions, api.restore_version, api.restore_categories, api.restore_browsers = [], '', [], []
    api.restore_source, api.auto_seen, api.auto_enabled = '', '', False
    api.restore_sensitive, api.restore_private, api.clear_folders = {}, None, 0
    api.software_plan, api.software_backups, api.software_result = None, [], None
    api.updates = MagicMock()
    api.updates.snapshot.return_value = {}
    return api


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name)
        for target, name in ((bt, 'save_config'), (BackupAPI, '_refresh_destination')):
            patcher = patch.object(target, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        status = patch.object(scheduler, 'STATUS_FILE', self.dest / 'status.json')
        status.start()
        self.addCleanup(status.stop)
        config_dir = patch.object(bt, 'CONFIG_DIR', self.dest / 'config')
        config_dir.start()
        self.addCleanup(config_dir.stop)
        self.api = make_api(self.dest)
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        # Never leak a running worker (it would hold the operation mutex for later tests).
        if self.api.busy:
            bt.CANCEL.set()
            self.wait()
        bt.CANCEL.clear()

    def wait(self):
        deadline = time.monotonic() + 10
        while self.api.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.api.busy)


class BackupReportTests(ApiTest):
    def run_backup(self, lines):
        def fake_backup(root, config, categories, job, version):
            folder = Path(root) / f'SauvegardePC_test_{len(list(Path(root).glob("SauvegardePC_*")))}'
            folder.mkdir()
            for line in lines:
                job.log(line)
            return dict(partial=False, folder=str(folder), report='', message='')
        with patch.object(engine, 'run_backup', side_effect=fake_backup):
            self.assertTrue(self.api.start_backup()['ok'])
            self.wait()
        folder = sorted(self.dest.glob('SauvegardePC_*'))[-1]
        return (folder/'journal.txt').read_text(encoding='utf-8').splitlines()

    def test_journal_keeps_every_line_beyond_ui_limit(self):
        lines = [f'ligne {n}' for n in range(1500)]
        report = self.run_backup(lines)
        self.assertEqual(report[3:], lines)
        self.assertLessEqual(len(self.api.events), 600)

    def test_journal_only_contains_current_backup(self):
        self.run_backup(['première sauvegarde'])
        report = self.run_backup(['seconde sauvegarde'])
        self.assertIn('seconde sauvegarde', report)
        self.assertNotIn('première sauvegarde', report)

    def test_manual_report_contains_whole_session(self):
        self.run_backup(['première sauvegarde'])
        self.run_backup(['seconde sauvegarde'])
        report = Path(self.api.write_report()).read_text(encoding='utf-8').splitlines()
        self.assertIn('première sauvegarde', report)
        self.assertIn('seconde sauvegarde', report)
        self.assertTrue(self.api.config['last_backup_at'])


class OperationTests(ApiTest):
    def test_cancel_stops_backup_and_reports_it(self):
        started = threading.Event()
        def slow_backup(root, config, categories, job, version):
            started.set()
            while True:
                bt.check_cancel()
                time.sleep(0.01)
        with patch.object(engine, 'run_backup', side_effect=slow_backup):
            self.api.start_backup()
            started.wait(5)
            self.assertTrue(self.api.get_state()['cancelable'])
            self.assertTrue(self.api.cancel_operation()['ok'])
            self.wait()
        self.assertEqual({k: self.api.events[-1][k] for k in ('kind', 'message')}, {'kind': 'warning', 'message': 'Sauvegarde annulé.'})
        self.assertRegex(self.api.events[-1]['at'], r'^\d\d:\d\d:\d\d$')
        self.assertFalse(bt.CANCEL.is_set())
        self.assertFalse(self.api.config['last_backup_at'])

    def test_operation_refused_while_scheduled_backup_holds_the_lock(self):
        holder = bt.OperationLock()
        self.assertTrue(holder.acquire())
        try:
            with patch.object(engine, 'run_backup') as run:
                self.api.start_backup()
                self.wait()
            run.assert_not_called()
            self.assertEqual(self.api.events[-1]['kind'], 'error')
            self.assertIn('automatique', self.api.events[-1]['message'])
        finally:
            holder.release()

    def test_settings_are_validated(self):
        self.api.set_setting('keep_versions', 5)
        self.assertEqual(self.api.config['keep_versions'], 5)
        for name, value in (('keep_versions', 0), ('onedrive_cloud', 'tout'), ('inconnu', 1)):
            with self.assertRaises(ValueError):
                self.api.set_setting(name, value)
        self.api.add_exclusion(' *.iso ')
        self.api.add_exclusion('*.ISO')
        self.assertEqual(self.api.config['exclusions'].count('*.iso'), 1)
        with self.assertRaises(ValueError):
            self.api.add_exclusion('C:\\Windows')
        self.api.remove_exclusion('*.iso')
        self.assertNotIn('*.iso', self.api.config['exclusions'])

    def test_restore_only_offers_categories_present(self):
        self.api.restore_version = str(self.dest)
        self.api.restore_categories = ['files']
        self.assertFalse(self.api.restore_full(['wifi'])['ok'])
        with patch.object(engine, 'restore', return_value=dict(partial=False)) as restore:
            self.assertTrue(self.api.restore_full(['files', 'wifi'])['ok'])
            self.wait()
        self.assertEqual(restore.call_args.args[1], ['files'])

    def test_sensitive_categories_require_password(self):
        self.api.config['selected_categories'] = ['files', 'licenses']
        result = self.api.start_backup()
        self.assertEqual(result['code'], 'vault_required')
        self.assertFalse(self.api.setup_vault('court')['ok'])
        created = self.api.setup_vault('mot de passe solide')
        self.assertEqual(len(created['recovery'].replace('-', '')), 24)
        self.assertFalse(self.api.setup_vault('autre mot de passe')['ok'])
        self.assertFalse(self.api.change_vault_password('faux', 'nouveau mot de passe')['ok'])
        self.assertTrue(self.api.change_vault_password(created['recovery'], 'nouveau mot de passe')['ok'])
        vault.unlock(self.api.config['vault'], 'nouveau mot de passe')

    def test_encrypted_wifi_restore_asks_password_and_new_pc_adopts_vault(self):
        doc, _ = vault.create('mot de passe solide')
        folder = self.dest / 'SauvegardePC_PC_2026'
        vault.write_blob(folder / 'WiFi' / vault.WIFI_BLOB, doc, vault.zip_files([]))
        self.api.restore_versions = [dict(folder=str(folder))]
        with patch.object(engine, 'browsers_in', return_value=[]):
            self.api.set_restore_version(str(folder))
        self.assertEqual(self.api.restore_sensitive['wifi'], 'encrypted')
        self.assertEqual(self.api.restore_full(['wifi'])['code'], 'secret_required')
        self.assertFalse(self.api.unlock_restore('faux mot de passe')['ok'])
        self.assertTrue(self.api.unlock_restore('mot de passe solide')['ok'])
        self.assertEqual(self.api.config['vault']['public_key'], doc['public_key'])
        with patch.object(engine, 'restore', return_value=dict(partial=False)) as restore:
            self.assertTrue(self.api.restore_full(['wifi'])['ok'])
            self.wait()
        self.assertIsNotNone(restore.call_args.kwargs['private'])

    def test_licences_list_kept_on_this_pc_only(self):
        self.api.save_licenses([{'logiciel': 'Office', 'cle': 'AAAAA'}])
        self.assertEqual(self.api.get_licenses(), [{'logiciel': 'Office', 'cle': 'AAAAA'}])
        self.assertFalse((self.dest / 'Licences').exists())

    def test_open_folder_refuses_unknown_paths(self):
        with self.assertRaises(ValueError):
            self.api.open_folder('C:\\Windows')


class AutoBackupTests(ApiTest):
    def setUp(self):
        super().setUp()
        self.config = json.loads(json.dumps(bt.DEFAULT_CONFIG))
        self.config.update(last_destination=str(self.dest), selected_categories=['files'],
                           auto_backup={'enabled': True, 'interval_days': 1})
        patcher = patch.object(bt, 'load_config', side_effect=lambda: self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_runs_when_due_and_records_success(self):
        folder = self.dest / 'SauvegardePC_auto'
        folder.mkdir()
        with patch.object(engine, 'run_backup', return_value=dict(partial=False, folder=str(folder), message='')) as run:
            self.assertEqual(web_main.auto_backup_main(), 0)
        run.assert_called_once()
        status = scheduler.read_status()
        self.assertEqual(status['last_result'], 'complete')
        self.assertTrue(status['last_success'])
        self.assertEqual(scheduler.last_backup_at(self.config), status['last_success'])
        self.assertTrue((folder / 'journal.txt').exists())

    def test_skips_when_not_due_disabled_or_unplugged(self):
        with patch.object(engine, 'run_backup') as run:
            self.config['last_backup_at'] = datetime.now().isoformat(timespec='minutes')
            web_main.auto_backup_main()
            self.config['last_backup_at'] = ''
            self.config['auto_backup']['enabled'] = False
            web_main.auto_backup_main()
            self.config['auto_backup']['enabled'] = True
            self.config['last_destination'] = str(self.dest / 'disque débranché')
            web_main.auto_backup_main()
        run.assert_not_called()

    def test_failure_is_recorded(self):
        with patch.object(engine, 'run_backup', side_effect=OSError('Espace insuffisant')):
            self.assertEqual(web_main.auto_backup_main(), 1)
        self.assertEqual(scheduler.read_status()['last_result'], 'error')
        self.assertIn('Espace insuffisant', scheduler.read_status()['last_message'])

    def test_is_due_follows_interval(self):
        now = datetime(2026, 10, 26, 12, 0)
        config = dict(auto_backup={'interval_days': 7}, last_backup_at=(now - timedelta(days=6)).isoformat())
        self.assertFalse(scheduler.is_due(config, now))
        config['last_backup_at'] = (now - timedelta(days=7)).isoformat()
        self.assertTrue(scheduler.is_due(config, now))
        self.assertTrue(scheduler.is_due(dict(auto_backup={}, last_backup_at=''), now))

    def test_task_registration_arguments(self):
        calls = []
        result = type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.object(scheduler, '_schtasks', side_effect=lambda *a: calls.append(a) or result):
            scheduler.enable()
            scheduler.disable()
        create = calls[0]
        self.assertEqual(create[create.index('/SC') + 1], 'MINUTE')
        self.assertEqual(create[create.index('/MO') + 1], '30')
        self.assertTrue(create[create.index('/TR') + 1].endswith('--auto-backup'))
        self.assertEqual(calls[1][0], '/Delete')


if __name__ == '__main__':
    unittest.main()
