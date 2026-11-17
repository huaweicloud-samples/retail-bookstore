#!/usr/bin/env python3
"""Upload frontend/dist to an existing OBS bucket using an OBS V2 signed request."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import mimetypes
import os
import pathlib
import urllib.error
import urllib.request


def sign(method: str, host: str, path: str, body: bytes, access_key: str, secret_key: str, token: str = "") -> urllib.request.Request:
    date = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    canonical = f"{method}\n{content_md5}\n{content_type}\n{date}\n/" + path.lstrip("/")
    signature = base64.b64encode(hmac.new(secret_key.encode(), canonical.encode(), hashlib.sha1).digest()).decode()
    headers = {"Host": host, "Date": date, "Content-MD5": content_md5, "Content-Type": content_type, "Authorization": f"OBS {access_key}:{signature}"}
    if token:
        headers["x-obs-security-token"] = token
    return urllib.request.Request(f"https://{host}/{path.lstrip('/')}", data=body, headers=headers, method=method)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="frontend/dist")
    parser.add_argument("--bucket", default=os.environ.get("OBS_BUCKET", ""))
    parser.add_argument("--prefix", default=os.environ.get("OBS_PREFIX", "bookstore/"))
    args = parser.parse_args()
    if not args.bucket:
        parser.error("--bucket or OBS_BUCKET is required")
    ak, sk = os.environ.get("OBS_ACCESS_KEY", ""), os.environ.get("OBS_SECRET_KEY", "")
    if not ak or not sk:
        parser.error("OBS_ACCESS_KEY and OBS_SECRET_KEY are required")
    host = os.environ.get("OBS_ENDPOINT_HOST", "obs.cn-north-4.myhuaweicloud.com")
    root = pathlib.Path(args.dist)
    if not root.is_dir():
        parser.error(f"dist directory does not exist: {root}")
    count = 0
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        key = args.prefix.rstrip("/") + "/" + file.relative_to(root).as_posix()
        request = sign("PUT", host, f"{args.bucket}/{key}", file.read_bytes(), ak, sk, os.environ.get("OBS_SECURITY_TOKEN", ""))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in {200, 201, 204}:
                    raise RuntimeError(f"OBS returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"OBS upload failed for {key}: HTTP {exc.code}")
        count += 1
    print(f"uploaded={count} bucket={args.bucket} prefix={args.prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
