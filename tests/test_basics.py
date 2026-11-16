"""Quick checks that run without the server: python3 -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path

from usher import auth
from usher.store import Store


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.store.add_user("sam", "Sam", auth.hash_password("correct horse battery"))
        self.auth = auth.Auth(self.store, 90)

    def tearDown(self):
        self.tmp.cleanup()

    def test_password_round_trip(self):
        h = auth.hash_password("correct horse battery")
        self.assertTrue(auth.check_password("correct horse battery", h))
        self.assertFalse(auth.check_password("wrong", h))
        self.assertFalse(auth.check_password("x", "garbage"))

    def test_sign_in_and_session(self):
        token, err = self.auth.sign_in("Sam", "correct horse battery", "10.0.0.20", "iPhone")
        self.assertIsNone(err)
        self.assertEqual(self.auth.session(token)["username"], "sam")
        self.assertIsNone(self.auth.session("not-a-token"))
        # only a hash of the token is stored
        self.assertIsNone(self.store.one("SELECT 1 FROM sessions WHERE id_hash = ?", (token,)))

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            self.assertIsNone(self.auth.sign_in("sam", "nope", "10.0.0.20", "")[0])
        token, err = self.auth.sign_in("sam", "correct horse battery", "10.0.0.20", "")
        self.assertIsNone(token)
        self.assertIn("15 minutes", err)

    def test_unknown_user_same_message(self):
        _, err1 = self.auth.sign_in("nobody", "x", "10.0.0.1", "")
        _, err2 = self.auth.sign_in("sam", "x", "10.0.0.2", "")
        self.assertEqual(err1, err2)

    def test_cookie_flags(self):
        c = self.auth.cookie("abc")
        for flag in ("__Host-usher=abc", "Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, c)
        self.assertNotIn("Domain", c)


if __name__ == "__main__":
    unittest.main()
