#!/usr/bin/env python3
"""Local or APIG E2E checks for identity isolation, checkout and projections."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import app  # noqa: E402


def event(method, path, token="", body=None, headers=None):
    values = {"Content-Type": "application/json"}
    if token:
        values["Authorization"] = f"Bearer {token}"
    if headers:
        values.update(headers)
    parsed = urllib.parse.urlsplit(path)
    query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
    raw = json.dumps(body).encode() if body is not None else None
    return {"httpMethod": method, "path": parsed.path, "queryStringParameters": query, "headers": values, "body": base64.b64encode(raw).decode() if raw else None, "isBase64Encoded": bool(raw)}


def local_setup():
    books = json.loads((ROOT / "src" / "fixtures/books.json").read_text(encoding="utf-8"))
    friends = json.loads((ROOT / "src" / "fixtures/friends.json").read_text(encoding="utf-8"))
    purchases = json.loads((ROOT / "src" / "fixtures/purchases.json").read_text(encoding="utf-8"))
    app._LOCAL_STORE.__init__()
    for book in books:
        app._LOCAL_STORE.put_book(book)
    for relation in friends:
        app._LOCAL_STORE.put_friend(relation["user_id"], relation["friend_id"])
    for purchase in purchases:
        app._LOCAL_STORE.add_purchase(purchase["user_id"], purchase["book_id"], purchase["quantity"])
        app.SERVICE.ranking.increment(purchase["book_id"], purchase["quantity"])
    app.SERVICE.store = app._LOCAL_STORE


def call_local(method, path, token="", body=None, headers=None):
    return app.handler(event(method, path, token, body, headers), None)


def assert_status(result, status):
    if result["statusCode"] != status:
        raise AssertionError(f"expected {status}, got {result['statusCode']}: {result['body']}")
    return json.loads(result["body"] or "{}")


def run_local() -> dict:
    os.environ.setdefault("BOOKSTORE_TEST_JWT_SECRET", "local-e2e-secret")
    os.environ.setdefault("JWT_SECRET", os.environ["BOOKSTORE_TEST_JWT_SECRET"])
    local_setup()
    alice = app.issue_test_token("alice")
    bob = app.issue_test_token("bob")
    assert len(assert_status(call_local("GET", "/bookstore/v1/books"), 200)) >= 5
    detail = assert_status(call_local("GET", "/bookstore/v1/books/book-001"), 200)
    assert detail["id"] == "book-001"
    assert_status(call_local("PUT", "/bookstore/v1/cart/book-001", alice, {"quantity": 2}), 200)
    assert_status(call_local("GET", "/bookstore/v1/cart", bob), 200)
    assert assert_status(call_local("GET", "/bookstore/v1/cart", bob), 200)["items"] == []
    assert_status(call_local("PUT", "/bookstore/v1/cart/book-001", bob, {"quantity": 1, "customerId": "alice"}), 200)
    key = "e2e-" + uuid.uuid4().hex
    first = assert_status(call_local("POST", "/bookstore/v1/checkout", alice, {"idempotency_key": key}), 201)
    second = assert_status(call_local("POST", "/bookstore/v1/checkout", alice, {"idempotency_key": key, "items": [{"book_id": "book-002", "quantity": 99}]}), 201)
    assert first["id"] == second["id"]
    assert first["total"] == 176.0
    assert len(assert_status(call_local("GET", "/bookstore/v1/orders", alice), 200)) == 1
    assert len(assert_status(call_local("GET", "/bookstore/v1/search?q=python", ""), 200)) == 1
    assert len(assert_status(call_local("GET", "/bookstore/v1/bestsellers", ""), 200)) >= 1
    assert_status(call_local("GET", "/bookstore/v1/recommendations/friends", alice), 200)
    assert_status(call_local("GET", "/bookstore/v1/recommendations/books/book-001", alice), 200)
    # customerId in the payload never changes the authenticated owner.
    assert assert_status(call_local("GET", "/bookstore/v1/cart", bob), 200)["items"] == [{"book_id": "book-001", "quantity": 1}]
    return {"passed": True, "mode": "local", "checks": 11}


def load_api_url() -> str:
    value = os.environ.get("BOOKSTORE_API_BASE_URL", "")
    if value:
        return value.rstrip("/")
    raise RuntimeError("BOOKSTORE_API_BASE_URL is required for non-local verification")


def run_remote() -> dict:
    base = load_api_url()
    secret = os.environ.get("BOOKSTORE_TEST_JWT_SECRET")
    if not secret:
        raise RuntimeError("BOOKSTORE_TEST_JWT_SECRET is required")
    os.environ.setdefault("JWT_SECRET", secret)
    primary_subject = os.environ.get("BOOKSTORE_TEST_SUBJECT", "alice")
    secondary_subject = os.environ.get("BOOKSTORE_TEST_SECONDARY_SUBJECT", "bob")
    primary_token = app.issue_test_token(primary_subject)
    secondary_token = app.issue_test_token(secondary_subject)
    stage = os.environ.get("BOOKSTORE_APIG_STAGE", "prod")
    observations = []

    def request(method, path, token, payload=None, idempotency_key=""):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Stage": stage,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                status = response.status
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read().decode("utf-8", "replace")
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {"raw": raw[:500]}
        observations.append({"method": method, "path": path, "status": status})
        return status, value

    def expect(status, value, expected, label):
        if status != expected:
            raise AssertionError(f"{label}: expected HTTP {expected}, got {status}: {value}")
        return value

    books = expect(*request("GET", "/books", primary_token), 200, "books")
    if not isinstance(books, list) or len(books) < 5:
        raise AssertionError(f"books: expected at least five books, got {books}")
    detail = expect(*request("GET", "/books/book-001", primary_token), 200, "book detail")
    if detail.get("id") != "book-001":
        raise AssertionError(f"book detail returned the wrong book: {detail}")
    categories = expect(*request("GET", "/categories", primary_token), 200, "categories")
    architecture = expect(*request("GET", "/categories/architecture", primary_token), 200, "category")
    if not categories or len(architecture) < 2:
        raise AssertionError("category projection is empty")
    search = expect(*request("GET", "/search?q=python", primary_token), 200, "CSS search")
    if not search or search[0].get("id") != "book-002":
        raise AssertionError(f"CSS search did not return book-002: {search}")

    expect(*request("PUT", "/cart/book-001", primary_token, {"quantity": 2}), 200, "primary cart update")
    primary_cart = expect(*request("GET", "/cart", primary_token), 200, "primary cart read")
    secondary_cart = expect(*request("GET", "/cart", secondary_token), 200, "secondary cart isolation")
    if primary_cart.get("items") != [{"book_id": "book-001", "quantity": 2}]:
        raise AssertionError(f"primary cart mismatch: {primary_cart}")
    if secondary_cart.get("items") != []:
        raise AssertionError(f"cart leaked between users: {secondary_cart}")
    expect(*request("PUT", "/cart/book-002", secondary_token, {"quantity": 1}), 200, "secondary cart update")

    idempotency_key = "remote-e2e-" + uuid.uuid4().hex
    first_order = expect(
        *request("POST", "/checkout", primary_token, {}, idempotency_key),
        201,
        "checkout",
    )
    second_order = expect(
        *request("POST", "/checkout", primary_token, {"items": [{"book_id": "book-002", "quantity": 99}]}, idempotency_key),
        201,
        "idempotent checkout replay",
    )
    if first_order.get("id") != second_order.get("id") or first_order.get("total") != 176.0:
        raise AssertionError(f"checkout idempotency mismatch: first={first_order}, second={second_order}")
    orders = expect(*request("GET", "/orders", primary_token), 200, "orders")
    order_detail = expect(*request("GET", f"/orders/{first_order['id']}", primary_token), 200, "order detail")
    if len(orders) != 1 or order_detail.get("id") != first_order.get("id"):
        raise AssertionError(f"order projection mismatch: orders={orders}, detail={order_detail}")

    bestsellers = expect(*request("GET", "/bestsellers", primary_token), 200, "DCS bestsellers")
    friend_recommendations = expect(*request("GET", "/recommendations/friends", primary_token), 200, "friend recommendations")
    book_recommendations = expect(*request("GET", "/recommendations/books/book-001", primary_token), 200, "book recommendations")
    if not bestsellers or not isinstance(friend_recommendations, list) or not isinstance(book_recommendations, list):
        raise AssertionError("one or more recommendation projections are empty or malformed")

    return {
        "passed": True,
        "mode": "apig",
        "checks": len(observations),
        "book_count": len(books),
        "category_count": len(categories),
        "architecture_book_count": len(architecture),
        "search_result_count": len(search),
        "bestseller_count": len(bestsellers),
        "friend_recommendation_count": len(friend_recommendations),
        "book_recommendation_count": len(book_recommendations),
        "order_id": first_order["id"],
        "request_statuses": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    result = run_local() if args.local else run_remote()
    output = ROOT / "verification-result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
