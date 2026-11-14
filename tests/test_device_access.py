"""Tests for the door rules: who may reach Remielle from another device."""
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import device_access
import remielle_config


class LocalMachine(unittest.TestCase):
    def test_only_this_computer_is_trusted_by_default(self):
        for ip in ("127.0.0.1", "::1", "localhost"):
            self.assertTrue(device_access.is_local(ip), ip)
        for ip in ("192.168.1.20", "10.0.0.5", "8.8.8.8", "", None):
            self.assertFalse(device_access.is_local(ip), ip)


class TheToken(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env_file = Path(self.dir.name) / ".env"

    def tearDown(self):
        self.dir.cleanup()

    def test_it_is_made_once_and_remembered(self):
        environ = {}
        first = device_access.ensure_token(environ, self.env_file)
        self.assertGreaterEqual(len(first), 16)
        self.assertEqual(environ[device_access.TOKEN_NAME], first)
        self.assertIn(first, self.env_file.read_text(encoding="utf-8"))
        self.assertEqual(device_access.ensure_token({}, self.env_file), first, "a restart must not re-roll it")

    def test_a_real_environment_variable_wins(self):
        environ = {device_access.TOKEN_NAME: "  already-set  "}
        self.assertEqual(device_access.ensure_token(environ, self.env_file), "already-set")
        self.assertFalse(self.env_file.exists(), "an environment token is not written to disk")

    def test_a_commented_out_line_is_not_a_token(self):
        self.env_file.write_text(f"#{device_access.TOKEN_NAME}=nope\nOTHER=1\n", encoding="utf-8")
        self.assertNotEqual(device_access.ensure_token({}, self.env_file), "nope")

    def test_she_still_starts_when_dot_env_cannot_be_written(self):
        missing_dir = Path(self.dir.name) / "nowhere" / ".env"
        token = device_access.ensure_token({}, missing_dir)
        self.assertTrue(token)
        self.assertFalse(missing_dir.exists())

    def test_the_token_is_long_enough_to_not_be_guessed(self):
        self.assertEqual(len(set(device_access.generate_token() for _ in range(20))), 20)


class CheckingAToken(unittest.TestCase):
    def test_the_right_one_passes_and_the_rest_do_not(self):
        self.assertTrue(device_access.token_matches("secret123", "secret123"))
        for wrong in ("", None, "secret12", "secret1234", "SECRET123", " secret123 "):
            self.assertFalse(device_access.token_matches(wrong, "secret123"), repr(wrong))

    def test_no_token_configured_means_no_one_from_away_gets_in(self):
        self.assertFalse(device_access.token_matches("anything", ""))

    def test_a_giant_or_punctuated_value_is_trimmed_not_used_raw(self):
        self.assertFalse(device_access.token_matches("a" * 5000, "a" * 20))
        self.assertFalse(device_access.token_matches("../../etc/passwd", "secret"))


class Addresses(unittest.TestCase):
    def test_only_an_address_a_phone_can_reach_is_printed(self):
        for ip, expected in (("192.168.1.20", True), ("10.5.0.9", True), ("172.16.0.1", True),
                             ("127.0.0.1", False), ("169.254.8.8", False), ("::1", False),
                             ("8.8.8.8", False), ("nonsense", False), ("", False)):
            self.assertEqual(device_access.is_routable(ip), expected, ip)

    def test_local_ip_is_a_real_address(self):
        ip = device_access.local_ip()
        self.assertTrue(socket.inet_aton(ip))

    def test_a_machine_with_no_network_still_gets_a_usable_answer(self):
        with mock.patch.object(device_access, "_new_probe", side_effect=OSError("no sockets")):
            self.assertEqual(device_access.local_ip(), "127.0.0.1")

    def test_a_cable_pulled_mid_probe_is_not_a_crash(self):
        probe = mock.Mock()
        probe.connect.side_effect = OSError("network down")
        with mock.patch.object(device_access, "_new_probe", return_value=probe):
            self.assertEqual(device_access.local_ip(), "127.0.0.1")
        probe.close.assert_called_once()

    def test_the_link_she_prints_carries_the_token(self):
        self.assertEqual(device_access.phone_url("abc", "192.168.1.7", 8000, True),
                         "https://192.168.1.7:8000/?token=abc")
        self.assertEqual(device_access.phone_url("abc", "192.168.1.7", 8000, False),
                         "http://192.168.1.7:8000/?token=abc")


class WhereSheListens(unittest.TestCase):
    def test_she_listens_on_every_interface_unless_told_otherwise(self):
        self.assertEqual(remielle_config.APP_HOST, "0.0.0.0")

    def test_https_follows_the_certificate(self):
        with mock.patch.object(remielle_config, "cert_paths", return_value=(None, None)), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(remielle_config.https_is_on())
        with mock.patch.object(remielle_config, "cert_paths", return_value=("k.pem", "c.pem")), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(remielle_config.https_is_on())

    def test_https_can_be_forced_off_even_with_a_certificate(self):
        with mock.patch.object(remielle_config, "cert_paths", return_value=("k.pem", "c.pem")), \
             mock.patch.dict("os.environ", {"REMIELLE_HTTPS": "0"}, clear=True):
            self.assertFalse(remielle_config.https_is_on())

    def test_https_can_be_forced_on_without_one(self):
        with mock.patch.object(remielle_config, "cert_paths", return_value=(None, None)), \
             mock.patch.dict("os.environ", {"REMIELLE_HTTPS": "yes"}, clear=True):
            self.assertTrue(remielle_config.https_is_on())

    def test_missing_files_mean_no_https(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(remielle_config, "CERT_DIR", Path(directory)):
                self.assertEqual(remielle_config.cert_paths(), (None, None))

    def test_both_files_are_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "remielle.pem").write_text("x", encoding="utf-8")
            with mock.patch.object(remielle_config, "CERT_DIR", root):
                self.assertEqual(remielle_config.cert_paths(), (None, None))
            (root / "remielle-key.pem").write_text("y", encoding="utf-8")
            with mock.patch.object(remielle_config, "CERT_DIR", root):
                self.assertEqual(remielle_config.cert_paths(),
                                 (str(root / "remielle-key.pem"), str(root / "remielle.pem")))


if __name__ == "__main__":
    unittest.main()
