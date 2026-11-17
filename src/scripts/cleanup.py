#!/usr/bin/env python3
"""Delete only this namespace's data and CSS index; never deletes service instances."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from app import CSSSearch, DCSRanking, GeminiAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-namespace", required=True)
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    namespace = os.environ.get("BOOKSTORE_NAMESPACE", "bookstore_local")
    if args.confirm_namespace != namespace:
        parser.error("confirmation must exactly equal BOOKSTORE_NAMESPACE")
    if args.local:
        print({"namespace": namespace, "deleted": "local process state only"})
        return 0
    if os.environ.get("BOOKSTORE_MODE", "").strip().lower() != "cloud":
        parser.error("BOOKSTORE_MODE=cloud is required unless --local is used")
    if not os.environ.get("GEMINI_ENDPOINT"):
        parser.error("GEMINI_ENDPOINT is required in cloud mode")
    store = GeminiAdapter.from_env()
    store.ensure_tables()
    deleted = store.delete_namespace()
    search = CSSSearch(
        os.environ.get("CSS_ENDPOINT", ""),
        int(os.environ.get("CSS_PORT", "443")),
        os.environ.get("CSS_TLS", "true").lower() == "true",
        os.environ.get("CSS_USERNAME", ""),
        os.environ.get("CSS_PASSWORD", ""),
        os.environ.get("CSS_TOKEN", ""),
        f"{namespace}-books",
    )
    search.delete_index()
    ranking = DCSRanking(
        os.environ.get("DCS_ENDPOINT", ""),
        int(os.environ.get("DCS_PORT", "6379")),
        os.environ.get("DCS_TLS", "true").lower() == "true",
        os.environ.get("DCS_PASSWORD", ""),
        namespace,
    )
    ranking.clear()
    print({"namespace": namespace, "deleted_geminidb_items": deleted, "deleted_css_index": bool(search.endpoint), "cleared_dcs_key": bool(ranking.endpoint)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
