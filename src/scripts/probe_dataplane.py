#!/usr/bin/env python3
"""Probe configured data-plane endpoints without printing credentials or payloads."""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))
from app import CSSSearch, DCSRanking, DynamoDBError, GeminiAdapter  # noqa: E402
from seed import load  # noqa: E402


def probe_http(name: str, endpoint: str) -> dict:
    if not endpoint:
        return {"name": name, "configured": False, "passed": False, "reason": "endpoint_not_configured"}
    parsed = urllib.parse.urlparse(endpoint if "://" in endpoint else "https://" + endpoint)
    host = parsed.hostname or ""
    result = {"name": name, "configured": True, "host": host, "passed": False}
    try:
        request = urllib.request.Request(endpoint if "://" in endpoint else "https://" + endpoint, method="HEAD")
        with urllib.request.urlopen(request, timeout=5) as response:
            result.update({"status": response.status, "passed": response.status < 500})
    except urllib.error.HTTPError as exc:
        result.update({"status": exc.code, "passed": exc.code not in {401, 403} and exc.code < 500, "reason": "http_error"})
    except Exception as exc:
        result["reason"] = type(exc).__name__
    return result


def probe_socket(name: str, endpoint: str, port: int, tls: bool) -> dict:
    if not endpoint:
        return {"name": name, "configured": False, "passed": False, "reason": "endpoint_not_configured"}
    host = endpoint.replace("https://", "").replace("http://", "").split("/", 1)[0].split(":", 1)[0]
    result = {"name": name, "configured": True, "host": host, "port": port, "passed": False}
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=5)
        if tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        result["passed"] = True
    except Exception as exc:
        result["reason"] = type(exc).__name__
    finally:
        if sock:
            sock.close()
    return result


def probe_gemini(seed_fixtures: bool = True) -> dict:
    endpoint = os.environ.get("GEMINI_ENDPOINT", "")
    if not endpoint:
        return {"name": "geminidb", "configured": False, "passed": False, "reason": "endpoint_not_configured"}
    parsed = urllib.parse.urlsplit(endpoint if "://" in endpoint else f"{os.environ.get('GEMINI_PROTOCOL', 'http')}://{endpoint}")
    result = {
        "name": "geminidb",
        "configured": True,
        "host": parsed.hostname or "",
        "port": int(parsed.port or os.environ.get("GEMINI_PORT", "8000")),
        "protocol": parsed.scheme,
        "region": os.environ.get("GEMINI_REGION", os.environ.get("HW_REGION", "cn-north-4")),
        "passed": False,
    }
    try:
        if os.environ.get("BOOKSTORE_MODE", "").strip().lower() != "cloud":
            result["reason"] = "BOOKSTORE_MODE_cloud_required"
            return result
        store = GeminiAdapter.from_env()
        store.ensure_tables()
        result["tables_active"] = len(store.db.table_definitions)
        if seed_fixtures:
            namespace = os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")
            search = CSSSearch(
                os.environ.get("CSS_ENDPOINT", ""),
                int(os.environ.get("CSS_PORT", "443")),
                os.environ.get("CSS_TLS", "true").lower() == "true",
                os.environ.get("CSS_USERNAME", ""),
                os.environ.get("CSS_PASSWORD", ""),
                os.environ.get("CSS_TOKEN", ""),
                f"{namespace}-books",
            )
            ranking = DCSRanking(
                os.environ.get("DCS_ENDPOINT", ""),
                int(os.environ.get("DCS_PORT", "6379")),
                os.environ.get("DCS_TLS", "true").lower() == "true",
                os.environ.get("DCS_PASSWORD", ""),
                namespace,
            )
            result["seed"] = load(store, search, ranking)
        result["passed"] = True
    except DynamoDBError as exc:
        result["reason"] = exc.code
    except Exception as exc:
        result["reason"] = type(exc).__name__
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "probe-result.json"))
    parser.add_argument("--no-seed", action="store_true", help="only bootstrap and describe tables")
    args = parser.parse_args()
    result = {
        "region": os.environ.get("GEMINI_REGION", os.environ.get("HW_REGION", "cn-north-4")),
        "geminidb": probe_gemini(not args.no_seed),
        "css": probe_http("css", os.environ.get("CSS_ENDPOINT", "")),
        "dcs": probe_socket("dcs", os.environ.get("DCS_ENDPOINT", ""), int(os.environ.get("DCS_PORT", "6379")), os.environ.get("DCS_TLS", "true").lower() == "true"),
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(not item["configured"] or item["passed"] for item in result.values() if isinstance(item, dict)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
