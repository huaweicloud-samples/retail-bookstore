import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app


class AuthTests(unittest.TestCase):
    def setUp(self):
        os.environ["BOOKSTORE_TEST_JWT_SECRET"] = "test-secret"
        os.environ["JWT_SECRET"] = "test-secret"
        os.environ.pop("JWT_ISSUER", None)
        os.environ.pop("JWT_AUDIENCE", None)

    def test_round_trip_subject(self):
        token = app.issue_test_token("user-a")
        self.assertEqual(app.verify_jwt(token)["sub"], "user-a")

    def test_tampered_token_rejected(self):
        token = app.issue_test_token("user-a")
        with self.assertRaises(PermissionError):
            app.verify_jwt(token[:-1] + ("a" if token[-1] != "a" else "b"))

    def test_expired_token_rejected(self):
        token = app.issue_test_token("user-a", ttl_seconds=-1)
        with self.assertRaises(PermissionError):
            app.verify_jwt(token)


if __name__ == "__main__":
    unittest.main()
