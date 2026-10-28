#!/usr/bin/env python3
"""InstaBox machine watchdog. Only uses official API operations; no delete."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.instabox.sh"
MACHINE = os.environ.get("INSTABOX_MACHINE_ID", "").strip()
KEY = os.environ.get("INSTABOX_API_KEY", "").strip()
TERMINAL = {"deleted", "deleting", "error"}


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f"HTTP {status}: {detail[:300]}")


def request(method, path, data=None):
    if not KEY or not MACHINE:
        raise RuntimeError("Missing INSTABOX_API_KEY or INSTABOX_MACHINE_ID")
    headers = {"X-API-Key": KEY, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        headers["Idempotency-Key"] = str(uuid.uuid4())
    raw = json.dumps(data).encode("utf-8") if data is not None else None
    for attempt in range(3):
        try:
            req = urllib.request.Request(API + path, data=raw, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=20) as res:
                payload = res.read(16384)
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(1024).decode("utf-8", "replace")
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ApiError(exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Request failed after retries")


def state():
    response = request("GET", f"/v1/machines/{MACHINE}")
    node = response
    for _ in range(3):
        if not isinstance(node, dict):
            break
        child = node.get("machine") or node.get("data")
        if isinstance(child, dict):
            node = child
        else:
            break
    status = node.get("state") or node.get("status") if isinstance(node, dict) else None
    if not isinstance(status, str):
        raise RuntimeError("Could not read machine state from API")
    return status.lower()


def ensure_running():
    current = state()
    print(f"Machine state: {current}", flush=True)
    if current in TERMINAL:
        raise RuntimeError(f"Cannot recover machine in state {current}")
    if current == "suspended":
        print("Requesting resume")
        request("POST", f"/v1/machines/{MACHINE}/resume", {})
    elif current == "stopped":
        print("Requesting start after stop (this is a new boot)")
        request("POST", f"/v1/machines/{MACHINE}/start", {})
    elif current == "running":
        return
    elif current not in ("starting", "resuming", "suspending", "stopping"):
        raise RuntimeError(f"Unsupported machine state: {current}")

    for _ in range(12):
        time.sleep(4)
        current = state()
        if current == "running":
            print("Machine running")
            return
        if current in TERMINAL:
            raise RuntimeError(f"Machine entered state {current}")
    raise RuntimeError("Machine not running after 48 seconds")


def renew_activity():
    path = f"/v1/machines/{MACHINE}/activity-leases/keepalive"
    try:
        result = request("PUT", path, {"ttlSeconds": 3600})
        expiration = result.get("expiresAt", "not provided") if isinstance(result, dict) else "not provided"
        print(f"Activity lease renewed (up to 3600s). Expires: {expiration}")
    except ApiError as exc:
        if exc.status not in (404, 405):
            raise
        print("Activity lease unsupported; using a minimal exec activity check")
        request("POST", f"/v1/machines/{MACHINE}/exec", {"cmd": "true"})
        print("Activity check executed; idle prevention is NOT guaranteed.")


if __name__ == "__main__":
    try:
        ensure_running()
        renew_activity()
        print("WATCHDOG_OK")
    except Exception as exc:
        print(f"WATCHDOG_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
