"""Health checks: tiny SOCKS5 client + exit-IP/latency/speed tests.

The SOCKS5 servers exposed by xProton (via sing-box) speak the standard
protocol; this client implements just what we need: no-auth CONNECT with
remote (proxy-side) hostname resolution, so DNS also goes through the tunnel.
"""

import socket
import ssl
import struct
import time

from .util import XProtonError

_RECV_CHUNK = 65536


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise XProtonError("connection closed during SOCKS handshake")
        buf += chunk
    return buf


def socks_connect(proxy_host: str, proxy_port: int, dst_host: str, dst_port: int,
                  timeout: float = 15.0) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(b"\x05\x01\x00")  # version 5, 1 method, no-auth
        ver, method = _recv_exact(sock, 2)
        if ver != 5 or method != 0:
            raise XProtonError("proxy refused no-auth SOCKS5")
        addr = dst_host.encode("idna")
        sock.sendall(
            b"\x05\x01\x00\x03" + bytes([len(addr)]) + addr + struct.pack(">H", dst_port)
        )
        ver, rep, _rsv, atyp = _recv_exact(sock, 4)
        if ver != 5:
            raise XProtonError("bad SOCKS5 reply version")
        if rep != 0:
            reasons = {
                1: "general failure",
                2: "not allowed",
                3: "network unreachable",
                4: "host unreachable",
                5: "connection refused",
                6: "TTL expired",
                7: "command not supported",
                8: "address type not supported",
            }
            raise XProtonError(f"SOCKS5 connect failed: {reasons.get(rep, rep)}")
        if atyp == 1:
            _recv_exact(sock, 4 + 2)
        elif atyp == 3:
            ln = _recv_exact(sock, 1)[0]
            _recv_exact(sock, ln + 2)
        elif atyp == 4:
            _recv_exact(sock, 16 + 2)
        return sock
    except Exception:
        sock.close()
        raise


def _https_over_socks(socks_port: int, host: str, path: str, timeout: float = 15.0):
    t0 = time.time()
    raw = socks_connect("127.0.0.1", socks_port, host, 443, timeout)
    ctx = ssl.create_default_context()
    try:
        tls = ctx.wrap_socket(raw, server_hostname=host)
        tls.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: xproton-health\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        chunks = []
        while True:
            chunk = tls.recv(_RECV_CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        body = b"".join(chunks)
        elapsed = time.time() - t0
        status = int(body.split(b" ", 2)[1]) if b" " in body[:32] else 0
        head, _, rest = body.partition(b"\r\n\r\n")
        return status, elapsed, rest
    finally:
        try:
            raw.close()
        except OSError:
            pass


def test_location(country: str, socks_port: int, timeout: float = 15.0) -> dict:
    """Latency via a 204 endpoint + exit IP via ipify (fallback ifconfig.me)."""
    result = {"ok": False, "latency_ms": None, "exit_ip": None, "error": None}
    try:
        status, elapsed, _ = _https_over_socks(
            socks_port, "www.gstatic.com", "/generate_204", timeout
        )
        if status == 204:
            result["latency_ms"] = int(elapsed * 1000)
        else:
            result["error"] = f"unexpected status {status} from gstatic 204"
    except Exception as e:
        result["error"] = str(e)
        return result
    for host, path in (("api.ipify.org", "/"), ("ifconfig.me", "/ip")):
        try:
            status, _elapsed, body = _https_over_socks(socks_port, host, path, timeout)
            text = body.decode("utf-8", "replace").strip()
            if status == 200 and text and "\n" not in text and len(text) <= 45:
                result["exit_ip"] = text
                break
        except Exception:
            continue
    result["ok"] = result["latency_ms"] is not None
    return result


def speed_location(country: str, socks_port: int, megabytes: int = 8,
                   timeout: float = 30.0) -> dict:
    """Download test via speed.cloudflare.com through the location's SOCKS5."""
    try:
        raw = socks_connect(
            "127.0.0.1", socks_port, "speed.cloudflare.com", 443, timeout
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname="speed.cloudflare.com")
        path = f"/__down?bytes={megabytes * 1024 * 1024}"
        tls.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: speed.cloudflare.com\r\n"
                "User-Agent: xproton-health\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        t0 = time.time()
        total = 0
        first_byte = None
        tls.settimeout(timeout)
        while total < megabytes * 1024 * 1024:
            chunk = tls.recv(_RECV_CHUNK)
            if not chunk:
                break
            if first_byte is None:
                first_byte = time.time()
            total += len(chunk)
            if time.time() - t0 > timeout:
                break
        elapsed = time.time() - t0
        mbps = (total * 8) / elapsed / 1e6 if elapsed > 0 else 0
        return {
            "ok": total > 0,
            "bytes": total,
            "seconds": round(elapsed, 2),
            "mbps": round(mbps, 1),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            raw.close()
        except OSError:
            pass
