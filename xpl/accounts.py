"""Unified account store (accounts.txt).

Every account — API-created or imported manually — lives in one file as a
two-line block:

    # manual account (real Proton credentials you own):
    email:password[:totp_secret][:COUNTRY]
    <session JSON (one line) or "-" when none is stored>

    # API-created account (no credentials — session-only):
    api:<label>[:COUNTRY]
    <session JSON (one line)>

* '#' comments and blank lines are ignored.
* The session line lets xProton reuse/refresh a session instead of logging
  in again (for API accounts it is the only way to use the account).
* Accounts with a COUNTRY field are pinned to that location; unpinned ones
  are assigned positionally to the remaining locations; anything left over
  forms the spare pool (used when an account gets banned).
"""

import json
import os
import re

from . import consts
from .util import XProtonError, atomic_write

_SESSION_RE = re.compile(r"^\s*\{.*\}\s*$", re.S)

HEADER = """\
# xProton accounts — one account per two-line block.
#
# Manual account (you created it at account.proton.me):
#   email:password[:totp_secret][:COUNTRY]
#   <session JSON, one line, or "-">
#
# API-created account (no credentials returned by the backend):
#   api:<label>[:COUNTRY]
#   <session JSON, one line>
#
# COUNTRY pins the account to one of: {countries}
# Lines starting with '#' and blank lines are ignored.
# After editing this file, run:  xproton verify
""".format(countries=", ".join(consts.LOCATION_COUNTRIES))


def _is_session_line(line: str) -> bool:
    s = line.strip()
    return s == "-" or bool(_SESSION_RE.match(s))


def parse_accounts(path=None) -> list:
    path = path or consts.ACCOUNTS_FILE
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        lineno = i + 1
        i += 1
        if not line or line.startswith("#"):
            continue
        if _is_session_line(line):
            raise XProtonError(
                f"{path}:{lineno}: session line without a preceding account line"
            )
        session = None
        session_line = None
        if i < len(lines) and _is_session_line(lines[i].strip()):
            session_line = i + 1
            nxt = lines[i].strip()
            i += 1
            if nxt != "-":
                try:
                    session = json.loads(nxt)
                except ValueError:
                    raise XProtonError(
                        f"{path}:{session_line}: invalid session JSON on this line"
                    )
        entry = _parse_entry(line, session, lineno, path)
        entry["session_line"] = session_line
        entries.append(entry)
    return entries


def _parse_entry(line, session, lineno, path):
    if line.lower().startswith("api:"):
        rest = line[4:]
        parts = rest.split(":")
        label = parts[0].strip() or "temp"
        country = None
        for part in parts[1:]:
            if part.strip():
                country = part.strip().upper()
                break
        if country and country not in consts.LOCATION_COUNTRIES:
            raise XProtonError(
                f"{path}:{lineno}: unknown country '{country}' "
                f"(choose from {', '.join(consts.LOCATION_COUNTRIES)})"
            )
        return {
            "type": "api",
            "label": label,
            "email": f"api:{label}",
            "password": None,
            "totp": None,
            "country": country,
            "session": session,
            "line": lineno,
        }
    parts = line.split(":")
    if len(parts) < 2:
        raise XProtonError(
            f"{path}:{lineno}: expected email:password[:totp][:COUNTRY] or api:<label>[:COUNTRY]"
        )
    email = parts[0].strip()
    password = parts[1].strip()
    totp = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    country = None
    if len(parts) > 3 and parts[3].strip():
        country = parts[3].strip().upper()
        if country not in consts.LOCATION_COUNTRIES:
            raise XProtonError(
                f"{path}:{lineno}: unknown country '{country}' "
                f"(choose from {', '.join(consts.LOCATION_COUNTRIES)})"
            )
    if not email or not password:
        raise XProtonError(f"{path}:{lineno}: empty email or password")
    return {
        "type": "manual",
        "email": email,
        "password": password,
        "totp": totp,
        "country": country,
        "session": session,
        "line": lineno,
    }


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def serialize_entry(entry: dict):
    """Return the two lines of an account block."""
    if entry["type"] == "api":
        line1 = f"api:{entry['label']}"
        if entry.get("country"):
            line1 += f":{entry['country']}"
    else:
        line1 = f"{entry['email']}:{entry['password']}"
        if entry.get("totp"):
            line1 += f":{entry['totp']}"
        if entry.get("country"):
            line1 += f"::{entry['country']}" if not entry.get("totp") else f":{entry['country']}"
    if entry.get("session"):
        line2 = json.dumps(entry["session"], separators=(",", ":"))
    else:
        line2 = "-"
    return line1, line2


