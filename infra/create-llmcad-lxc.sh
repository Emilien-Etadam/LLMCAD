#!/usr/bin/env bash
# create-llmcad-lxc.sh
#
# Proxmox VE host-side automation: create an unprivileged LXC, install LLMCAD,
# configure systemd, and verify outbound connectivity to external AI/RAG services.
#
# Usage:
#   Run as root on a Proxmox node:
#     ./create-llmcad-lxc.sh
#
# Override defaults via environment variables, e.g.:
#   CT_ID=200 CT_RAM=8192 ./create-llmcad-lxc.sh
#
# Non-interactive overwrite of an existing CT:
#   LLMCAD_DESTROY_EXISTING=1 CT_ID=200 ./create-llmcad-lxc.sh

set -euo pipefail

# -----------------------------------------------------------------------------
# Defaults (override via environment)
# -----------------------------------------------------------------------------

CT_ID="${CT_ID:-}"
CT_HOSTNAME="${CT_HOSTNAME:-llmcad}"
CT_DISK="${CT_DISK:-20}"
CT_RAM="${CT_RAM:-4096}"
CT_CORES="${CT_CORES:-4}"
CT_STORAGE="${CT_STORAGE:-sas600}"
CT_TEMPLATE="${CT_TEMPLATE:-local:vztmpl/debian-13-standard_13.1-1_amd64.tar.zst}"
GITHUB_REPO="${GITHUB_REPO:-https://github.com/Emilien-Etadam/LLMCAD.git}"

QDRANT_URL="${QDRANT_URL:-http://192.168.30.127:6333}"
TEI_URL="${TEI_URL:-http://192.168.30.121:8080}"
VLLM_URL="${VLLM_URL:-http://192.168.30.121:8000/v1}"
VLLM_MODEL="${VLLM_MODEL:-/data/models/qwen3-32b-fp8}"
# Mirror of VLLM_URL for apps that read VLLM_BASE_URL (see .env.example).
VLLM_BASE_URL="${VLLM_BASE_URL:-$VLLM_URL}"

APP_USER="${APP_USER:-llmcad}"

# Optional: set to skip the interactive destroy prompt when CT_ID already exists.
LLMCAD_DESTROY_EXISTING="${LLMCAD_DESTROY_EXISTING:-0}"

# Optional root password for the container (pct create). If unset, a random
# password is generated at runtime (never printed; use `pct enter` from host).
CT_ROOT_PASSWORD="${CT_ROOT_PASSWORD:-}"

# NVM version tag (official install script URL uses this tag).
NVM_VERSION="${NVM_VERSION:-v0.40.1}"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

