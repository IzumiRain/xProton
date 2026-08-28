#!/usr/bin/env bash
#
# xProton installer — runs multiple ProtonVPN locations on one Ubuntu server,
# each exposing SOCKS5 on 127.0.0.1:<fixed port>.
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/IzumiRain/xProton/main/install.sh)
#   # or from a local checkout:
#   sudo bash install.sh
#
# Env overrides:
#   XPROTON_GITHUB_REPO   (default: IzumiRain/xProton)
#   XPROTON_GITHUB_REF    (default: main)
#   XPROTON_SINGBOX_VER   (default: 1.13.19)
#   XPROTON_INSTALL_DIR   (default: /opt/xproton)
#
# WARNING: automating account creation violates Proton ToS. Use at your own
# risk — see README.md.

set -euo pipefail

# ---- banner ----
cat <<'EOF'
    ______  __  _____  ____    ___    ____  _   _  ____  _____   __
   (_) __/ /_/ / __  )/ __ \  /   |  / __ \| | | ||    \|  |  | ||
     _\ \ / -_) /  __/ /  / / / /| | / /_/ /| |_| ||  o  )|  |  | ||
   (_)___/\__/_\__(_) /_/ /_/ /_/ |_| \____(_)____/|____/ |______|

           xProton  ·  multi-location ProtonVPN on one server
              every location = its own SOCKS5 on 127.0.0.1

------------------------------------------------------------------
EOF

log() { printf '\033[1;34m[xproton]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[xproton] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

REPO="${XPROTON_GITHUB_REPO:-IzumiRain/xProton}"
REF="${XPROTON_GITHUB_REF:-main}"
SINGBOX_VER="${XPROTON_SINGBOX_VER:-1.13.19}"
INSTALL_DIR="${XPROTON_INSTALL_DIR:-/opt/xproton}"
ETC_DIR="/etc/xproton"

log() { printf '\033[1;34m[xproton]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[xproton] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo bash install.sh"

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  SINGBOX_ARCH="linux-amd64" ;;
  aarch64|arm64) SINGBOX_ARCH="linux-arm64" ;;
  *) die "unsupported architecture: $ARCH" ;;
esac

log "installing prerequisites (python3, curl, ca-certificates)..."
apt-get update -y -qq >/dev/null 2>&1 || true
apt-get install -y -qq python3 curl ca-certificates >/dev/null 2>&1 || apt-get install -y python3 curl ca-certificates

# ---- fetch code ------------------------------------------------------------
SRC_DIR=""
if [ -f "$(dirname "$0")/xproton" ] && [ -d "$(dirname "$0")/xpl" ]; then
  SRC_DIR="$(cd "$(dirname "$0")" && pwd)"     # local checkout (dev mode)
  log "installing from local checkout: $SRC_DIR"
else
  log "downloading xProton ($REPO @ $REF)..."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz" -o "$TMP/xproton.tar.gz" \
    || die "failed to download xProton"
  tar -xzf "$TMP/xproton.tar.gz" -C "$TMP"
  SRC_DIR="$(find "$TMP" -maxdepth 1 -type d -name 'xProton-*' | head -1)"
  [ -n "$SRC_DIR" ] || die "could not locate extracted sources"
fi

mkdir -p "$INSTALL_DIR" "$ETC_DIR/instances" "$ETC_DIR/bin"
log "copying code to $INSTALL_DIR..."
cp -r "$SRC_DIR/xproton" "$SRC_DIR/xpl" "$INSTALL_DIR/"
chmod 755 "$INSTALL_DIR/xproton"

# ---- sing-box binary (GPL-3.0, external program) --------------------------
if [ ! -x "$ETC_DIR/bin/sing-box" ]; then
  log "downloading sing-box ${SINGBOX_VER} (${SINGBOX_ARCH})..."
  curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VER}/sing-box-${SINGBOX_VER}-${SINGBOX_ARCH}.tar.gz" \
    -o /tmp/singbox.tar.gz || die "failed to download sing-box"
  tar -xzf /tmp/singbox.tar.gz -C /tmp
  cp "/tmp/sing-box-${SINGBOX_VER}-${SINGBOX_ARCH}/sing-box" "$ETC_DIR/bin/sing-box"
  chmod 755 "$ETC_DIR/bin/sing-box"
  rm -f /tmp/singbox.tar.gz
fi
"$ETC_DIR/bin/sing-box" version >/dev/null 2>&1 || die "sing-box binary is broken"

# ---- entry points ----------------------------------------------------------
for NAME in xproton xpn xpt; do
  ln -sf "$INSTALL_DIR/xproton" "/usr/local/bin/$NAME"
done

# ---- systemd unit ----------------------------------------------------------
if [ -f "$SRC_DIR/systemd/xproton@.service" ]; then
  cp "$SRC_DIR/systemd/xproton@.service" /etc/systemd/system/xproton@.service
else
  die "systemd unit template missing"
fi
chmod 644 /etc/systemd/system/xproton@.service
systemctl daemon-reload

# ---- accounts template ----------------------------------------------------
if [ ! -f "$ETC_DIR/accounts.txt" ]; then
  cp "$SRC_DIR/accounts.example.txt" "$ETC_DIR/accounts.txt"
  chmod 600 "$ETC_DIR/accounts.txt"
fi

log "done."
echo
echo "  next steps:"
echo "    sudo xpn doctor"
echo "    sudo xpn start US      # or: sudo xpn start all"
echo "    xpn test US            # check exit IP through the tunnel"
echo
echo "  WARNING: this tool creates throwaway Proton accounts automatically."
echo "  That is against Proton's ToS; accounts can be banned at any time."
echo "  Use at your own risk. See README.md for the full warning list."
