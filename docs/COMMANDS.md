# 📚 xProton — Command Reference

> *`xproton`, `xpt` and `xpn` are all symlinks to the same binary. Any of
> them works.*

## Entry points

```bash
xproton              # interactive panel (no arguments)
xproton status       # per-location state table
xproton locations    # list locations + fixed ports
xproton version      # show the installed version
```

The three names are interchangeable — `xproton`, `xpn`, `xpt`.

---

## Account management

### Create accounts (via API)

```bash
xproton create                    # interactive (asks for proxy, then count)
xproton create --count 10 --yes   # 10 accounts, auto-assigned to free countries
xproton create --count 20 --proxy-file /path/proxies.txt --yes`
xproton create --count 3 --countries US,CA
```

- Without a proxy list: **max 10**.
- With a proxy list: **max 50**.
- Dead proxies are skipped automatically.

### Verify accounts

```bash
xproton verify                     # check every account + renew expiring certs
xproton verify --proxy-file proxies.txt
```

Returns `OK` / `FAIL` per account, prints a summary, and re-issues any
WireGuard certificates that are about to expire.

### Show / manage the account store

```bash
xproton accounts                   # table + assignment map + spares
```

All accounts live in `/etc/xproton/accounts.txt` (root-only, 0600) as
two-line blocks — see the README.

---

## Locations

```bash
sudo xproton start all             # provision (if needed) + start all 10
sudo xproton start US              # one location
sudo xproton stop [US|all]
sudo xproton restart [US|all]
xproton logs US [-f] [-n 100]      # journald logs for one location
xproton test [US|all] [--speed]    # latency / exit IP / speed through SOCKS
```

### Port manager

```bash
sudo xproton port US --socks 64210   # change US SOCKS port
```

The port must be 1024–65535 and not already used by another location. The
running tunnel is restarted automatically.

---

## Provisioning

```bash
sudo xproton provision [US|all] [--provider auto|manual|temp] [--force]
```

- `auto` (default): use the account assigned to a location.
- `manual`: only use manually-added accounts.
- `temp`: create a fresh throwaway account on the fly (legacy).
- `--force`: rebuild even if a valid state already exists.

---

## Lifecycle

```bash
sudo xproton autostart on|off|status   # systemd start-on-boot
sudo xproton doctor                    # full health check
sudo xproton update [--check]          # update from GitHub
sudo xproton uninstall [--purge] [--yes]   # remove xProton
```

- `update` fetches the latest GitHub release, asks for confirmation, updates
  the code, refreshes the systemd unit and **restarts running tunnels**.
- `uninstall` stops everything and removes the code, symlinks and unit
  (`--purge` also deletes `/etc/xproton` data; by default accounts are kept).

---

## Useful one-liners

```bash
# exit IP through a specific country
curl --socks5-hostname 127.0.0.1:64210 https://api.ipify.org

# full diagnostics after install
sudo xproton doctor
```