log() {
  echo "[$(date +%H:%M:%S)] $*"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_root() {
  if [[ "${EUID:-0}" -ne 0 ]]; then
    die "This script must run as root on the Proxmox VE host."
  fi
}

get_next_free_ct_id() {
  local out
  if ! out="$(pvesh get /cluster/nextid --output-format text 2>/dev/null)"; then
    die "Failed to query next free CT ID via 'pvesh get /cluster/nextid'. Is this a Proxmox node?"
  fi
  out="$(echo "$out" | tr -d '\r\n \"' | grep -oE '[0-9]+' | head -1)"
  if [[ -z "$out" ]]; then
    die "Could not parse next free CT ID from pvesh output."
  fi
  echo "$out"
}

storage_exists() {
  local name="$1"
  # Portable across Proxmox versions (avoid relying on pvesm subcommands).
  pvesm status 2>/dev/null | awk -v n="$name" 'BEGIN { found = 0 } NR > 1 && $1 == n { found = 1 } END { exit !found }'
}

template_available() {
  local tpl="$1"
  local base
  base="${tpl##*/}"
  if [[ -z "$base" ]]; then
    return 1
  fi
  # Fast path: template cache filename matches OVF artifact basename.
  if [[ -f "/var/lib/vz/template/cache/${base}" ]]; then
    return 0
  fi
  # Fallback: query appliance index for local storage.
  if pveam list local 2>/dev/null | grep -Fq "$base"; then
    return 0
  fi
  return 1
}

ct_exists() {
  pct config "$1" &>/dev/null
}

confirm_destroy() {
  local id="$1"
  if [[ "$LLMCAD_DESTROY_EXISTING" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    die "CT $id already exists and stdin is not a TTY. Set LLMCAD_DESTROY_EXISTING=1 to confirm destroy or choose a different CT_ID."
  fi
  local ans
  read -r -p "[$(date +%H:%M:%S)] CT $id already exists. Destroy it and recreate? [y/N] " ans
  if [[ "${ans,,}" != "y" ]]; then
    log "Aborted by user."
    exit 0
  fi
}

wait_pct_exec_ready() {
  local id="$1"
  local i
  log "Waiting until pct exec works for CT $id..."
  for i in $(seq 1 90); do
    if pct exec "$id" -- true 2>/dev/null; then
      log "pct exec is ready."
      return 0
    fi
    sleep 2
  done
  die "Timed out waiting for pct exec readiness on CT $id."
}

get_ct_ipv4() {
  local id="$1"
  local ip="" raw
  
  raw="$(pct exec "$id" -- hostname -I 2>/dev/null || true)"
  ip="$(echo "$raw" | awk '{print $1}')"
  if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
    echo "$ip"
    return 0
  fi
  
  ip="$(pct exec "$id" -- ip -4 -o addr show eth0 2>/dev/null | head -1 | awk '{print $4}' | cut -d/ -f1 || true)"
  if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
    echo "$ip"
    return 0
  fi
  
  echo ""
}

wait_for_dhcp_ip() {
  local id="$1"
  local i ip
  log "Waiting for a non-empty DHCP IPv4 on CT $id..."
  for i in $(seq 1 120); do
    ip="$(get_ct_ipv4 "$id")"
    if [[ -n "$ip" ]]; then
      log "Detected IPv4: $ip"
      echo "$ip"
      return 0
    fi
    sleep 2
  done
  die "Timed out waiting for DHCP IPv4 on CT $id."
}

remote_test_connectivity() {
  local id="$1"
  local qdrant_probe="${QDRANT_URL%/}/collections"
  local tei_probe="${TEI_URL%/}/health"
  local vllm_probe="${VLLM_URL%/}/models"

  local status_q status_t status_v

  if pct exec "$id" -- env "PROBE_URL=$qdrant_probe" bash -lc 'curl -sf -m 8 "$PROBE_URL" >/dev/null'; then
    status_q="OK"
  else
    status_q="FAIL"
  fi

  if pct exec "$id" -- env "PROBE_URL=$tei_probe" bash -lc 'curl -sf -m 8 "$PROBE_URL" >/dev/null'; then
    status_t="OK"
  else
    status_t="FAIL"
  fi

  if pct exec "$id" -- env "PROBE_URL=$vllm_probe" bash -lc 'curl -sf -m 8 "$PROBE_URL" >/dev/null'; then
    status_v="OK"
  else
    status_v="FAIL"
  fi

  echo "$status_q|$status_t|$status_v"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
  require_root

  if [[ -z "${CT_ID}" ]]; then
    CT_ID="$(get_next_free_ct_id)"
    log "CT_ID not set; using next free ID: $CT_ID"
  fi

  if ! storage_exists "$CT_STORAGE"; then
    die "Storage '${CT_STORAGE}' is missing or not available. Check 'pvesm status'."
  fi

  if ! template_available "$CT_TEMPLATE"; then
    log "Template '${CT_TEMPLATE}' not found. Download it first, for example:"
    log "  pveam update"
    log "  pveam download local debian-13-standard_13.1-1_amd64.tar.zst"
    die "Aborting because the container template is missing."
  fi

  if ct_exists "$CT_ID"; then
    confirm_destroy "$CT_ID"
    log "Destroying existing CT $CT_ID..."
    pct stop "$CT_ID" &>/dev/null || true
    pct destroy "$CT_ID"
  fi

  local root_pw
  if [[ -n "${CT_ROOT_PASSWORD}" ]]; then
    root_pw="$CT_ROOT_PASSWORD"
  else
    root_pw="$(openssl rand -base64 24)"
  fi

  log "Creating CT $CT_ID from template ${CT_TEMPLATE} (rootfs ${CT_STORAGE}:${CT_DISK})..."
  pct create "$CT_ID" "$CT_TEMPLATE" \
    --hostname "$CT_HOSTNAME" \
    --password "$root_pw" \
    --memory "$CT_RAM" \
    --cores "$CT_CORES" \
    --rootfs "${CT_STORAGE}:${CT_DISK}" \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 \
    --features nesting=1 \
    --onboot 1

  log "Starting CT $CT_ID..."
  pct start "$CT_ID"

  wait_pct_exec_ready "$CT_ID"
  local ct_ip
  ct_ip="$(wait_for_dhcp_ip "$CT_ID")"

  log "Updating Debian packages inside CT..."
  pct exec "$CT_ID" -- bash -lc "export DEBIAN_FRONTEND=noninteractive; apt-get update -y && apt-get upgrade -y"

  log "Installing system packages..."
  pct exec "$CT_ID" -- bash -lc "export DEBIAN_FRONTEND=noninteractive; apt-get install -y sudo python3 python3-venv python3-pip libgl1 libglx-mesa0 curl ca-certificates git"

  log "Creating application user '${APP_USER}'..."
  pct exec "$CT_ID" -- bash -lc "id -u '${APP_USER}' &>/dev/null || useradd -m -s /bin/bash '${APP_USER}'"

  log "Cloning LLMCAD repository..."
  pct exec "$CT_ID" -- bash -lc "rm -rf '/home/${APP_USER}/LLMCAD' && sudo -u '${APP_USER}' -H git clone --depth 1 '${GITHUB_REPO}' '/home/${APP_USER}/LLMCAD'"
  pct exec "$CT_ID" -- chmod +x "/home/${APP_USER}/LLMCAD/start.sh"

  log "Installing NVM + Node.js LTS as '${APP_USER}'..."
  pct exec "$CT_ID" -- bash -lc "sudo -u '${APP_USER}' -H bash -lc '
set -euo pipefail
export NVM_DIR=\"/home/${APP_USER}/.nvm\"
curl -fsSL \"https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh\" | bash
# shellcheck disable=SC1090
. \"\$NVM_DIR/nvm.sh\"
nvm install --lts
nvm alias default lts/*
'"

  log "Creating Python venv for cadquery and installing requirements..."
  pct exec "$CT_ID" -- bash -lc "sudo -u '${APP_USER}' -H bash -lc '
set -euo pipefail
export NVM_DIR=\"/home/${APP_USER}/.nvm\"
# shellcheck disable=SC1090
. \"\$NVM_DIR/nvm.sh\" || true
cd \"/home/${APP_USER}/LLMCAD/cadquery\"
python3 -m venv venv
# shellcheck disable=SC1091
. venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
'"

  log "Installing Node dependencies..."
  pct exec "$CT_ID" -- bash -lc "sudo -u '${APP_USER}' -H bash -lc '
set -euo pipefail
export NVM_DIR=\"/home/${APP_USER}/.nvm\"
# shellcheck disable=SC1090
. \"\$NVM_DIR/nvm.sh\"
cd \"/home/${APP_USER}/LLMCAD/node\"
npm install
'"

  log "Preparing .env from .env.example..."
  pct exec "$CT_ID" -- bash -lc "sudo -u '${APP_USER}' -H bash -lc '
set -euo pipefail
cd \"/home/${APP_USER}/LLMCAD\"
cp .env.example .env
'"

  # Patch .env with computed URLs (run as root for sed simplicity; file owned by user).
  pct exec "$CT_ID" -- bash -lc "
set -euo pipefail
ENV_FILE=\"/home/${APP_USER}/LLMCAD/.env\"
sed -i \
  -e \"s|^VLLM_URL=.*|VLLM_URL=${VLLM_URL}|\" \
  -e \"s|^VLLM_BASE_URL=.*|VLLM_BASE_URL=${VLLM_BASE_URL}|\" \
  -e \"s|^VLLM_MODEL=.*|VLLM_MODEL=${VLLM_MODEL}|\" \
  \"\$ENV_FILE\"
chown '${APP_USER}:${APP_USER}' \"\$ENV_FILE\"
"

  log "Optional RAG virtualenv + rag/.env..."
  if pct exec "$CT_ID" -- test -f "/home/${APP_USER}/LLMCAD/rag/requirements.txt"; then
    pct exec "$CT_ID" -- bash -lc "sudo -u '${APP_USER}' -H bash -lc '
set -euo pipefail
cd \"/home/${APP_USER}/LLMCAD/rag\"
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
'"
    pct exec "$CT_ID" -- bash -lc "cat > '/home/${APP_USER}/LLMCAD/rag/.env' <<EOF
QDRANT_URL=${QDRANT_URL}
TEI_URL=${TEI_URL}
QDRANT_COLLECTION=build123d_docs
EOF
chmod 600 '/home/${APP_USER}/LLMCAD/rag/.env'
chown '${APP_USER}:${APP_USER}' '/home/${APP_USER}/LLMCAD/rag/.env'"
  else
    log "No rag/requirements.txt in repo clone; skipping RAG venv."
  fi

  log "Installing systemd unit llmcad.service..."
  pct exec "$CT_ID" -- bash -lc "cat > /etc/systemd/system/llmcad.service <<EOF
[Unit]
Description=LLMCAD (Node + CadQuery) stack
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=/home/${APP_USER}/LLMCAD
ExecStart=/home/${APP_USER}/LLMCAD/start.sh
Restart=on-failure
RestartSec=5
Environment=HOME=/home/${APP_USER}

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable llmcad.service
systemctl restart llmcad.service
"

  log "Waiting briefly for services to bind..."
  sleep 6

  log "Testing outbound connectivity from CT (Qdrant / TEI / vLLM)..."
  local conn_res
  conn_res="$(remote_test_connectivity "$CT_ID")"
  local status_qdrant status_tei status_vllm
  IFS='|' read -r status_qdrant status_tei status_vllm <<<"$conn_res"

  log "---------- SUMMARY ----------"
  log "CT ID:              $CT_ID"
  log "DHCP IPv4:          $ct_ip"
  log "Enter container:    pct enter $CT_ID"
  log "Web UI:             http://${ct_ip}:49157"
  log "Logs (on CT):       journalctl -u llmcad -f"
  log "Remote connectivity:"
  log "  - Qdrant (/collections): $status_qdrant"
  log "  - TEI (/health):         $status_tei"
  log "  - vLLM (/v1/models):     $status_vllm"
  log "-----------------------------"
}

main "$@"
