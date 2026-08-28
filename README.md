<p align="center">
  <strong style="font-size:2.5em">🌧️ xProton</strong><br/>
  <em>multi-location ProtonVPN on a single server &middot; one SOCKS5 per country</em><br/>
  <code>v1.1.0-beta</code> &middot; <a href="README.fa.md">🇮🇷 فارسی</a>
</p>

<p align="center">
  <a href="#-live-test-results"><img alt="stress tested ~3 TiB" src="https://img.shields.io/badge/stress%20tested-%E2%89%883%20TiB-%235a9"></a>
  <a href="#-warning"><img alt="beta" src="https://img.shields.io/badge/status-beta-orange"></a>
  <a href="#-test-only"><img alt="for testing" src="https://img.shields.io/badge/purpose-test--only-critical"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-brightgreen"></a>
</p>

> **🚧 BETA — FOR TESTING ONLY, not for production.** This is a beta release
> built to validate the idea on real hardware. It is **not** a finished,
> production-ready tool. Read [the warnings](#-warning) and
> [the test results](#-live-test-results) before doing anything with it.

---

## ✨ What is it

**xProton** runs **10 ProtonVPN locations on one Linux server**, each bound to
a **fixed SOCKS5 port on `127.0.0.1`**. One account → one tunnel → one port,
so you can route 10 different countries at once and pick which apps use which.

```
xproton                # interactive panel (xproton / xpt / xpn all work)
sudo xproton start all # provision + start every location
xproton test US        # 127.0.0.1:64210  latency 425ms  exit 72.251.221.12
```

### 📍 Locations & fixed ports

| Location | SOCKS5 | Location | SOCKS5 |
|---|---|---|---|
| 🇨🇦 CA | `127.0.0.1:64201` | 🇳🇴 NO | `127.0.0.1:64206` |
| 🇨🇭 CH | `127.0.0.1:64202` | 🇵🇱 PL | `127.0.0.1:64207` |
| 🇯🇵 JP | `127.0.0.1:64203` | 🇷🇴 RO | `127.0.0.1:64208` |
| 🇲🇽 MX | `127.0.0.1:64204` | 🇸🇬 SG | `127.0.0.1:64209` |
| 🇳🇱 NL | `127.0.0.1:64205` | 🇺🇸 US | `127.0.0.1:64210` |

All **10 free-tier countries** are covered. Ports default to `64201–64210`
(depending on the location) and can be changed per-location from the panel
or `xproton port` command.

---

## 🧪 Live Test Results

> These are the real results from a stress test run on an hourly VPS
> (Ubuntu 24.04, 1 vCPU / 2 GB RAM) against ProtonVPN's free tier.

| Metric | Result |
|---|---|
| Locations tested | **10 / 10**, simultaneously |
| Download traffic consumed | **≈ 3 TiB (≈ 2.9 × 10¹² bytes)** |
| Peak aggregate rate | several Gb/s spread across 10 tunnels |
| Parallel connections per account | **3** (to see if multi-conn triggers a ban) |
| Accounts banned | **0** ✅ |

**What we learned:**

1. **Proton does not ban free accounts for heavy download usage** — not even
   after ~3 TiB of sustained traffic with multiple parallel connections per
   account. Consumption-heavy use is not what triggers their anti-abuse.
2. **What DOES risk a ban is the signup flow**, not bandwidth: mass account
   creation from a single IP (CAPTCHA / 9001 errors), multiple connections
   sharing an account, and ToS-violating automation of the temp-account
   backend. This is why the tool **paces** account creation and offers
   **proxy rotation** for bulk signups.
3. Every location came back healthy after the test — same exit IPs, low
   latency, tunnels up.

> **Interpretation:** xProton is useful as long as you're a "normal-ish"
> bandwidth consumer. It is **not** designed for unlimited TB-scale abuse;
> the moment you start churning temp accounts or pushing absurd volumes from
> a flagged IP, Proton can and will cut accounts.

### ⚙️ How to reproduce

```bash
# on the server, after install + `xproton start all`:
bash tools/stress-test.sh 1000     # pull 1 GiB through each of the 10 ports
tail -f /run/xproton-stress/results.log
bash tools/stress-test.sh stop     # halt all workers
```

> ⚠️ Pulling terabytes on a metered VPS has a real egress cost. We ran this
> on an hourly box and stopped before it mattered. You were warned.

---

## 🚀 Quick start (one line)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/IzumiRain/xProton/main/install.sh)
```

That single command: installs prerequisites, downloads the pinned
`sing-box` binary (GPL-3.0, run as an external program), installs the
`xproton` / `xpt` / `xpn` commands, the `xproton@.service` systemd template,
and an `accounts.txt` template.

```bash
sudo xproton doctor          # health check
xproton create               # create API accounts (proxy-aware)
sudo xproton start all       # provision + start every location
xproton status               # per-location table
xproton test all --speed     # latency + exit IP + speed per location
```

## 📚 Documentation

- Full command reference: [`docs/COMMANDS.md`](docs/COMMANDS.md)
- Interactive panel: run `xproton` with no arguments
- Everything is explained inline in the panel.

---

## ⚠️ Warning

This project is **for educational and research purposes only**. Read these
before using it — you are fully responsible for what you do with it.

1. **Use at your own risk.** Not affiliated with Proton AG in any way.
   ProtonVPN is a trademark of Proton AG.
2. **Proton ToS violation.** The built-in provisioner automates account
   creation, which is against Proton's Terms of Service. Accounts can be
   banned at any time with no recourse.
3. **Not for illegal activity.** Exit traffic leaves through ProtonVPN free
   servers, which have their own logging & abuse policies. If you break the
   law through this tool, it's your problem, not ours.
4. **No guarantees / no SLA.** Temp accounts may stop working, the
   third-party session backend may throttle or disappear, and free-tier
   server pools rotate. Tunnels can die silently.
5. **CAPTCHA / IP-ban risk.** Creating many accounts or logging in from a
   single datacenter IP can trigger Proton's anti-abuse. This tool paces
   creation and supports proxy lists to keep the server IP out of bulk
   signups — but nothing is guaranteed.
6. **You own the consequences.** If you run this on a production box or rely
   on it for anything important, that's on you. This is a **beta** tool.
7. **Metered-traffic costs.** Stress testing can rack up real egress bills on
   a metered VPS. Be careful.

---

## 🛠️ Command reference

| Command | What it does |
|---|---|
| `xproton` (or `xpt`, `xpn`) | interactive panel |
| `xproton status` | per-location state table |
| `xproton locations` | list locations + fixed ports |
| `sudo xproton start [US\|all]` | provision + start location(s) |
| `sudo xproton stop [US\|all]` | stop location(s) |
| `sudo xproton restart [US\|all]` | restart location(s) |
| `xproton logs US [-f] [-n 100]` | journald logs for one location |
| `xproton test [US\|all] [--speed]` | latency / exit IP / speed through SOCKS |
| `xproton create [--count N] [--proxy-file F]` | create API accounts (10 max no proxy, 50 with) |
| `xproton verify [--proxy-file F]` | verify all accounts + renew expiring certs |
| `sudo xproton provision [US\|all] [--provider auto\|manual\|temp]` | (re)build accounts + certs |
| `sudo xproton port US --socks 64210` | change a location's SOCKS port |
| `xproton accounts` | show accounts.txt mapping + spares |
| `sudo xproton autostart on\|off\|status` | start on boot |
| `sudo xproton doctor` | health checks |
| `sudo xproton update [--check]` | update from GitHub |
| `sudo xproton uninstall [--purge] [--yes]` | remove xProton |

---

## 🔑 Accounts

All accounts live in one file, `/etc/xproton/accounts.txt` (root-only, 0600),
as **two-line blocks**:

```text
# Manual account (you created it at account.proton.me — free forever):
myname@proton.me:S3cretPass:JBSWY3DPEHPK3PXP:JP
{"uid":"...","accessToken":"..."}

# API-created account (tempo, no credentials returned by the backend):
api:tmp-3f9a2c71:US
{"uid":"...","accessToken":"...","refreshToken":"..."}
```

- **Manual** accounts are permanent and owned by you (free forever).
- **API** accounts have no credentials (the backend doesn't return any), so
  they depend on the temp-account backend and can die at any time — when a
  stored session dies, xProton **self-heals** by re-creating the account.
- Certificates (~1 year) are re-issued automatically by `xproton verify`
  before they expire.
- Accounts pinned to a country fill that location; the rest fill the
  remaining locations in order; extras become **spares** used when a location
  account gets banned.

### Proxy lists for account creation

Free proxy lists work directly (plain `ip:port` lines), e.g. from
[monosans/proxy-list](https://github.com/monosans/proxy-list),
[TheSpeedX/PROXY-List](https://github.com/TheSpeedX/PROXY-List), or
[proxifly/free-proxy-list](https://github.com/proxifly/free-proxy-list):

```text
# proxies.txt — one per line, comments (#) ignored
socks5://user:pass@1.2.3.4:1080
socks4://5.6.7.8:1080
http://9.9.9.9:3128
31.76.80.215:1080          # plain ip:port defaults to socks5
```

`xproton create` asks whether you have a proxy list, then the count.
Limits: **10 accounts** without a proxy list, **50** with one. Dead proxies
are skipped automatically.

---

## 🏗️ How it works

- **One location = one account = one tunnel = one SOCKS5 port.**
  Proton Free allows **1 VPN connection per account**, so every location has
  its own account.
- **Two account sources:** API (auto, temp) or manual (yours).
- **Tunnels run in userspace** with [sing-box](https://sing-box.sagernet.org/)
  `wireguard` endpoints — no kernel module, no routing conflicts between the
  10 tunnels.
- **DNS is resolved through the tunnel** (10.2.0.1), so DNS also exits from
  the location's IP.

```
 apps ─► 127.0.0.1:64210 (socks5, US) ──► sing-box ──► Proton US-FREE
 apps ─► 127.0.0.1:64205 (socks5, NL) ──► sing-box ──► Proton NL-FREE
 apps ─► 127.0.0.1:64201 (socks5, CA) ──► sing-box ──► Proton CA-FREE
```

## 🖥️ Interactive panel

```bash
xproton
```

A bordered menu with the xProton banner, live stats, and every action:

```
[1] Create Account (via API)    [2] Manage / Import Accounts
[3] Verify Accounts             [4] Start Location
[5] Stop Location               [6] Restart Location
[7] Port Manager                [8] Speed & Ping Test
[9] Update xProton              [u] Uninstall xProton
[0] Exit
```

---

## 🧰 Requirements

- Ubuntu 22 / 24 (any recent Linux with systemd)
- `python3` ≥ 3.10, `curl`
- Root access for install + start/stop
- ~1 GB RAM recommended when running all 10 locations

---

## ❓ FAQ / Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Proton API error 9001` | CAPTCHA demanded. Wait, use a different account or IP. |
| `Proton API error 5003` | stale app version → set `app_version` in config.json |
| `Proton API error 8001` | wrong credentials in accounts.txt |
| `Proton API error 10013` | account uses two-password mode → switch to one |
| `no free server available in X` | free-country pool rotated; try another location |
| `xproton test` shows nothing listening | unit crashed → `xproton logs US`; or port changed |
| backend errors on temp provider | third-party backend down/throttled → use manual accounts |

---

## 🙏 Donate

If xProton helped you, a coffee (or a star ⭐) goes a long way:

| Network | Address |
|---------|---------|
| **TRC20** (Tron) | `TKBHWNoeygcaCK8N78e7dQX5Yco3WTb6ZN` |
| **BEP20** (BNB Smart Chain) | `0x0F982640a69D3B9FB944840D7DA8bECCfcF0bb9E` |
| **TON** | `UQAyLUyxew-eggwhxbzsAZZZ9ULM8MYOk-3IXFh7tNC33LNt` |

## 📄 License

MIT — see [LICENSE](LICENSE). Run as external programs: **sing-box**
(GPL-3.0) and the **SRP algorithm** from Proton's MIT-licensed `go-srp`
(reimplemented independently in Python — nothing copied).