def write_accounts(path, entries):
    blocks = [serialize_entry(e) for e in entries]
    text = HEADER
    for l1, l2 in blocks:
        text += f"{l1}\n{l2}\n"
    atomic_write(path, text, mode=0o600)


def append_accounts(path, entries):
    """Append account blocks to the file, preserving existing content."""
    blocks = [serialize_entry(e) for e in entries]
    text = ""
    if not os.path.isfile(path):
        text = HEADER
    else:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        if existing and not existing.endswith("\n"):
            text += "\n"
    for l1, l2 in blocks:
        text += f"{l1}\n{l2}\n"
    if not os.path.isfile(path):
        atomic_write(path, text, mode=0o600)
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def update_entry(entry: dict, path=None):
    """Replace one entry block in the file (surgery by line numbers, with a
    canonical-rewrite fallback)."""
    path = path or consts.ACCOUNTS_FILE
    l1, l2 = serialize_entry(entry)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.readlines()
        line = entry.get("line")
        session_line = entry.get("session_line")
        if line and 1 <= line <= len(raw):
            if session_line and 1 <= session_line <= len(raw):
                raw[line - 1] = l1 + "\n"
                raw[session_line - 1] = l2 + "\n"
            else:
                raw[line - 1] = l1 + "\n" + l2 + "\n"
            atomic_write(path, "".join(raw), mode=0o600)
            return
    except OSError:
        pass
    # Fallback: full canonical rewrite.
    entries = parse_accounts(path)
    for e in entries:
        if _same_entry(e, entry):
            e.update(entry)
            e["session_line"] = None
            e["line"] = None
    write_accounts(path, entries)


def remove_entry(entry: dict, path=None):
    """Delete one entry block from the file."""
    path = path or consts.ACCOUNTS_FILE
    entries = parse_accounts(path)
    entries = [e for e in entries if not _same_entry(e, entry)]
    write_accounts(path, entries)


def _same_entry(a: dict, b: dict) -> bool:
    if a.get("type") != b.get("type"):
        return False
    if a.get("type") == "api":
        if a.get("session") and b.get("session"):
            return a["session"].get("uid") == b["session"].get("uid")
        return a.get("label") == b.get("label")
    if a.get("email") != b.get("email"):
        return False
    if a.get("session") and b.get("session"):
        return a["session"].get("uid") == b["session"].get("uid")
    return True


# ---------------------------------------------------------------------------
# Assignment: pins first, then positional, extras become spares.
# ---------------------------------------------------------------------------
def assign_accounts(entries=None):
    """Return {country: entry} mapping (one account per location)."""
    if entries is None:
        entries = parse_accounts()
    mapping = {}
    pinned = set()
    for entry in entries:
        if entry["country"]:
            if entry["country"] in pinned:
                raise XProtonError(
                    f"two accounts are pinned to {entry['country']} "
                    f"(line {entry['line']})"
                )
            mapping[entry["country"]] = entry
            pinned.add(entry["country"])
    idx = 0
    for country in consts.LOCATION_COUNTRIES:
        if country in mapping:
            continue
        while idx < len(entries) and entries[idx]["country"]:
            idx += 1
        if idx >= len(entries):
            break
        mapping[country] = entries[idx]
        idx += 1
    return mapping


def spare_pool(entries=None):
    """Accounts that are not assigned to any location."""
    if entries is None:
        entries = parse_accounts()
    mapping = assign_accounts(entries)
    used = {id(e) for e in mapping.values()}
    return [e for e in entries if id(e) not in used]


def display_name(entry: dict) -> str:
    if entry["type"] == "api":
        return f"api:{entry['label']}"
    return entry["email"]
