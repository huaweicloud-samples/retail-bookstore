import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from app import BookstoreService, CSSSearch, DCSRanking, MemoryStore


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.store.put_book({"id": "b1", "name": "One", "category": "x", "description": "first", "price": 10.0})
        self.store.put_book({"id": "b2", "name": "Two", "category": "x", "description": "second", "price": 7.5})
        self.service = BookstoreService(self.store, CSSSearch(), DCSRanking(namespace="test"))

    def test_checkout_reads_server_price_and_is_idempotent(self):
        self.service.change_cart("alice", "b1", 2)
        order = self.service.checkout("alice", {"items": [{"book_id": "b1", "quantity": 2, "unit_price": 0.01}]}, "same-key")
        repeated = self.service.checkout("alice", {"items": [{"book_id": "b2", "quantity": 10}]}, "same-key")
        self.assertEqual(order["total"], 20.0)
        self.assertEqual(order["id"], repeated["id"])
        self.assertEqual(self.store.purchased_by_user("alice"), {"b1": 2})

    def test_friend_and_book_projection_recommendations(self):
        self.store.put_friend("alice", "bob")
        self.store.add_purchase("bob", "b2", 2)
        self.store.add_purchase("carol", "b1", 1)
        self.store.add_purchase("carol", "b2", 1)
        friends = self.service.recommendations_for_friends("alice", 5)
        related = self.service.recommendations_for_book("b1", 5)
        self.assertEqual(friends[0]["id"], "b2")
        self.assertEqual(related[0]["id"], "b2")

    def test_redis_command_uses_bulk_string_prefixes(self):
        self.assertEqual(DCSRanking._command("AUTH", "secret"), b"*2\r\n$4\r\nAUTH\r\n$6\r\nsecret\r\n")


if __name__ == "__main__":
    unittest.main()
