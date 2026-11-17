#!/usr/bin/env python3
"""Load the small bookstore fixture set into local memory or configured data planes."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from app import CSSSearch, DCSRanking, GeminiAdapter, MemoryStore  # noqa: E402


def read_json(name: str):
    return json.loads((ROOT / "src" / "fixtures" / name).read_text(encoding="utf-8"))


def load(store, search, ranking):
    """Bootstrap tables first, then replace only this application's fixture data."""
    if hasattr(store, "ensure_tables"):
        store.ensure_tables()
    if isinstance(store, MemoryStore):
        store.__init__()
    elif hasattr(store, "delete_namespace"):
        store.delete_namespace()
    search.delete_index()
    search.ensure_index()
    ranking.clear()
    books = read_json("books.json")
    friends = read_json("friends.json")
    purchases = read_json("purchases.json")
    for book in books:
        if hasattr(store, "put_book"):
            store.put_book(book)
        else:
            store._put("books", book)
        search.index_book(book)
    for relation in friends:
        store.put_friend(relation["user_id"], relation["friend_id"])
    for purchase in purchases:
        store.add_purchase(purchase["user_id"], purchase["book_id"], int(purchase["quantity"]))
        ranking.increment(purchase["book_id"], int(purchase["quantity"]))
    return {"books": len(books), "friends": len(friends), "purchases": len(purchases), "namespace": os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="load the in-memory backend; useful with an in-process caller")
    args = parser.parse_args()
    namespace = os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")
    if args.local:
        store = MemoryStore()
        search = CSSSearch(index=f"{namespace}-books")
        ranking = DCSRanking(namespace=namespace)
    else:
        if os.environ.get("BOOKSTORE_MODE", "").strip().lower() != "cloud":
            parser.error("BOOKSTORE_MODE=cloud is required unless --local is used")
        if not os.environ.get("GEMINI_ENDPOINT"):
            parser.error("GEMINI_ENDPOINT is required in cloud mode")
        store = GeminiAdapter.from_env()
        search = CSSSearch(os.environ.get("CSS_ENDPOINT", ""), int(os.environ.get("CSS_PORT", "443")), os.environ.get("CSS_TLS", "true").lower() == "true", os.environ.get("CSS_USERNAME", ""), os.environ.get("CSS_PASSWORD", ""), os.environ.get("CSS_TOKEN", ""), f"{namespace}-books")
        ranking = DCSRanking(os.environ.get("DCS_ENDPOINT", ""), int(os.environ.get("DCS_PORT", "6379")), os.environ.get("DCS_TLS", "true").lower() == "true", os.environ.get("DCS_PASSWORD", ""), namespace)
    print(json.dumps(load(store, search, ranking), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
