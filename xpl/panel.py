"""Interactive terminal panel (TUI) for xProton.

A bordered, colored menu (the design the user sketched):
  banner -> stats -> menu -> footer. Submenus are small boxes. Falls back
  to ASCII borders on consoles that cannot print box-drawing characters.
"""

import os
import sys

from . import accounts, consts, health, provision, proxies, service, verify
from .cli import _github_latest_tag, _perform_update, _validate_port
from .util import XProtonError, port_in_use, version_tuple

# ---------------------------------------------------------------------------
# color + box primitives
# ---------------------------------------------------------------------------
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
BLUE = "\x1b[34m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"


def c(color, s):
    return f"{color}{s}{RESET}"


def _encodable(s: str) -> bool:
    try:
        (s + "x").encode(sys.stdout.encoding or "utf-8")
        return True
    except UnicodeEncodeError:
        return False


_BOX_UNI = ("\u250c", "\u2500", "\u2510", "\u2502", "\u251c", "\u2524", "\u2514", "\u2518")
_BOX_ASCII = ("+", "-", "+", "|", "+", "+", "+", "+")
BOX = _BOX_UNI if _encodable("".join(_BOX_UNI)) else _BOX_ASCII

WIDTH = 62
_INNER = WIDTH - 4


def _top():
    return BOX[0] + BOX[1] * (WIDTH - 2) + BOX[2]


def _mid():
    return BOX[4] + BOX[1] * (WIDTH - 2) + BOX[5]


def _bot():
    return BOX[6] + BOX[1] * (WIDTH - 2) + BOX[7]


