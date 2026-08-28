"""Small shared helpers: HTTP, files, subprocess, output formatting."""

import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class XProtonError(Exception):
    """Fatal, user-facing error."""


# ---------------------------------------------------------------------------
# Output helpers (ASCII-safe on consoles that cannot print unicode)
# ---------------------------------------------------------------------------
def _encodable(s: str) -> bool:
    try:
        (s + "x").encode(sys.stdout.encoding or "utf-8")
        return True
    except UnicodeEncodeError:
        return False


_SYMS = {
    "ok": ("✓", "[ok]"),
    "warn": ("⚠", "[warn]"),
    "fail": ("✗", "[x]"),
    "arrow": ("→", "->"),
}


def sym(name: str) -> str:
    uni, ascii_fb = _SYMS.get(name, (name, name))
    return uni if _encodable(uni) else ascii_fb


def info(msg: str) -> None:
    print(msg)


def ok(msg: str) -> None:
    print(f"  {sym('ok')} {msg}")


def warn(msg: str) -> None:
    print(f"  {sym('warn')} {msg}")


def fail(msg: str) -> None:
    print(f"  {sym('fail')} {msg}")


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# base64 helpers
# ---------------------------------------------------------------------------
def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(data: str) -> bytes:
    return base64.b64decode(data)


# ---------------------------------------------------------------------------
# HTTP (stdlib only, with small retry policy)
# ---------------------------------------------------------------------------
def version_tuple(v):
    """Parse 'v1.2.3' / '1.2.3' into a comparable tuple of ints."""
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts) or (0,)


def http_json(
    method: str,
    url: str,
    body=None,
    headers=None,
    timeout: int = 30,
    retries: int = 2,
):
    """Perform a JSON HTTP request.

    Returns (status, headers, parsed_json_or_raw_bytes).
    Raises XProtonError on transport failure after retries.
    """
    hdrs = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = resp.status
                rheaders = dict(resp.headers.items())
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    parsed = None
                return status, rheaders, parsed if parsed is not None else raw
        except urllib.error.HTTPError as e:
            # HTTP errors still carry a JSON body worth returning.
            try:
                raw = e.read()
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = None
            return e.code, dict(e.headers.items()), parsed if parsed is not None else raw
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise XProtonError(f"network error talking to {url}: {last_err}")


def curl_json(
    method: str,
    url: str,
    body=None,
    headers=None,
    timeout: int = 60,
    proxy: str = None,
):
    """JSON HTTP request routed through an explicit proxy (curl binary).

    urllib has no SOCKS support, so account creation through socks4/socks5
    proxies shells out to curl. Returns (status, {}, parsed_json_or_raw).
    Raises XProtonError on transport failure.
    """
    cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method]
    if proxy:
        cmd += ["--proxy", proxy]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd += ["-w", "\n%{http_code}", url]
    rc, out, err = run(cmd, timeout=timeout + 15)
    if rc != 0:
        raise XProtonError(f"proxy request failed (rc={rc}): {err or out}")
    body_txt, _, status = out.rpartition("\n")
    try:
        parsed = json.loads(body_txt)
    except ValueError:
        parsed = body_txt
    try:
        status_code = int(status.strip() or 0)
    except ValueError:
        status_code = 0
    return status_code, {}, parsed


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def atomic_write(path: str, data: str, mode: int = 0o644) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-xproton-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write_json(path: str, obj, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n", mode=mode)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def file_exists(path: str) -> bool:
    return os.path.isfile(path)


def is_root() -> bool:
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------
def run(cmd, check=False, timeout=60):
    """Run a command; returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        rc, out, err = p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out: {' '.join(cmd)}"
    if check and rc != 0:
        raise XProtonError(
            f"command failed ({rc}): {' '.join(cmd)}\n{out}{err}"
        )
    return rc, out, err


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True
