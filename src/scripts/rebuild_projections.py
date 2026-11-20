#!/usr/bin/env python3
"""Rebuild purchase projections and the namespaced DCS Sorted Set from orders."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from app import DCSRanking, GeminiAdapter  # noqa: E402


def clear_projection(store, table: str, fields) -> int:
    items = store.db.scan_items(table)
    for start in range(0, len(items), 25):
        requests = [{"DeleteRequest": {"Key": {field: item[field] for field in fields}}} for item in items[start : start + 25]]
        if requests:
            store.db.batch_write_item({table: requests})
    return len(items)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    namespace = os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")
    if args.local:
        print(json.dumps({"namespace": namespace, "rebuilt": False, "reason": "local projections live in the calling process"}))
        return 0
    if os.environ.get("BOOKSTORE_MODE", "").strip().lower() != "cloud":
        parser.error("BOOKSTORE_MODE=cloud is required unless --local is used")
    if not os.environ.get("GEMINI_ENDPOINT"):
        parser.error("GEMINI_ENDPOINT is required in cloud mode")
    store = GeminiAdapter.from_env()
    store.ensure_tables()
    by_user = defaultdict(Counter)
    by_book = defaultdict(Counter)
    orders = store.db.scan_items("orders")
    for order in orders:
        if order.get("status") != "PAID_SIMULATED":
            continue
        user_id = str(order.get("user_id", ""))
        for line in order.get("items", []):
            book_id = str(line.get("book_id", ""))
            quantity = int(line.get("quantity", 0))
            by_user[user_id][book_id] += quantity
            by_book[book_id][user_id] += quantity
    cleared_user = clear_projection(store, "purchases_by_user", ("user_id", "book_id"))
    cleared_book = clear_projection(store, "purchases_by_book", ("book_id", "user_id"))
    for user_id, books in by_user.items():
        for book_id, quantity in books.items():
            store._put("purchases_by_user", {"user_id": user_id, "book_id": book_id, "quantity": quantity})
    for book_id, users in by_book.items():
        for user_id, quantity in users.items():
            store._put("purchases_by_book", {"book_id": book_id, "user_id": user_id, "quantity": quantity})
    ranking = DCSRanking(os.environ.get("DCS_ENDPOINT", ""), int(os.environ.get("DCS_PORT", "6379")), os.environ.get("DCS_TLS", "true").lower() == "true", os.environ.get("DCS_PASSWORD", ""), namespace)
    ranking.clear()
    for book_id, users in by_book.items():
        ranking.increment(book_id, sum(users.values()))
    result = {"namespace": namespace, "orders_scanned": len(orders), "users": len(by_user), "books": len(by_book), "cleared_projection_items": cleared_user + cleared_book}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