def _row(text: str = "", color=None, align: str = "left"):
    if align == "center":
        left = max((_INNER - len(text)) // 2, 0)
        padded = (" " * left + text).ljust(_INNER)
    elif align == "right":
        padded = text.rjust(_INNER)
    else:
        padded = text.ljust(_INNER)
    inner = c(color, padded) if color else padded
    return BOX[3] + " " + inner + " " + BOX[3]


def _menu_item(key, label, color=BLUE, note=None):
    key_part = f"[{key}]"
    main = f"{key_part} {label}"
    if note:
        pad = max(_INNER - len(main) - len(note), 1)
        return (
            BOX[3] + " " + c(YELLOW, key_part)
            + c(color, main[len(key_part):] + " " * pad)
            + c(DIM, note) + " " + BOX[3]
        )
    return (
        BOX[3] + " " + c(YELLOW, key_part)
        + c(color, main[len(key_part):].ljust(_INNER - len(key_part)))
        + " " + BOX[3]
    )


def _show(rows):
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.write("\n".join(rows) + "\n")
    sys.stdout.flush()


def _box(title, lines):
    """Render a bordered box with centered title and content lines."""
    rows = [_top()]
    if title:
        rows.append(_row("", None, "center"))
        rows.append(_center(title, CYAN))
    rows.append(_mid())
    rows.append(_row())
    for line in lines:
        rows.append(_row(line))
    rows.append(_row())
    rows.append(_bot())
    _show(rows)


def _center(text, color=None):
    left = max((_INNER - len(text)) // 2, 0)
    padded = (" " * left + text).ljust(_INNER)
    return BOX[3] + " " + (c(color, padded) if color else padded) + " " + BOX[3]


def _submenu(title, options, prompt="choose an option"):
    """options: list of (key, label, color). Returns the picked key or None."""
    rows = [_top(), _center(title, CYAN), _mid(), _row()]
    for key, label, color in options:
        rows.append(_menu_item(key, label, color))
    rows.append(_row())
    rows.append(_menu_item("0", "Back", RED))
    rows.append(_bot())
    _show(rows)
    key = input(f"  {prompt}: ").strip().lower()
    return None if key in ("0", "q", "back", "") else key


def _msg(text, color=GREEN):
    _box("", [c(color, text)])
    print()


def _err(text):
    _box("", [c(RED, "! " + text)])
    print()


def _pause():
    try:
        input(c(DIM, "  [Enter] back to menu "))
    except EOFError:
        pass


def _warn_file_changed():
    _box("accounts.txt changed", [
        c(YELLOW, "The account file was modified."),
        c(YELLOW, "Run [3] Verify Accounts from the main menu to"),
        c(YELLOW, "confirm every account is still healthy."),
    ])


def _need_root():
    _err("this action needs root - run the panel with sudo")


# ---------------------------------------------------------------------------
# banner + main screen
# ---------------------------------------------------------------------------
BANNER = [
    "          ______",
    "         (_____ \\      _",
    "   _   _  _____) )  ____   ___   _| |_   ___   ____",
    "  ( \\ / )|  ____/  / ___) / _ \\ (_   _) / _ \\ |  _ \\",
    "   ) X ( | |      | |    | |_| |  | |_ | |_| || | | |",
    "  (_/ \\_)|_|      |_|     \\___/    \\__) \\___/ |_| |_|",
]


def _stats_row():
    entries = accounts.parse_accounts()
    spares = len(accounts.spare_pool(entries))
    running = 0
    if service.unit_installed():
        running = sum(1 for c in consts.LOCATION_COUNTRIES if service.is_active(c))
    left = f"Active Locations: {running}/{len(consts.LOCATION_COUNTRIES)}"
    right = f"Active Accounts: {len(entries)}"
    if spares:
        right += f" ({spares} spare)"
    pad = max(_INNER - len(left) - len(right), 1)
    return _row(left + " " * pad + right, color=GREEN)


def _render_main():
    rows = [_top()]
    for line in BANNER:
        rows.append(_center(line, CYAN))
    rows.append(_center(f"v{consts.VERSION}  |  multi-location ProtonVPN", DIM))
    rows.append(_mid())
    rows.append(_stats_row())
    rows.append(_mid())
    rows.append(_row())
    rows.append(_menu_item("1", "Create Account (via API)", BLUE))
    rows.append(_menu_item("2", "Manage / Import Accounts", BLUE))
    rows.append(_menu_item("3", "Verify Accounts", BLUE))
    rows.append(_row())
    rows.append(_menu_item("4", "Start Location", BLUE))
    rows.append(_menu_item("5", "Stop Location", BLUE))
    rows.append(_menu_item("6", "Restart Location", BLUE))
    rows.append(_row())
    rows.append(_menu_item("7", "Port Manager", BLUE))
    rows.append(_menu_item("8", "Speed & Ping Test", BLUE))
    rows.append(_menu_item("9", "Update xProton", GREEN))
    rows.append(_menu_item("u", "Uninstall xProton", RED))
    rows.append(_row())
    rows.append(_menu_item("0", "Exit", RED))
    rows.append(_bot())
    _show(rows)
    hint = "run with sudo for start/stop/provision" if not _is_root() else "choose an option"
    return input(f"  {c(DIM, hint)}  > ").strip().lower()


def _is_root():
    try:
        return os.geteuid() == 0
    except AttributeError:
        return True


# ---------------------------------------------------------------------------
# submenu handlers
# ---------------------------------------------------------------------------
def _create_accounts():
    _box("Create Account (via API)", [
        "Accounts are created through the temp-account backend.",
        "Bulk signups from one IP risk a block, so:",
        f"  without proxy  -> max {consts.MAX_ACCOUNTS_NO_PROXY}",
        f"  with proxy     -> max {consts.MAX_ACCOUNTS_WITH_PROXY}",
    ])
    has_proxy = input("  Do you have a proxy list? [y/N] ").strip().lower() in ("y", "yes")
    proxies_list = None
    if has_proxy:
        path = input("  Proxy file path (one proxy per line): ").strip()
        try:
            proxies_list = proxies.read_proxies(path)
        except XProtonError as e:
            _err(str(e))
            return
        _msg(f"{len(proxies_list)} proxy/proxies loaded")
    cap = consts.MAX_ACCOUNTS_WITH_PROXY if proxies_list else consts.MAX_ACCOUNTS_NO_PROXY
    hint = "max 50 with proxy" if proxies_list else f"max {cap} - no proxy: protect your server IP"
    raw = input(f"  How many accounts? ({hint}): ").strip()
    try:
        count = int(raw)
    except ValueError:
        _err("invalid number")
        return
    if not (1 <= count <= cap):
        _err(f"count must be between 1 and {cap}")
        return
    print()
    print(c(CYAN, "  creating accounts (this takes a few seconds each)..."))
    try:
        created, failed = provision.create_accounts(count, proxies_list)
    except XProtonError as e:
        _err(str(e))
        return
    lines = []
    if created:
        lines.append(c(GREEN, f"  {len(created)} account(s) created and saved to accounts.txt"))
        for e in created:
            pin = f" for {e['country']}" if e.get("country") else " (spare)"
            lines.append(f"  {accounts.display_name(e)}{pin}")
    if failed:
        lines.append(c(RED, f"  {len(failed)} account(s) failed"))
        for f_ in failed:
            lines.append(f"  #{f_['index']} {f_['country'] or 'spare'}: {f_['error']}")
    _box("Done", lines)
    if created:
        _warn_file_changed()
    _pause()


def _manage_accounts():
    while True:
        key = _submenu("Manage / Import Accounts", [
            ("1", "View accounts", BLUE),
            ("2", "Add account (manual)", BLUE),
            ("3", "Edit file ($EDITOR)", BLUE),
            ("4", "Delete account", BLUE),
            ("5", "Show assignment map", BLUE),
        ])
        if key is None:
            return
        if key == "1":
            _view_accounts()
        elif key == "2":
            _add_account()
        elif key == "3":
            _edit_accounts_file()
        elif key == "4":
            _delete_account()
        elif key == "5":
            _show_assignment()


def _view_accounts():
    entries = accounts.parse_accounts()
    if not entries:
        _err("no accounts yet - create some (option 1) or edit the file")
        return
    lines = [f"  {'TYPE':<7}{'ACCOUNT':<34}{'COUNTRY':<8}{'SESS':<5}"]
    lines.append("  " + "-" * 54)
    for e in entries:
        lines.append(
            f"  {e['type'].upper():<7}{accounts.display_name(e):<34}"
            f"{(e.get('country') or '-'):<8}{'yes' if e.get('session') else 'no':<5}"
        )
    _box(f"Accounts ({len(entries)})", lines)
    _pause()


def _add_account():
    raw = input("  email:password[:totp_secret][:COUNTRY] > ").strip()
    if not raw:
        return
    try:
        entry = accounts._parse_entry(raw, None, 0, "input")
    except XProtonError as e:
        _err(str(e))
        return
    entry["line"] = None
    entry["session_line"] = None
    accounts.append_accounts(consts.ACCOUNTS_FILE, [entry])
    _msg(f"added {entry['email']}" + (f" pinned to {entry['country']}" if entry.get("country") else ""))
    _warn_file_changed()
    _pause()


def _edit_accounts_file():
    editor = os.environ.get("EDITOR") or "nano"
    try:
        import subprocess
        subprocess.call([editor, consts.ACCOUNTS_FILE])
    except FileNotFoundError:
        try:
            import subprocess
            subprocess.call(["vi", consts.ACCOUNTS_FILE])
        except FileNotFoundError:
            _err(f"no editor found - set $EDITOR")
            return
    try:
        accounts.parse_accounts()
    except XProtonError as e:
        _err(f"accounts.txt has a parse error: {e}")
        _pause()
        return
    _msg("accounts.txt saved and parsed OK")
    _warn_file_changed()
    _pause()


def _delete_account():
    entries = accounts.parse_accounts()
    if not entries:
        _err("no accounts to delete")
        return
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(
            f"  [{i}] {e['type'].upper():<7}{accounts.display_name(e):<34}"
            f"{(e.get('country') or '-'):<8}"
        )
    _box("Pick an account to delete", lines)
    raw = input("  number > ").strip()
    try:
        idx = int(raw) - 1
        target = entries[idx]
    except (ValueError, IndexError):
        _err("invalid number")
        return
    confirm = input(f"  delete {accounts.display_name(target)}? [y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        return
    accounts.remove_entry(target)
    _msg(f"deleted {accounts.display_name(target)}")
    _warn_file_changed()
    _pause()


def _show_assignment():
    entries = accounts.parse_accounts()
    mapping = accounts.assign_accounts(entries)
    spares = accounts.spare_pool(entries)
    lines = []
    for country, entry in mapping.items():
        lines.append(f"  {country:<4} -> {accounts.display_name(entry)}")
    if spares:
        lines.append("")
        lines.append("  spares (not assigned):")
        for e in spares:
            lines.append(f"    {accounts.display_name(e)}")
    _box("Assignment", lines)
    _pause()


def _verify_accounts():
    entries = accounts.parse_accounts()
    if not entries:
        _err("no accounts yet - create some (option 1)")
        return
    print(c(CYAN, f"  verifying {len(entries)} account(s)..."))
    results = verify.verify_entries(
        entries, progress=lambda label, st: print(f"    {label:<38} {st}")
    )
    print()
    lines = []
    for r in results:
        label = accounts.display_name(r["entry"])
        mark = c(GREEN, "OK  ") if r["ok"] else c(RED, "FAIL")
        lines.append(f"  {mark} {label:<34} {r['detail']}")
    good = sum(1 for r in results if r["ok"])
    lines.append("")
    lines.append(c(GREEN if good == len(results) else RED,
                   f"  {good}/{len(results)} accounts healthy"))
    _box("Verify result", lines)
    print(c(CYAN, "  checking provisioned certificates..."))
    actions = verify.reissue_expiring(results)
    for country, msg in actions:
        print(f"    [{country}] {msg}")
    if not actions:
        _msg("all certificates valid")
    _pause()


def _location_rows(action):
    rows = []
    for i, (country, _dflt) in enumerate(consts.FREE_LOCATIONS, 1):
        state = provision.read_state(country)
        port = (state or {}).get("socks_port", consts.DEFAULT_SOCKS_PORTS[country])
        running = service.is_active(country)
        status = c(GREEN, "running") if running else c(DIM, "stopped")
        rows.append((str(i), f"{country}   127.0.0.1:{port:<6}  {status}", BLUE))
    return rows


def _location_action(action):
    rows = _location_rows(action)
    rows.append(("a", "all locations", BLUE))
    key = _submenu(f"{action} Location", rows)
    if key is None:
        return
    try:
        if key == "a":
            targets = consts.LOCATION_COUNTRIES
        else:
            targets = [consts.LOCATION_COUNTRIES[int(key) - 1]]
    except (ValueError, IndexError):
        _err("invalid choice")
        return
    try:
        service.require_root(action.lower())
    except XProtonError:
        _need_root()
        return
    for country in targets:
        port = (provision.read_state(country) or {}).get(
            "socks_port", consts.DEFAULT_SOCKS_PORTS[country]
        )
        try:
            if action == "Start":
                if not provision.is_provisioned(country):
                    print(f"  [{country}] provisioning...")
                    provision.provision_location(country)
                service.start(country)
                print(f"  [{country}] started  ->  socks5://127.0.0.1:{port}")
            elif action == "Stop":
                service.stop(country)
                print(f"  [{country}] stopped")
            elif action == "Restart":
                service.restart(country)
                print(f"  [{country}] restarted")
        except XProtonError as e:
            print(f"  {c(RED, '!')} [{country}] {e}")
    _pause()


def _port_manager():
    rows = []
    for i, (country, dflt) in enumerate(consts.FREE_LOCATIONS, 1):
        state = provision.read_state(country)
        port = (state or {}).get("socks_port", dflt)
        running = service.is_active(country)
        status = c(GREEN, "running") if running else c(DIM, "stopped")
        marker = c(YELLOW, " (custom)") if port != dflt else ""
        rows.append((str(i), f"{country}   127.0.0.1:{port:<6}  {status}{marker}", BLUE))
    key = _submenu("Port Manager", rows)
    if key is None:
        return
    try:
        country = consts.LOCATION_COUNTRIES[int(key) - 1]
    except (ValueError, IndexError):
        _err("invalid choice")
        return
    dflt = consts.DEFAULT_SOCKS_PORTS[country]
    state = provision.read_state(country)
    cur = (state or {}).get("socks_port", dflt)
    raw = input(f"  new SOCKS port for {country} (current {cur}, 'd' for default {dflt}): ").strip()
    if raw.lower() in ("d", "default", "reset"):
        new_port = dflt
    else:
        try:
            new_port = int(raw)
        except ValueError:
            _err("invalid port")
            return
    try:
        _validate_port(country, new_port)
    except XProtonError as e:
        _err(str(e))
        return
    from .util import write_json
    write_json(provision.state_path(country), {
        "country": country,
        "socks_port": new_port,
    }, mode=0o600)
    if state:  # regenerate config so the running tunnel uses the new port
        from . import singbox
        singbox.write_config(state)
    if service.is_active(country):
        try:
            service.require_root("port change")
            service.restart(country)
        except XProtonError:
            _need_root()
            return
    _msg(f"{country} SOCKS port -> 127.0.0.1:{new_port}")
    _pause()


def _speed_test():
    targets = []
    for country in consts.LOCATION_COUNTRIES:
        state = provision.read_state(country)
        if state and port_in_use(state["socks_port"]):
            targets.append((country, state["socks_port"]))
    if not targets:
        _err("no running locations to test - start some first (option 4)")
        return
    print(c(CYAN, "  testing running locations (ping + exit IP + speed)..."))
    results = []
    for country, port in targets:
        print(f"    {country} ...")
        res = health.test_location(country, port)
        sp = None
        if res["ok"]:
            sp = health.speed_location(country, port)
        results.append((country, res, sp))
    lines = ["  " + f"{'LOC':<5}{'LATENCY':<10}{'EXIT IP':<18}{'SPEED':<10}"]
    lines.append("  " + "-" * 42)
    for country, res, sp in results:
        if res["ok"]:
            lat = f"{res['latency_ms']}ms"
            ip = res.get("exit_ip") or "?"
            speed = f"{sp['mbps']} Mbps" if sp and sp["ok"] else "-"
        else:
            lat, ip, speed = "-", "-", "-"
        lines.append(f"  {country:<5}{lat:<10}{ip:<18}{speed:<10}")
    _box("Speed & Ping Test", lines)
    _pause()


def _update():
    tag = _github_latest_tag(consts.GITHUB_REPO)
    if tag is None:
        _err("no release found on GitHub yet - nothing to update")
        return
    _msg(f"local {consts.VERSION}   latest {tag}")
    if version_tuple(tag) <= version_tuple(consts.VERSION):
        _msg("xProton is already up to date")
        _pause()
        return
    answer = input(f"  update to {tag} now? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        return
    try:
        _perform_update(consts.GITHUB_REPO, tag)
    except XProtonError as e:
        _err(str(e))
    _pause()


def _uninstall():
    try:
        from .cli import cmd_uninstall
        cmd_uninstall([])
    except XProtonError as e:
        _err(str(e))
        return
    _msg("xProton removed")
    _pause()


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
def run():
    while True:
        choice = _render_main()
        try:
            if choice == "1":
                _create_accounts()
            elif choice == "2":
                _manage_accounts()
            elif choice == "3":
                _verify_accounts()
            elif choice == "4":
                _location_action("Start")
            elif choice == "5":
                _location_action("Stop")
            elif choice == "6":
                _location_action("Restart")
            elif choice == "7":
                _port_manager()
            elif choice == "8":
                _speed_test()
            elif choice == "9":
                _update()
            elif choice == "u":
                _uninstall()
            elif choice in ("0", "q", "exit"):
                print(c(DIM, "  bye"))
                return 0
            else:
                _err("unknown option")
        except XProtonError as e:
            _err(str(e))
        except KeyboardInterrupt:
            print(c(DIM, "\n  bye"))
            return 0
