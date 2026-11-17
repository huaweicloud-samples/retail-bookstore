import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app


class HandlerTests(unittest.TestCase):
    def setUp(self):
        os.environ["BOOKSTORE_TEST_JWT_SECRET"] = "handler-secret"
        os.environ["JWT_SECRET"] = "handler-secret"
        app._LOCAL_STORE.__init__()
        app._LOCAL_STORE.put_book({"id": "b1", "name": "One", "category": "x", "description": "desc", "price": 10.0})
        app.SERVICE.store = app._LOCAL_STORE

    def call(self, method, path, body=None, token=None):
        event = {"httpMethod": method, "path": path, "headers": {}, "body": None, "isBase64Encoded": False}
        if token:
            event["headers"]["Authorization"] = "Bearer " + token
        if body is not None:
            event["body"] = base64.b64encode(json.dumps(body).encode()).decode()
            event["isBase64Encoded"] = True
        return app.handler(event, None)

    def test_apig_response_shape_and_public_route(self):
        result = self.call("GET", "/bookstore/v1/books")
        self.assertEqual(result["statusCode"], 200)
        self.assertIn("statusCode", result)
        self.assertFalse(result["isBase64Encoded"])
        self.assertEqual(json.loads(result["body"])[0]["id"], "b1")

    def test_subject_is_required_and_customer_id_is_ignored(self):
        self.assertEqual(self.call("GET", "/bookstore/v1/cart")["statusCode"], 401)
        token = app.issue_test_token("alice")
        self.assertEqual(self.call("PUT", "/bookstore/v1/cart/b1", {"quantity": 1, "customerId": "bob"}, token)["statusCode"], 200)
        bob = app.issue_test_token("bob")
        self.assertEqual(json.loads(self.call("GET", "/bookstore/v1/cart", token=token)["body"])["user_id"], "alice")
        self.assertEqual(json.loads(self.call("GET", "/bookstore/v1/cart", token=bob)["body"])["items"], [])


if __name__ == "__main__":
    unittest.main()
