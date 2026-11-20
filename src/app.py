"""Bookstore FunctionGraph entry point.

The file is intentionally self-contained because the Terraform example uploads
this file as inline FunctionGraph code.  The small adapter classes are also
usable by the local scripts and tests.
"""
from __future__ import annotations

# Capacity mode used when a table is created: on demand, no provisioned throughput.
CAPACITY_MODE = "PAY_PER_REQUEST"

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple


LOGGER = logging.getLogger("bookstore")
if not LOGGER.handlers:
    LOGGER.addHandler(logging.StreamHandler())
LOGGER.setLevel(logging.INFO)

NAMESPACE = os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_test_token(subject: str, ttl_seconds: int = 3600) -> str:
    """Issue a controlled HS256 token for local validation only."""
    secret = os.environ.get("BOOKSTORE_TEST_JWT_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        raise ValueError("BOOKSTORE_TEST_JWT_SECRET or JWT_SECRET is required")
    now = int(time.time())
    payload: Dict[str, Any] = {"sub": subject, "iat": now, "exp": now + ttl_seconds}
    issuer = os.environ.get("JWT_ISSUER", "")
    audience = os.environ.get("JWT_AUDIENCE", "")
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience
    header = {"alg": "HS256", "typ": "JWT"}
    signing = _b64u(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64u(
        json.dumps(payload, separators=(",", ":")).encode()
    )
    return signing + "." + _b64u(hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest())


def verify_jwt(token: str) -> Dict[str, Any]:
    secret = os.environ.get("JWT_SECRET") or os.environ.get("BOOKSTORE_TEST_JWT_SECRET")
    if not secret:
        raise PermissionError("JWT verification secret is not configured")
    parts = token.split(".")
    if len(parts) != 3:
        raise PermissionError("malformed JWT")
    signing = parts[0] + "." + parts[1]
    expected = _b64u(hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise PermissionError("invalid JWT signature")
    try:
        header = json.loads(_b64u_decode(parts[0]))
        claims = json.loads(_b64u_decode(parts[1]))
    except (ValueError, binascii.Error) as exc:
        raise PermissionError("invalid JWT encoding") from exc
    if header.get("alg") != "HS256" or not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise PermissionError("JWT must contain HS256 and a subject")
    now = int(time.time())
    if "exp" in claims and (not isinstance(claims["exp"], (int, float)) or now >= claims["exp"]):
        raise PermissionError("JWT expired")
    if "nbf" in claims and now < claims["nbf"]:
        raise PermissionError("JWT is not active")
    issuer = os.environ.get("JWT_ISSUER", "")
    audience = os.environ.get("JWT_AUDIENCE", "")
    if issuer and claims.get("iss") != issuer:
        raise PermissionError("JWT issuer mismatch")
    if audience and claims.get("aud") != audience:
        raise PermissionError("JWT audience mismatch")
    return claims


class MemoryStore:
    """Deterministic local backend with the same logical tables as GeminiDB."""

    table_names = (
        "books",
        "carts",
        "orders",
        "friends",
        "purchases_by_user",
        "purchases_by_book",
    )

    def __init__(self) -> None:
        self.books: Dict[str, Dict[str, Any]] = {}
        self.carts: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.friends: Dict[str, set[str]] = defaultdict(set)
        self.purchases_by_user: Dict[str, Counter[str]] = defaultdict(Counter)
        self.purchases_by_book: Dict[str, Counter[str]] = defaultdict(Counter)
        self.idempotency: Dict[Tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def put_book(self, book: Dict[str, Any]) -> None:
        item = dict(book)
        item["id"] = str(item["id"])
        item["price"] = float(item["price"])
        self.books[item["id"]] = item

    def get_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        book = self.books.get(str(book_id))
        return dict(book) if book else None

    def list_books(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        values = list(self.books.values())
        if category:
            values = [b for b in values if str(b.get("category", "")).lower() == category.lower()]
        return [dict(v) for v in sorted(values, key=lambda x: str(x.get("name", "")))]

    def categories(self) -> List[str]:
        return sorted({str(b.get("category", "")) for b in self.books.values() if b.get("category")})

    def get_cart(self, user_id: str) -> Dict[str, Any]:
        cart = self.carts.get(user_id, {"user_id": user_id, "items": []})
        return json.loads(json.dumps(cart))

    def put_cart(self, user_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        cart = {"user_id": user_id, "items": items, "updated_at": int(time.time())}
        self.carts[user_id] = json.loads(json.dumps(cart))
        return self.get_cart(user_id)

    def delete_cart(self, user_id: str) -> None:
        self.carts.pop(user_id, None)

    def put_order(self, order: Dict[str, Any]) -> None:
        self.orders[order["id"]] = json.loads(json.dumps(order))

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        value = self.orders.get(order_id)
        return json.loads(json.dumps(value)) if value else None

    def list_orders(self, user_id: str) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(x)) for x in self.orders.values() if x.get("user_id") == user_id]

    def put_friend(self, user_id: str, friend_id: str) -> None:
        self.friends[user_id].add(friend_id)

    def friend_ids(self, user_id: str) -> List[str]:
        return sorted(self.friends.get(user_id, set()))

    def add_purchase(self, user_id: str, book_id: str, quantity: int) -> None:
        self.purchases_by_user[user_id][book_id] += quantity
        self.purchases_by_book[book_id][user_id] += quantity

    def purchased_by_user(self, user_id: str) -> Dict[str, int]:
        return dict(self.purchases_by_user.get(user_id, {}))

    def purchasers_of_book(self, book_id: str) -> Dict[str, int]:
        return dict(self.purchases_by_book.get(book_id, {}))

    def idempotency_get(self, user_id: str, key: str) -> Optional[str]:
        return self.idempotency.get((user_id, key))

    def idempotency_put(self, user_id: str, key: str, order_id: str) -> None:
        self.idempotency[(user_id, key)] = order_id


class DynamoDBError(RuntimeError):
    """A safe, structured error returned by the DynamoDB-compatible endpoint."""

    def __init__(self, operation: str, status: int, code: str, message: str = "") -> None:
        self.operation = operation
        self.status = status
        self.code = code
        self.message = message
        detail = f" ({message})" if message else ""
        super().__init__(f"GeminiDB {operation} failed with HTTP {status}: {code}{detail}")


class GeminiStore:
    """DynamoDB JSON API adapter using SigV4 request signing and only the standard library."""

    table_definitions = {
        "books": {
            "key_schema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "attribute_definitions": [{"AttributeName": "id", "AttributeType": "S"}],
        },
        "carts": {
            "key_schema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "attribute_definitions": [{"AttributeName": "user_id", "AttributeType": "S"}],
        },
        "orders": {
            "key_schema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "attribute_definitions": [{"AttributeName": "id", "AttributeType": "S"}],
        },
        "friends": {
            "key_schema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "friend_id", "KeyType": "RANGE"},
            ],
            "attribute_definitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "friend_id", "AttributeType": "S"},
            ],
        },
        "purchases_by_user": {
            "key_schema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "book_id", "KeyType": "RANGE"},
            ],
            "attribute_definitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "book_id", "AttributeType": "S"},
            ],
        },
        "purchases_by_book": {
            "key_schema": [
                {"AttributeName": "book_id", "KeyType": "HASH"},
                {"AttributeName": "user_id", "KeyType": "RANGE"},
            ],
            "attribute_definitions": [
                {"AttributeName": "book_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
        },
    }

    def __init__(
        self,
        endpoint: str,
        namespace: str,
        access_key: str = "",
        secret_key: str = "",
        port: int = 8000,
        protocol: str = "http",
        region: str = "cn-north-4",
    ) -> None:
        if not endpoint:
            raise ValueError("GeminiDB endpoint is required")
        raw_endpoint = endpoint if "://" in endpoint else f"{protocol}://{endpoint}"
        parsed = urllib.parse.urlsplit(raw_endpoint)
        scheme = parsed.scheme.lower() or protocol.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("GeminiDB protocol must be http or https")
        hostname = parsed.hostname or ""
        if not hostname:
            raise ValueError("GeminiDB endpoint must contain a host")
        selected_port = parsed.port or int(port)
        if ":" in hostname and not hostname.startswith("["):
            host = f"[{hostname}]"
        else:
            host = hostname
        self.endpoint = f"{scheme}://{host}:{selected_port}{parsed.path.rstrip('/')}"
        self.host = f"{host}:{selected_port}"
        self.protocol = scheme
        self.port = selected_port
        self.region = region
        self.namespace = namespace
        self.access_key = access_key
        self.secret_key = secret_key
        self.table = {name: f"{namespace}_{name}" for name in MemoryStore.table_names}
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls, namespace: str) -> "GeminiStore":
        return cls(
            os.environ.get("GEMINI_ENDPOINT", ""),
            namespace,
            os.environ.get("GEMINI_ACCESS_KEY", ""),
            os.environ.get("GEMINI_SECRET_KEY", ""),
            int(os.environ.get("GEMINI_PORT", "8000")),
            os.environ.get("GEMINI_PROTOCOL", "http"),
            os.environ.get("GEMINI_REGION", os.environ.get("HW_REGION", "cn-north-4")),
        )

    @staticmethod
    def _av(value: Any) -> Dict[str, Any]:
        if value is None:
            return {"NULL": True}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, (int, float, Decimal)):
            return {"N": str(value)}
        if isinstance(value, str):
            return {"S": value}
        if isinstance(value, list):
            return {"L": [GeminiStore._av(v) for v in value]}
        if isinstance(value, dict):
            return {"M": {str(k): GeminiStore._av(v) for k, v in value.items()}}
        raise TypeError(f"unsupported DynamoDB value: {type(value)!r}")

    @classmethod
    def _plain(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if "S" in value:
                return value["S"]
            if "N" in value:
                number = Decimal(str(value["N"]))
                return int(number) if number == number.to_integral_value() else float(number)
            if "BOOL" in value:
                return bool(value["BOOL"])
            if "NULL" in value:
                return None
            if "L" in value:
                return [cls._plain(v) for v in value["L"]]
            if "M" in value:
                return {k: cls._plain(v) for k, v in value["M"].items()}
        return value

    @staticmethod
    def _signing_key(secret_key: str, date: str, region: str) -> bytes:
        def sign(key: bytes, value: str) -> bytes:
            return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

        key = sign(("AWS4" + secret_key).encode("utf-8"), date)
        key = sign(key, region)
        key = sign(key, "dynamodb")
        return sign(key, "aws4_request")

    def _signed_request(self, operation: str, body: bytes) -> urllib.request.Request:
        if not self.access_key or not self.secret_key:
            raise RuntimeError("GeminiDB access key and secret key are required")
        now = time.gmtime()
        date = time.strftime("%Y%m%d", now)
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", now)
        payload_hash = hashlib.sha256(body).hexdigest()
        path = urllib.parse.urlsplit(self.endpoint).path or "/"
        canonical_uri = urllib.parse.quote(path, safe="/-_.~")
        if not canonical_uri.startswith("/"):
            canonical_uri = "/" + canonical_uri
        headers = {
            "content-type": "application/x-amz-json-1.0",
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "x-amz-target": f"DynamoDB_20120810.{operation}",
        }
        canonical_headers = "".join(f"{key}:{' '.join(str(headers[key]).strip().split())}\n" for key in sorted(headers))
        signed_headers = ";".join(sorted(headers))
        canonical_request = "\n".join(["POST", canonical_uri, "", canonical_headers, signed_headers, payload_hash])
        scope = f"{date}/{self.region}/dynamodb/aws4_request"
        string_to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
        )
        signature = hmac.new(self._signing_key(self.secret_key, date, self.region), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        request_headers = {
            "Content-Type": headers["content-type"],
            "Host": headers["host"],
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": headers["x-amz-target"],
            "Authorization": authorization,
        }
        return urllib.request.Request(self.endpoint, data=body, headers=request_headers, method="POST")

    def _call(self, operation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = self._signed_request(operation, body)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise RuntimeError("GeminiDB returned a non-object response")
                return result
        except urllib.error.HTTPError as exc:
            raw = exc.read(1024).decode("utf-8", "replace")
            code = "HTTPError"
            message = ""
            try:
                error = json.loads(raw)
                code = str(error.get("__type") or error.get("code") or code).split("#")[-1]
                message = str(error.get("message") or "")
            except (TypeError, ValueError):
                pass
            raise DynamoDBError(operation, exc.code, code, message) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GeminiDB {operation} network failure: {exc.reason}") from exc

    def _physical_table(self, table: str) -> str:
        try:
            return self.table[table]
        except KeyError as exc:
            raise KeyError(f"unknown GeminiDB table: {table}") from exc

    def create_table(self, table: str) -> Dict[str, Any]:
        definition = self.table_definitions[table]
        return self._call(
            "CreateTable",
            {
                "TableName": self._physical_table(table),
                "KeySchema": definition["key_schema"],
                "AttributeDefinitions": definition["attribute_definitions"],
                "BillingMode": CAPACITY_MODE,
            },
        )

    def describe_table(self, table: str) -> Dict[str, Any]:
        return self._call("DescribeTable", {"TableName": self._physical_table(table)}).get("Table", {})

    def ensure_tables(self, timeout: float = 180.0, poll_interval: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        for table in self.table_definitions:
            try:
                self.create_table(table)
            except DynamoDBError as exc:
                if exc.code not in {"ResourceInUseException", "TableAlreadyExistsException"}:
                    raise
            while True:
                try:
                    description = self.describe_table(table)
                except DynamoDBError as exc:
                    if exc.code != "ResourceNotFoundException" or time.monotonic() >= deadline:
                        raise
                    time.sleep(poll_interval)
                    continue
                status = str(description.get("TableStatus", ""))
                if status == "ACTIVE":
                    break
                if status in {"DELETING", "ARCHIVING", "ARCHIVED"}:
                    raise RuntimeError(f"GeminiDB table {table} is not usable: {status}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for GeminiDB table {table} to become ACTIVE")
                time.sleep(poll_interval)

    def put_item(
        self,
        table: str,
        item: Dict[str, Any],
        condition_expression: str = "",
        expression_attribute_values: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"TableName": self._physical_table(table), "Item": {k: self._av(v) for k, v in item.items()}}
        if condition_expression:
            payload["ConditionExpression"] = condition_expression
        if expression_attribute_values:
            payload["ExpressionAttributeValues"] = {k: self._av(v) for k, v in expression_attribute_values.items()}
        return self._call("PutItem", payload)

    def update_item(
        self,
        table: str,
        key: Dict[str, Any],
        update_expression: str,
        expression_attribute_values: Optional[Dict[str, Any]] = None,
        expression_attribute_names: Optional[Dict[str, str]] = None,
        condition_expression: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "TableName": self._physical_table(table),
            "Key": {k: self._av(v) for k, v in key.items()},
            "UpdateExpression": update_expression,
        }
        if expression_attribute_values:
            payload["ExpressionAttributeValues"] = {k: self._av(v) for k, v in expression_attribute_values.items()}
        if expression_attribute_names:
            payload["ExpressionAttributeNames"] = expression_attribute_names
        if condition_expression:
            payload["ConditionExpression"] = condition_expression
        return self._call("UpdateItem", payload)

    def delete_item(self, table: str, key: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("DeleteItem", {"TableName": self._physical_table(table), "Key": {k: self._av(v) for k, v in key.items()}})

    def get_item(self, table: str, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        result = self._call("GetItem", {"TableName": self._physical_table(table), "Key": {k: self._av(v) for k, v in key.items()}})
        raw = result.get("Item")
        return {k: self._plain(v) for k, v in raw.items()} if raw else None

    def scan_items(self, table: str) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"TableName": self._physical_table(table)}
        items: List[Dict[str, Any]] = []
        while True:
            result = self._call("Scan", payload)
            items.extend(result.get("Items", []))
            last_key = result.get("LastEvaluatedKey")
            if not last_key:
                break
            payload["ExclusiveStartKey"] = last_key
        return [{k: self._plain(v) for k, v in item.items()} for item in items]

    def query_items(self, table: str, key_name: str, key_value: Any) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "TableName": self._physical_table(table),
            "KeyConditionExpression": f"{key_name} = :value",
            "ExpressionAttributeValues": {":value": self._av(key_value)},
        }
        items: List[Dict[str, Any]] = []
        while True:
            result = self._call("Query", payload)
            items.extend(result.get("Items", []))
            last_key = result.get("LastEvaluatedKey")
            if not last_key:
                break
            payload["ExclusiveStartKey"] = last_key
        return [{k: self._plain(v) for k, v in item.items()} for item in items]

    def batch_write_item(self, request_items: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        wire: Dict[str, List[Dict[str, Any]]] = {}
        for logical_table, requests in request_items.items():
            converted: List[Dict[str, Any]] = []
            for request in requests:
                if "PutRequest" in request:
                    converted.append({"PutRequest": {"Item": {k: self._av(v) for k, v in request["PutRequest"]["Item"].items()}}})
                elif "DeleteRequest" in request:
                    converted.append({"DeleteRequest": {"Key": {k: self._av(v) for k, v in request["DeleteRequest"]["Key"].items()}}})
                else:
                    raise ValueError("BatchWriteItem requests must contain PutRequest or DeleteRequest")
            wire[self._physical_table(logical_table)] = converted
        response = self._call("BatchWriteItem", {"RequestItems": wire})
        attempts = 0
        while response.get("UnprocessedItems") and attempts < 8:
            time.sleep(min(2.0, 0.1 * (2**attempts)))
            response = self._call("BatchWriteItem", {"RequestItems": response["UnprocessedItems"]})
            attempts += 1
        if response.get("UnprocessedItems"):
            raise RuntimeError("GeminiDB BatchWriteItem left unprocessed items after retries")
        return response

    def clear_namespace(self) -> int:
        key_fields = {"books": ("id",), "carts": ("user_id",), "orders": ("id",), "friends": ("user_id", "friend_id"), "purchases_by_user": ("user_id", "book_id"), "purchases_by_book": ("book_id", "user_id")}
        deleted = 0
        for table, fields in key_fields.items():
            items = self.scan_items(table)
            for start in range(0, len(items), 25):
                requests = [{"DeleteRequest": {"Key": {field: item[field] for field in fields}}} for item in items[start : start + 25]]
                if requests:
                    self.batch_write_item({table: requests})
                    deleted += len(requests)
        return deleted


class CSSSearch:
    def __init__(self, endpoint: str = "", port: int = 443, tls: bool = True, username: str = "", password: str = "", token: str = "", index: str = "bookstore-books", verify_tls: bool = True) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.port = int(port)
        self.tls = tls
        self.verify_tls = verify_tls
        self.username = username
        self.password = password
        self.token = token
        self.index = index
        self._index_ready = False

    def _url(self, path: str) -> str:
        if self.endpoint.startswith("http://") or self.endpoint.startswith("https://"):
            return self.endpoint + path
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.endpoint}:{self.port}{path}"

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self._url(path), data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
        if self.username or self.password:
            value = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {value}")
        if self.tls:
            context = ssl.create_default_context() if self.verify_tls else ssl._create_unverified_context()
        else:
            context = None
        try:
            with urllib.request.urlopen(request, timeout=10, context=context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", "replace")
            raise RuntimeError(f"CSS request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CSS network failure: {exc.reason}") from exc

    def ensure_index(self) -> None:
        if not self.endpoint or self._index_ready:
            return
        payload = {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {"properties": {"name": {"type": "text"}, "category": {"type": "keyword"}, "style": {"type": "keyword"}, "description": {"type": "text"}}},
        }
        try:
            self._request("PUT", f"/{urllib.parse.quote(self.index)}", payload)
        except RuntimeError as exc:
            detail = str(exc).lower()
            if "http 409" not in detail and "already_exists" not in detail and "already exists" not in detail:
                raise
        self._index_ready = True

    def index_book(self, book: Dict[str, Any]) -> None:
        if not self.endpoint:
            return
        self.ensure_index()
        self._request("PUT", f"/{urllib.parse.quote(self.index)}/_doc/{urllib.parse.quote(str(book['id']))}", book)

    def delete_index(self) -> None:
        if not self.endpoint:
            return
        try:
            self._request("DELETE", f"/{urllib.parse.quote(self.index)}")
        except RuntimeError as exc:
            if "http 404" not in str(exc).lower() and "not_found" not in str(exc).lower():
                raise
        self._index_ready = False

    def search(self, term: str, size: int = 20, local_books: Optional[Iterable[Dict[str, Any]]] = None) -> List[str]:
        term = term.strip().lower()
        if not term:
            return []
        if not self.endpoint:
            found = []
            for book in local_books or []:
                haystack = " ".join(str(book.get(k, "")) for k in ("name", "category", "description", "style")).lower()
                if term in haystack:
                    found.append(str(book["id"]))
            return found[:size]
        result = self._request(
            "POST",
            f"/{urllib.parse.quote(self.index)}/_search",
            {"size": size, "query": {"multi_match": {"query": term, "fields": ["name", "category", "style", "description"]}}},
        )
        return [str(hit.get("_id")) for hit in result.get("hits", {}).get("hits", []) if hit.get("_id")]


class DCSRanking:
    """Minimal Redis RESP client; it avoids requiring redis-py in FunctionGraph."""

    def __init__(self, endpoint: str = "", port: int = 6379, tls: bool = True, password: str = "", namespace: str = NAMESPACE) -> None:
        parsed = urllib.parse.urlsplit(endpoint if "://" in endpoint else f"redis://{endpoint}") if endpoint else None
        self.endpoint = parsed.hostname if parsed else ""
        self.port = int(parsed.port or port) if parsed else int(port)
        self.tls = tls
        self.password = password
        self.key = f"{namespace}:bestsellers"
        self.local_scores: Counter[str] = Counter()

    @staticmethod
    def _command(*parts: Any) -> bytes:
        encoded = [str(p).encode() for p in parts]
        return b"*" + str(len(encoded)).encode() + b"\r\n" + b"".join(b"$" + str(len(p)).encode() + b"\r\n" + p + b"\r\n" for p in encoded)

    @staticmethod
    def _readline(sock: Any) -> bytes:
        data = b""
        while not data.endswith(b"\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise RuntimeError("DCS closed the connection")
            data += chunk
        return data[:-2]

    def _read_response(self, sock: Any) -> Any:
        line = self._readline(sock)
        prefix, value = line[:1], line[1:]
        if prefix == b"-":
            raise RuntimeError(f"DCS error: {value.decode('utf-8', 'replace')}")
        if prefix == b":":
            return int(value)
        if prefix == b"$":
            length = int(value)
            if length < 0:
                return None
            data = b""
            while len(data) < length + 2:
                data += sock.recv(length + 2 - len(data))
            return data[:length].decode("utf-8", "replace")
        if prefix == b"*":
            count = int(value)
            return [self._read_response(sock) for _ in range(count)]
        return value.decode("utf-8", "replace")

    def _redis(self, *parts: Any) -> Any:
        raw = socket.create_connection((self.endpoint, self.port), timeout=8)
        sock: Any = raw
        if self.tls:
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.endpoint)
        try:
            if self.password and str(parts[0]).upper() != "AUTH":
                sock.sendall(self._command("AUTH", self.password))
                self._read_response(sock)
            sock.sendall(self._command(*parts))
            return self._read_response(sock)
        finally:
            sock.close()

    def increment(self, book_id: str, quantity: int) -> None:
        if not self.endpoint:
            self.local_scores[str(book_id)] += quantity
            return
        self._redis("ZINCRBY", self.key, quantity, book_id)

    def top(self, limit: int = 10) -> List[Tuple[str, float]]:
        if not self.endpoint:
            return [(key, float(value)) for key, value in self.local_scores.most_common(limit)]
        values = self._redis("ZREVRANGE", self.key, 0, max(0, limit - 1), "WITHSCORES") or []
        return [(values[i], float(values[i + 1])) for i in range(0, len(values) - 1, 2)]

    def clear(self) -> None:
        if not self.endpoint:
            self.local_scores.clear()
            return
        self._redis("DEL", self.key)


class BookstoreService:
    def __init__(self, store: Any, search: CSSSearch, ranking: DCSRanking) -> None:
        self.store = store
        self.search = search
        self.ranking = ranking
        self.lock = getattr(store, "_lock", threading.RLock())

    def books(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.store.list_books(category)

    def bootstrap(self, payload: Dict[str, Any]) -> Dict[str, int]:
        """Initialize this namespace from a one-off, IAM-authenticated invocation."""
        books = payload.get("books")
        friends = payload.get("friends", [])
        purchases = payload.get("purchases", [])
        if not isinstance(books, list) or not isinstance(friends, list) or not isinstance(purchases, list):
            raise ValueError("books, friends and purchases must be lists")
        with self.lock:
            if hasattr(self.store, "ensure_tables"):
                self.store.ensure_tables()
            if hasattr(self.store, "delete_namespace"):
                self.store.delete_namespace()
            self.search.delete_index()
            self.search.ensure_index()
            self.ranking.clear()
            for book in books:
                self.store.put_book(book)
                self.search.index_book(book)
            for relation in friends:
                self.store.put_friend(str(relation["user_id"]), str(relation["friend_id"]))
            for purchase in purchases:
                quantity = int(purchase["quantity"])
                self.store.add_purchase(str(purchase["user_id"]), str(purchase["book_id"]), quantity)
                self.ranking.increment(str(purchase["book_id"]), quantity)
        return {"books": len(books), "friends": len(friends), "purchases": len(purchases)}

    def book(self, book_id: str) -> Dict[str, Any]:
        value = self.store.get_book(book_id)
        if not value:
            raise KeyError("book not found")
        return value

    def cart(self, user_id: str) -> Dict[str, Any]:
        return self.store.get_cart(user_id)

    def change_cart(self, user_id: str, book_id: str, quantity: int) -> Dict[str, Any]:
        if quantity < 0 or quantity > 100:
            raise ValueError("quantity must be between 0 and 100")
        self.book(book_id)
        cart = self.store.get_cart(user_id)
        items = {str(x["book_id"]): int(x["quantity"]) for x in cart.get("items", [])}
        if quantity == 0:
            items.pop(str(book_id), None)
        else:
            items[str(book_id)] = quantity
        return self.store.put_cart(user_id, [{"book_id": key, "quantity": value} for key, value in sorted(items.items())])

    def replace_cart(self, user_id: str, supplied: List[Dict[str, Any]]) -> Dict[str, Any]:
        items = []
        for item in supplied:
            book_id = str(item.get("book_id", ""))
            quantity = int(item.get("quantity", 0))
            if quantity > 0:
                self.book(book_id)
                items.append({"book_id": book_id, "quantity": quantity})
        return self.store.put_cart(user_id, items)

    def checkout(self, user_id: str, request_body: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("Idempotency-Key header or idempotency_key body is required")
        with self.lock:
            previous = self.store.idempotency_get(user_id, idempotency_key)
            if previous:
                existing = self.store.get_order(previous)
                if existing:
                    return existing
            raw_items = request_body.get("items") or self.store.get_cart(user_id).get("items", [])
            if not isinstance(raw_items, list) or not raw_items:
                raise ValueError("cart is empty")
            lines: List[Dict[str, Any]] = []
            total = Decimal("0")
            for raw in raw_items:
                book_id = str(raw.get("book_id", ""))
                quantity = int(raw.get("quantity", 0))
                if quantity < 1 or quantity > 100:
                    raise ValueError("quantity must be between 1 and 100")
                book = self.book(book_id)
                price = Decimal(str(book["price"]))
                line_total = price * quantity
                total += line_total
                lines.append({"book_id": book_id, "name": book.get("name", ""), "quantity": quantity, "unit_price": float(price), "line_total": float(line_total)})
            order = {"id": str(uuid.uuid4()), "user_id": user_id, "items": lines, "total": float(total), "currency": "CNY", "status": "PAID_SIMULATED", "created_at": int(time.time()), "idempotency_key": idempotency_key}
            self.store.put_order(order)
            self.store.idempotency_put(user_id, idempotency_key, order["id"])
            for line in lines:
                self.store.add_purchase(user_id, line["book_id"], line["quantity"])
                self.ranking.increment(line["book_id"], line["quantity"])
            self.store.delete_cart(user_id)
            return order

    def recommendations_for_friends(self, user_id: str, limit: int) -> List[Dict[str, Any]]:
        already = set(self.store.purchased_by_user(user_id))
        counts: Counter[str] = Counter()
        for friend in self.store.friend_ids(user_id):
            for book_id, quantity in self.store.purchased_by_user(friend).items():
                if book_id not in already:
                    counts[book_id] += quantity
        return self._hydrate_counts(counts, limit)

    def recommendations_for_book(self, book_id: str, limit: int) -> List[Dict[str, Any]]:
        self.book(book_id)
        counts: Counter[str] = Counter()
        for user_id in self.store.purchasers_of_book(book_id):
            for other_id, quantity in self.store.purchased_by_user(user_id).items():
                if other_id != book_id:
                    counts[other_id] += quantity
        return self._hydrate_counts(counts, limit)

    def _hydrate_counts(self, counts: Counter[str], limit: int) -> List[Dict[str, Any]]:
        result = []
        for book_id, score in counts.most_common(limit):
            item = self.store.get_book(book_id)
            if item:
                item["recommendation_score"] = score
                result.append(item)
        return result


def _runtime() -> Tuple[BookstoreService, Any]:
    mode = os.environ.get("BOOKSTORE_MODE", "").strip().lower()
    if not mode:
        mode = "local" if _env_bool("BOOKSTORE_LOCAL", True) else "cloud"
    if mode == "local":
        store: Any = _LOCAL_STORE
    elif mode == "cloud":
        store = GeminiAdapter.from_env()
    else:
        raise RuntimeError("BOOKSTORE_MODE must be local or cloud")
    search = CSSSearch(
        endpoint=os.environ.get("CSS_ENDPOINT", ""),
        port=int(os.environ.get("CSS_PORT", "443")),
        tls=_env_bool("CSS_TLS", True),
        username=os.environ.get("CSS_USERNAME", ""),
        password=os.environ.get("CSS_PASSWORD", ""),
        token=os.environ.get("CSS_TOKEN", ""),
        index=os.environ.get("CSS_INDEX", "bookstore-books"),
        verify_tls=_env_bool("CSS_TLS_VERIFY", True),
    )
    ranking = DCSRanking(
        endpoint=os.environ.get("DCS_ENDPOINT", ""),
        port=int(os.environ.get("DCS_PORT", "6379")),
        tls=_env_bool("DCS_TLS", True),
        password=os.environ.get("DCS_PASSWORD", ""),
        namespace=NAMESPACE,
    )
    return BookstoreService(store, search, ranking), store


class GeminiAdapter:
    """Logical store facade backed by the six namespaced GeminiDB tables."""

    def __init__(self, endpoint: str = "", bootstrap: bool = False) -> None:
        self.db = GeminiStore(
            endpoint or os.environ.get("GEMINI_ENDPOINT", ""),
            NAMESPACE,
            os.environ.get("GEMINI_ACCESS_KEY", ""),
            os.environ.get("GEMINI_SECRET_KEY", ""),
            int(os.environ.get("GEMINI_PORT", "8000")),
            os.environ.get("GEMINI_PROTOCOL", "http"),
            os.environ.get("GEMINI_REGION", os.environ.get("HW_REGION", "cn-north-4")),
        )
        if bootstrap:
            self.db.ensure_tables()
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls, bootstrap: bool = False) -> "GeminiAdapter":
        return cls(os.environ.get("GEMINI_ENDPOINT", ""), bootstrap=bootstrap)

    def ensure_tables(self) -> None:
        self.db.ensure_tables()

    def _put(self, table: str, item: Dict[str, Any]) -> None:
        self.db.put_item(table, item)

    def _scan(self, table: str) -> List[Dict[str, Any]]:
        return self.db.scan_items(table)

    def list_books(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        values = self._scan("books")
        if category:
            values = [x for x in values if str(x.get("category", "")).lower() == category.lower()]
        return sorted(values, key=lambda x: str(x.get("name", "")))

    def get_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_item("books", {"id": str(book_id)})

    def put_book(self, book: Dict[str, Any]) -> None:
        self._put("books", book)

    def get_cart(self, user_id: str) -> Dict[str, Any]:
        return self.db.get_item("carts", {"user_id": user_id}) or {"user_id": user_id, "items": []}

    def put_cart(self, user_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        value = {"user_id": user_id, "items": items, "updated_at": int(time.time())}
        self._put("carts", value)
        return value

    def delete_cart(self, user_id: str) -> None:
        self.db.delete_item("carts", {"user_id": user_id})

    def put_order(self, order: Dict[str, Any]) -> None:
        self._put("orders", order)

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_item("orders", {"id": order_id})

    def list_orders(self, user_id: str) -> List[Dict[str, Any]]:
        return sorted([x for x in self._scan("orders") if x.get("user_id") == user_id], key=lambda x: x.get("created_at", 0), reverse=True)

    def put_friend(self, user_id: str, friend_id: str) -> None:
        self._put("friends", {"user_id": user_id, "friend_id": friend_id})

    def friend_ids(self, user_id: str) -> List[str]:
        return sorted({x["friend_id"] for x in self.db.query_items("friends", "user_id", user_id) if x.get("friend_id")})

    def add_purchase(self, user_id: str, book_id: str, quantity: int) -> None:
        # GeminiDB's DynamoDB-compatible parser does not support if_not_exists
        # within arithmetic update expressions, so use compatible full-item writes.
        for table, key in (
            ("purchases_by_user", {"user_id": user_id, "book_id": book_id}),
            ("purchases_by_book", {"book_id": book_id, "user_id": user_id}),
        ):
            current = self.db.get_item(table, key) or key
            current["quantity"] = int(current.get("quantity", 0)) + quantity
            self._put(table, current)

    def purchased_by_user(self, user_id: str) -> Dict[str, int]:
        return {str(x["book_id"]): int(x.get("quantity", 0)) for x in self.db.query_items("purchases_by_user", "user_id", user_id)}

    def purchasers_of_book(self, book_id: str) -> Dict[str, int]:
        return {str(x["user_id"]): int(x.get("quantity", 0)) for x in self.db.query_items("purchases_by_book", "book_id", book_id)}

    def idempotency_get(self, user_id: str, key: str) -> Optional[str]:
        for order in self.list_orders(user_id):
            if order.get("idempotency_key") == key:
                return str(order["id"])
        return None

    def idempotency_put(self, user_id: str, key: str, order_id: str) -> None:
        # The idempotency key is retained in the order item and checked before every write.
        return None

    def delete_namespace(self) -> int:
        return self.db.clear_namespace()


_LOCAL_STORE = MemoryStore()
try:
    SERVICE, ACTIVE_STORE = _runtime()
except Exception as _runtime_error:
    if os.environ.get("BOOKSTORE_MODE", "").strip().lower() == "cloud":
        raise
    LOGGER.warning("bookstore runtime initialization deferred: %s", _runtime_error)
    SERVICE, ACTIVE_STORE = BookstoreService(_LOCAL_STORE, CSSSearch(), DCSRanking()), _LOCAL_STORE


def _json_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if body is None or body == "":
        return {}
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, dict):
        return body
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a JSON object")
    return parsed


def _headers(event: Dict[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}


def _response(status: int, payload: Any, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization,Content-Type,Idempotency-Key", "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "isBase64Encoded": False, "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def _subject(event: Dict[str, Any], required: bool = True) -> Optional[str]:
    headers = _headers(event)
    value = headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value:
        context = event.get("requestContext") or {}
        authorizer = context.get("authorizer") or {}
        value = authorizer.get("sub") or authorizer.get("subject") or ""
        if isinstance(value, dict):
            value = value.get("sub", "")
    if value and "." in value:
        return verify_jwt(value)["sub"]
    if value and not required:
        return value
    if required:
        raise PermissionError("Bearer JWT is required")
    return None


def _path(event: Dict[str, Any]) -> str:
    value = str(event.get("path") or event.get("rawPath") or "/")
    prefix = "/bookstore/v1"
    if value.startswith(prefix):
        value = value[len(prefix):]
    return value or "/"


def _query(event: Dict[str, Any]) -> Dict[str, str]:
    values = event.get("queryStringParameters") or {}
    return {str(k): str(v) for k, v in values.items() if v is not None}


def _limit(query: Dict[str, str], default: int = 10) -> int:
    try:
        return max(1, min(50, int(query.get("limit", default))))
    except ValueError:
        raise ValueError("limit must be an integer")


def route(event: Dict[str, Any]) -> Dict[str, Any]:
    method = str(event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = _path(event)
    query = _query(event)
    if method == "OPTIONS":
        return _response(204, {})
    body = _json_body(event) if method in {"POST", "PUT", "PATCH"} else {}

    if path == "/bootstrap" and method == "POST":
        bootstrap_subject = _subject(event, required=True)
        if not bootstrap_subject.startswith("bootstrap-"):
            raise PermissionError("bootstrap subject is not allowed")
        return _response(200, SERVICE.bootstrap(body))

    if path == "/books" and method == "GET":
        return _response(200, SERVICE.books(query.get("category")))
    match = re.fullmatch(r"/books/([^/]+)", path)
    if match and method == "GET":
        return _response(200, SERVICE.book(urllib.parse.unquote(match.group(1))))
    if path == "/categories" and method == "GET":
        return _response(200, [{"name": name} for name in SERVICE.store.categories()] if hasattr(SERVICE.store, "categories") else [{"name": x.get("category")} for x in SERVICE.books()])
    match = re.fullmatch(r"/categories/([^/]+)", path)
    if match and method == "GET":
        category = urllib.parse.unquote(match.group(1))
        return _response(200, SERVICE.books(category))
    if path == "/search" and method == "GET":
        term = query.get("q") or query.get("searchTerm")
        if not term:
            raise ValueError("q is required")
        ids = SERVICE.search.search(term, _limit(query), SERVICE.books())
        return _response(200, [SERVICE.book(book_id) for book_id in ids if SERVICE.store.get_book(book_id)])
    if path == "/bestsellers" and method == "GET":
        return _response(200, [dict(SERVICE.book(book_id), score=score) for book_id, score in SERVICE.ranking.top(_limit(query)) if SERVICE.store.get_book(book_id)])

    user_id = _subject(event, required=True)
    if path == "/cart" and method == "GET":
        return _response(200, SERVICE.cart(user_id))
    if path == "/cart" and method in {"POST", "PUT"}:
        items = body.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        return _response(200, SERVICE.replace_cart(user_id, items))
    match = re.fullmatch(r"/cart/([^/]+)", path)
    if match and method in {"PUT", "DELETE"}:
        book_id = urllib.parse.unquote(match.group(1))
        quantity = 0 if method == "DELETE" else int(body.get("quantity", body.get("qty", 0)))
        return _response(200, SERVICE.change_cart(user_id, book_id, quantity))
    if path == "/checkout" and method == "POST":
        headers = _headers(event)
        key = headers.get("idempotency-key") or str(body.get("idempotency_key", ""))
        return _response(201, SERVICE.checkout(user_id, body, key))
    if path == "/orders" and method == "GET":
        return _response(200, SERVICE.store.list_orders(user_id))
    match = re.fullmatch(r"/orders/([^/]+)", path)
    if match and method == "GET":
        order = SERVICE.store.get_order(urllib.parse.unquote(match.group(1)))
        if not order or order.get("user_id") != user_id:
            raise KeyError("order not found")
        return _response(200, order)
    if path == "/recommendations/friends" and method == "GET":
        return _response(200, SERVICE.recommendations_for_friends(user_id, _limit(query)))
    match = re.fullmatch(r"/recommendations/books/([^/]+)", path)
    if match and method == "GET":
        return _response(200, SERVICE.recommendations_for_book(urllib.parse.unquote(match.group(1)), _limit(query)))
    raise KeyError("route not found")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    request_id = "unknown"
    try:
        getter = getattr(context, "getRequestID", None)
        if callable(getter):
            request_id = getter()
    except Exception:
        pass
    try:
        result = route(event)
        LOGGER.info("request_id=%s method=%s path=%s status=%s", request_id, event.get("httpMethod"), event.get("path"), result["statusCode"])
        return result
    except PermissionError as exc:
        LOGGER.info("request_id=%s auth_failure=%s", request_id, str(exc))
        return _response(401, {"error": "unauthorized"})
    except KeyError as exc:
        return _response(404, {"error": str(exc).strip("'")})
    except (ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        return _response(400, {"error": str(exc)})
    except Exception as exc:
        LOGGER.exception("request_id=%s bookstore failure", request_id)
        return _response(500, {"error": "internal_error", "request_id": request_id})

