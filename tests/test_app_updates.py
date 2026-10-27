import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import app_updates as updates


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.repository = 'example/SauvegardePC'
        self.content = b'installer fixture, never execute'
        self.checksum = hashlib.sha256(self.content).hexdigest()
        self.prefix = f'https://github.com/{self.repository}/releases/download/v1.10.0/'
        self.data = dict(tag_name='v1.10.0', draft=False, prerelease=False, body='Changes', assets=[
            dict(name=updates.ASSET, state='uploaded', size=len(self.content),
                 browser_download_url=self.prefix+updates.ASSET, digest='sha256:'+self.checksum)])

    def release(self, current='1.9.9'):
        return updates.find_release(self.repository, current, lambda url: json.dumps(self.data).encode())

    def test_semantic_version_comparison(self):
        self.assertEqual(self.release()['version'], '1.10.0')
        self.assertIsNone(self.release('1.10.0'))
        self.assertIsNone(self.release('2.0.0'))

    def test_drafts_and_previews_not_offered(self):
        for field in ('draft', 'prerelease'):
            with self.subTest(field=field):
                self.data[field] = True
                self.assertIsNone(self.release())
                self.data[field] = False

    def test_download_must_belong_to_configured_repository(self):
        self.data['assets'][0]['browser_download_url'] = 'https://github.com/other/repo/releases/download/v1.10.0/'+updates.ASSET
        with self.assertRaises(ValueError):
            self.release()

    def test_checksum_sidecar_supported(self):
        self.data['assets'][0]['digest'] = None
        self.data['assets'].append(dict(name=updates.ASSET+'.sha256', state='uploaded', browser_download_url=self.prefix+updates.ASSET+'.sha256'))
        def fetch(url):
            return (self.checksum+'  '+updates.ASSET+'\n').encode() if url.endswith('.sha256') else json.dumps(self.data).encode()
        release = updates.find_release(self.repository, '1.9.9', fetch)
        self.assertEqual(release['sha256'], self.checksum)

    def test_missing_checksum_and_incomplete_release_rejected(self):
        self.data['assets'][0]['digest'] = None
        with self.assertRaises(ValueError):
            self.release()
        self.data['assets'] = []
        with self.assertRaises(ValueError):
            self.release()

    def test_unsafe_redirects_rejected(self):
        for url in ['http://github.com/file', 'https://github.com.evil.test/file', 'file:///tmp/setup.exe',
                    'https://user:pass@github.com/file', 'https://github.com:444/file']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                updates.safe_url(url)
        updates.safe_url('https://release-assets.githubusercontent.com/asset')

    def test_verified_download_promoted_only_when_complete(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(updates, 'open_url', return_value=io.BytesIO(self.content)):
            path = updates.download_release(self.release(), directory)
            self.assertEqual(path.read_bytes(), self.content)
            self.assertFalse(list(Path(directory).rglob('*.part')))

    def test_corrupt_truncated_oversized_download_never_executable(self):
        for content in (b'wrong', self.content[:-1], self.content+b'extra'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory, \
                 patch.object(updates, 'open_url', return_value=io.BytesIO(content)):
                with self.assertRaises(ValueError):
                    updates.download_release(self.release(), directory)
                self.assertFalse(list(Path(directory).rglob('*.exe')))

    def test_installer_rechecked_before_launch_and_preserves_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/updates.ASSET
            path.write_bytes(self.content)
            with patch.object(updates.sys, 'frozen', True, create=True), \
                 patch.object(updates.sys, 'executable', str(Path(directory)/'installed'/'SauvegardePC.exe')), \
                 patch.dict(updates.os.environ, {'_PYI_ARCHIVE_FILE': 'old.exe', '_PYI_PARENT_PROCESS_LEVEL': '1'}), \
                 patch.object(updates.os, 'startfile', create=True) as launch:
                updates.launch_installer(path, self.checksum)
                self.assertIn('/NOCLOSEAPPLICATIONS', launch.call_args.args[2])
                self.assertIn('installed', launch.call_args.args[2])
                # The installer relaunches the new SauvegardePC.exe: it must not inherit this process's
                # PyInstaller state, or the new version stops with "Security validation failure".
                self.assertFalse([n for n in updates.os.environ if n.startswith('_PYI_')])
                self.assertEqual(updates.os.environ['PYINSTALLER_RESET_ENVIRONMENT'], '1')
                path.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    updates.launch_installer(path, self.checksum)
                self.assertEqual(launch.call_count, 1)

    def test_check_cannot_interrupt_download(self):
        manager = updates.UpdateManager(self.repository)
        manager.set_state(status='downloading')
        self.assertFalse(manager.check())
        self.assertEqual(manager.snapshot()['status'], 'downloading')


if __name__ == '__main__':
    unittest.main()
