from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import vault


class VaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.recovery = vault.create('mot de passe solide')

    def test_password_and_recovery_code_open_the_same_key(self):
        blob = vault.encrypt(self.doc, b'cle secrete')
        for secret in ('mot de passe solide', self.recovery, self.recovery.lower().replace('-', ' ')):
            with self.subTest(secret=secret):
                self.assertEqual(vault.decrypt(vault.unlock(self.doc, secret), blob), b'cle secrete')

    def test_wrong_secret_rejected(self):
        with self.assertRaises(vault.VaultError):
            vault.unlock(self.doc, 'mauvais mot de passe')

    def test_encrypted_data_never_contains_the_plain_text(self):
        blob = vault.encrypt(self.doc, b'XXXXX-YYYYY-ZZZZZ')
        self.assertNotIn(b'XXXXX', blob)
        self.assertNotEqual(blob, vault.encrypt(self.doc, b'XXXXX-YYYYY-ZZZZZ'))

    def test_tampered_or_foreign_blob_rejected(self):
        blob = bytearray(vault.encrypt(self.doc, b'data'))
        blob[-1] ^= 1
        private = vault.unlock(self.doc, 'mot de passe solide')
        with self.assertRaises(vault.VaultError):
            vault.decrypt(private, bytes(blob))
        other, _ = vault.create('un autre mot de passe')
        with self.assertRaises(vault.VaultError):
            vault.decrypt(private, vault.encrypt(other, b'data'))

    def test_change_password_with_recovery_code(self):
        changed = vault.change_password(self.doc, self.recovery, 'nouveau mot de passe')
        blob = vault.encrypt(self.doc, b'data')
        self.assertEqual(vault.decrypt(vault.unlock(changed, 'nouveau mot de passe'), blob), b'data')
        with self.assertRaises(vault.VaultError):
            vault.unlock(changed, 'mot de passe solide')
        vault.unlock(changed, self.recovery)

    def test_short_password_refused(self):
        with self.assertRaises(vault.VaultError):
            vault.create('court')

    def test_blob_written_with_version_vault(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'Licences'/vault.LICENSES_BLOB
            vault.write_blob(path, self.doc, b'data')
            self.assertEqual(vault.read_vault(Path(directory))['public_key'], self.doc['public_key'])
            self.assertFalse(list(Path(directory).rglob('*.part')))

    def test_zip_round_trip_ignores_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'a.xml'
            source.write_text('<WLANProfile/>')
            files = vault.unzip_to(vault.zip_files([source]), Path(directory)/'out')
            self.assertEqual([f.name for f in files], ['a.xml'])

    def test_local_licenses_protected_by_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'licences.protege'
            vault.save_local_licenses(path, [{'logiciel': 'Office', 'cle': 'AAAAA-BBBBB'}])
            self.assertNotIn(b'AAAAA', path.read_bytes())
            self.assertEqual(vault.load_local_licenses(path), [{'logiciel': 'Office', 'cle': 'AAAAA-BBBBB'}])
            self.assertEqual(vault.load_local_licenses(Path(directory)/'absent'), [])


if __name__ == '__main__':
    unittest.main()
