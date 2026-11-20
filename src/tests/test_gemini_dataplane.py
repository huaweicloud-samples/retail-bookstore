import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from app import GeminiStore  # noqa: E402


class GeminiDataplaneTests(unittest.TestCase):
    def test_endpoint_defaults_to_private_http_port(self):
        store = GeminiStore("10.0.0.10", "bookstore_test")
        self.assertEqual(store.endpoint, "http://10.0.0.10:8000")
        self.assertEqual(store.host, "10.0.0.10:8000")
        self.assertEqual(store.region, "cn-north-4")

    def test_sigv4_request_requires_injected_credentials(self):
        store = GeminiStore("10.0.0.10", "bookstore_test", access_key="", secret_key="")
        with self.assertRaisesRegex(RuntimeError, "access key and secret key"):
            store._signed_request("DescribeTable", b"{}")

    def test_supported_operations_keep_standard_condition_and_wire_shapes(self):
        store = GeminiStore("http://10.0.0.10:8000", "bookstore_test")
        calls = []

        def fake_call(operation, payload):
            calls.append((operation, payload))
            return {"Table": {"TableStatus": "ACTIVE"}}

        store._call = fake_call
        store.put_item("books", {"id": "book-1"}, "attribute_not_exists(id)")
        store.update_item("carts", {"user_id": "alice"}, "SET #items = :items", {":items": []}, {"#items": "items"})
        store.delete_item("books", {"id": "book-1"})
        store.batch_write_item({"friends": [{"PutRequest": {"Item": {"user_id": "alice", "friend_id": "bob"}}}]})

        self.assertEqual([operation for operation, _ in calls], ["PutItem", "UpdateItem", "DeleteItem", "BatchWriteItem"])
        self.assertEqual(calls[0][1]["ConditionExpression"], "attribute_not_exists(id)")
        self.assertEqual(calls[0][1]["Item"]["id"], {"S": "book-1"})
        self.assertEqual(calls[-1][1]["RequestItems"]["bookstore_test_friends"][0]["PutRequest"]["Item"]["friend_id"], {"S": "bob"})

    def test_six_table_schemas_are_namespaced(self):
        store = GeminiStore("10.0.0.10", "bookstore_test")
        self.assertEqual(set(store.table), {"books", "carts", "orders", "friends", "purchases_by_user", "purchases_by_book"})
        self.assertEqual(store.table["books"], "bookstore_test_books")
        self.assertEqual(store.table_definitions["purchases_by_book"]["key_schema"][0]["AttributeName"], "book_id")


if __name__ == "__main__":
    unittest.main()
