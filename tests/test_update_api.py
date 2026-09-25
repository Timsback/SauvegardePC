from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from web_main import BackupAPI


class UpdateAPITests(unittest.TestCase):
    def test_busy_backup_or_restore_blocks_update_installation(self):
        api = BackupAPI.__new__(BackupAPI)
        api.busy = True
        api.lock = threading.RLock()
        api.updates = MagicMock()
        with patch.object(sys, 'frozen', True, create=True):
            result = api.install_update()
        self.assertFalse(result['ok'])
        api.updates.download.assert_not_called()

    def test_disabled_automatic_check_still_allows_manual_check(self):
        api = BackupAPI.__new__(BackupAPI)
        api.config = {'auto_check_updates': False}
        api.updates = MagicMock()
        api.check_updates(manual=False)
        api.updates.check.assert_not_called()
        api.check_updates(manual=True)
        api.updates.check.assert_called_once()


if __name__ == '__main__':
    unittest.main()
