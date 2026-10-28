#!/usr/bin/env python3
"""Smart InstaBox watchdog: observe deadlines and renew only near idle pause."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
STATE = Path(__file__).resolve().parent / ".state" / "lease.json"
GUARD = max(600, int(os.getenv("INSTABOX_RENEW_GUARD_SECONDS", "1500")))
TERMINAL = {"deleted", "deleting", "error"}


class ApiError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f"HTTP {status}: {detail[:220]}")


def utcnow():
    return datetime.now(timezone.utc)


def iso_time(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def request(method, path, payload=None):
    if not KEY or not MACHINE:
        raise RuntimeError("Missing INSTABOX_API_KEY or INSTABOX_MACHINE_ID")
    headers = {"X-API-Key": KEY, "Accept": "application/json"}
    # InstaBox lifecycle endpoints require an idempotency key. Keep the same
    # value across retries for one logical operation.
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE") and (path.endswith("/start") or path.endswith("/resume") or path.endswith("/suspend") or path.endswith("/stop")):
        headers["Idempotency-Key"] = str(uuid.uuid4())
    raw = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        raw = json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(API + path, data=raw, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=18) as response:
                body = response.read(16384)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", "replace")
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ApiError(exc.code, detail) from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("Exhausted retries")


def machine_info():
    info = request("GET", f"/v1/machines/{MACHINE}")
    if isinstance(info, dict) and isinstance(info.get("machine"), dict):
        info = info["machine"]
    elif isinstance(info, dict) and isinstance(info.get("data"), dict):
        info = info["data"]
    if not isinstance(info, dict) or not isinstance(info.get("state"), str):
        raise RuntimeError("Machine response did not include a state")
    return info


def read_lease():
    try:
        info = json.loads(STATE.read_text(encoding="utf-8"))
        if info.get("machine_id") == MACHINE:
            return info
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        pass
    return {}


def write_lease(expires_at, started_at):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "machine_id": MACHINE,
        "expires_at": expires_at,
        "run_started_at": started_at,
    }
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    temp.replace(STATE)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write("renewed=true\n")


def seconds_left(target):
    return int((target - utcnow()).total_seconds())


def show_max_runtime(info):
    limit = info.get("maxRunMinutes")
    started = iso_time(info.get("runStartedAt"))
    if isinstance(limit, int) and limit > 0 and started:
        ends = started + timedelta(minutes=limit)
        print(f"Maximum continuous runtime deadline (UTC): {ends.isoformat()}")
        print(f"Remaining until forced pause: {seconds_left(ends)} seconds")
        return seconds_left(ends)
    print("No finite max-run deadline reported (or start time unavailable).")
    return None


def ensure_running(info):
    current = info["state"].lower()
    print(f"Current machine state: {current}")
    deadline = iso_time(info.get("cooldownUntil"))
    if current in TERMINAL:
        raise RuntimeError(f"Machine cannot recover from state {current}")
    if current == "running":
        return info

    if deadline and seconds_left(deadline) > 0:
        print(f"COOLDOWN_WAIT: {seconds_left(deadline)} seconds remain; wake blocked by InstaBox")
        return None

    if current == "suspended":
        print("Paused; issuing resume")
        try:
            request("POST", f"/v1/machines/{MACHINE}/resume", {})
        except ApiError as exc:
            if exc.status == 409:
                print("COOLDOWN_OR_STATE_CONFLICT: retry on next scheduled run")
                return None
            raise
    elif current == "stopped":
        print("Stopped; issuing start (this is a cold boot)")
        try:
            request("POST", f"/v1/machines/{MACHINE}/start", {})
        except ApiError as exc:
            if exc.status == 409:
                print("START_CONFLICT: retry on next scheduled run")
                return None
            raise
    elif current not in ("starting", "resuming", "suspending", "stopping"):
        raise RuntimeError(f"Unexpected machine state {current}")

    for _ in range(12):
        time.sleep(4)
        info = machine_info()
        if info["state"].lower() == "running":
            print("Machine is running again")
            return info
        deadline = iso_time(info.get("cooldownUntil"))
        if deadline and seconds_left(deadline) > 0:
            print("COOLDOWN_WAIT: next schedule will retry")
            return None
        if info["state"].lower() in TERMINAL:
            raise RuntimeError(f"Machine entered {info['state']}")
    print("STATE_PENDING: start/resume not finished; next schedule will check")
    return None


def smart_renew(info):
    run_remaining = show_max_runtime(info)
    if run_remaining is not None and run_remaining <= 120:
        print("FORCED_PAUSE_IMMINENT: lease cannot extend max runtime; waiting for cooldown")
        return

    previous = read_lease()
    previous_expiry = iso_time(previous.get("expires_at"))
    last_started = previous.get("run_started_at")
    started = info.get("runStartedAt")
    idle_seconds = info.get("autoSuspendAfterSeconds")
    if idle_seconds is None:
        print("Idle auto-pause disabled by plan: no activity lease required")
        return
    if not isinstance(idle_seconds, int) or idle_seconds < 0:
        raise RuntimeError("Invalid autoSuspendAfterSeconds from API")

    if previous_expiry and last_started == started:
        # InstaBox starts a fresh idle period only AFTER the activity lease expires.
        # This is a conservative estimate; real user activity can delay idle further.
        estimated_pause = previous_expiry + timedelta(seconds=idle_seconds)
        remain = seconds_left(estimated_pause)
        print(f"Last lease expires (UTC): {previous_expiry.isoformat()}")
        print(f"Conservative idle-pause deadline (UTC): {estimated_pause.isoformat()}")
        print(f"Remaining to estimated idle pause: {remain} seconds")
        if remain > GUARD:
            print(f"NOT_DUE: no renewal (safety window {GUARD}s)")
            return
    else:
        print("No current reliable lease record; renewing once now")

    result = request("PUT", f"/v1/machines/{MACHINE}/activity-leases/github-smart-watchdog", {"ttlSeconds": 3600})
    if not isinstance(result, dict):
        raise RuntimeError("Invalid lease response")
    expiry = iso_time(result.get("expiresAt"))
    if not expiry or seconds_left(expiry) < 300:
        raise RuntimeError("Lease returned an invalid or too-short expiresAt")
    write_lease(result["expiresAt"], started)
    print(f"LEASE_RENEWED until {expiry.isoformat()}, idle timer begins only after lease expiry")


def main():
    info = machine_info()
    info = ensure_running(info)
    if info is None:
        return
    smart_renew(info)
    print("WATCHDOG_OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"WATCHDOG_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
