"""Given/When/Then installation contracts; runnable without dependencies."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class InstallationContracts(unittest.TestCase):
    def test_given_empty_config_when_initialized_then_private_and_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'installation.env'
            command = ['python3', str(ROOT / 'ops/configure.py'), str(target), 'http://127.0.0.1:18391', '18391', 'twiggle-test']
            first = subprocess.run(command, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            original = target.read_bytes()
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b'OWNER_PASSWORD=', original)
            values = dict(line.split('=', 1) for line in original.decode().splitlines())
            self.assertEqual(len(values['POSTGRES_PASSWORD']), 64)
            self.assertEqual(len(values['TWIGGLE_SECRET_KEY']), 96)
            self.assertNotEqual(values['POSTGRES_PASSWORD'], values['TWIGGLE_SECRET_KEY'])
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(target.read_bytes(), original)

    def test_given_non_loopback_http_origin_when_initialized_then_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'installation.env'
            result = subprocess.run(['python3', str(ROOT / 'ops/configure.py'), str(target), 'http://example.com', '18391', 'twiggle-test'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())

    def test_given_newline_in_origin_when_initialized_then_refused_without_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'installation.env'
            result = subprocess.run(['python3', str(ROOT / 'ops/configure.py'), str(target), 'https://example.com\nTWIGGLE_PORT=9', '18391', 'twiggle-test'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())

    def test_given_missing_installer_when_installing_then_setup_exists(self):
        self.assertTrue((ROOT / 'setup.sh').is_file(), 'portable setup is not implemented')

if __name__ == '__main__':
    unittest.main()
