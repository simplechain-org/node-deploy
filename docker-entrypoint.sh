#!/usr/bin/env bash
set -euo pipefail

#
# Expected mounts:
# - /home/sipc2/config/config.toml
# - /home/sipc2/config/genesis.json  (or /genesis-out/genesis.json which will be copied)
# - /home/sipc2/keys/password.txt
# - /home/sipc2/keys/consensus/keystore/*
# - /home/sipc2/keys/bls/wallet (optional if validator)
# - /home/sipc2/keys/nodekey
# - /data (container data dir)
#

NODE_TYPE=${NODE_TYPE:-fullnode}
GCMODE=${GCMODE:-full}

# Load genesis configuration
GENESIS_CONFIG=${GENESIS_CONFIG:-testnet}
CONFIG_FILE="/home/sipc2/config/genesis-config-${GENESIS_CONFIG}.yaml"

# Helper function to read YAML config (simple grep-based parser)
get_config() {
  local key=$1
  local default=$2
  if [ -f "$CONFIG_FILE" ]; then
    local value=$(grep -A 1 "^  ${key##*.}:" "$CONFIG_FILE" 2>/dev/null | tail -n 1 | sed 's/.*: *"\?\([^"]*\)"\?.*/\1/')
    echo "${value:-$default}"
  else
    echo "$default"
  fi
}

# Read key chain parameters from config
CHAIN_ID=$(get_config "chain_id" "1913")
BREATHE_BLOCK_INTERVAL=$(get_config "breathe_block_interval_seconds" "600")

echo "[entrypoint] Using genesis config: ${GENESIS_CONFIG}"
echo "[entrypoint] Chain ID: ${CHAIN_ID}"
echo "[entrypoint] Breathe block interval: ${BREATHE_BLOCK_INTERVAL}s"

# Fix permissions for mounted volumes (run as root first)
mkdir -p /home/sipc2/config /home/sipc2/keys /data
chown -R sipc2:sipc2 /home/sipc2 /data 2>/dev/null || true

# Switch to sipc2 user for all subsequent commands
if [ "$(id -u)" = "0" ]; then
  # Use su-exec if available, otherwise fall back to su
  if command -v su-exec >/dev/null 2>&1; then
    exec su-exec sipc2 "$0" "$@"
  elif command -v gosu >/dev/null 2>&1; then
    exec gosu sipc2 "$0" "$@"
  else
    # Fall back to su with proper shell invocation
    exec su -s /bin/bash sipc2 -c "exec \"\$0\" \"\$@\"" -- "$0" "$@"
  fi
fi

# If genesis not present but mounted volume provides it, copy
if [ ! -f /home/sipc2/config/genesis.json ] && [ -f /genesis-out/genesis.json ]; then
  cp /genesis-out/genesis.json /home/sipc2/config/genesis.json
fi

if [ ! -f /home/sipc2/config/genesis.json ]; then
  echo "[entrypoint] Missing /home/sipc2/config/genesis.json" >&2
  exit 1
fi

# Initialize datadir on first run
if [ ! -d /data/geth/geth ] && [ ! -d /data/geth/chaindata ]; then
  echo "[entrypoint] init datadir with genesis"
  geth --datadir /data init --state.scheme path --db.engine pebble /home/sipc2/config/genesis.json
fi

if [ "$NODE_TYPE" = "archive" ]; then
  GCMODE="archive"
fi
COMMON_FLAGS=(
  --config /home/sipc2/config/config.toml
  --datadir /data
  --nodekey /home/sipc2/keys/nodekey
  --rpc.allow-unprotected-txs --allow-insecure-unlock
  --ws.addr 0.0.0.0 --ws.port 8546 --http.addr 0.0.0.0 --http.port 8545 --http.corsdomain "*"
  --metrics --metrics.addr localhost --metrics.port 9000 --metrics.expensive
  --pprof --pprof.addr localhost --pprof.port 6060
  --gcmode $GCMODE --syncmode full
  --monitor.maliciousvote
  --override.breatheblockinterval $BREATHE_BLOCK_INTERVAL
  --override.passedforktime 1725500000
  --override.lorentz 1725500000
  --override.maxwell 1725500000
  --override.immutabilitythreshold 2048
  --override.minforblobrequest 576
  --override.defaultextrareserve 32
)

if [ "$NODE_TYPE" = "validator" ]; then
  # Get validator index (default to 0 if not set)
  VALIDATOR_INDEX=${VALIDATOR_INDEX:-0}
  CONSENSUS_DIR="/home/sipc2/keys/consensus${VALIDATOR_INDEX}"
  BLS_DIR="/home/sipc2/keys/bls${VALIDATOR_INDEX}"
  
  echo "[entrypoint] Validator index: ${VALIDATOR_INDEX}"
  echo "[entrypoint] Looking for consensus keystore in: ${CONSENSUS_DIR}/keystore/"
  
  # Try derive from consensus keystore
  CONSENSUS_ADDRESS=""
  if ls ${CONSENSUS_DIR}/keystore/* >/dev/null 2>&1; then
    CONS_ADDR_RAW=$(cat ${CONSENSUS_DIR}/keystore/* | jq -r .address | head -n1)
    CONSENSUS_ADDRESS="0x${CONS_ADDR_RAW}"
    echo "[entrypoint] Found consensus address: ${CONSENSUS_ADDRESS}"
  fi
  
  if [ -z "$CONSENSUS_ADDRESS" ]; then
    echo "[entrypoint] validator requires CONSENSUS_ADDRESS" >&2
    echo "[entrypoint] No keystore found in ${CONSENSUS_DIR}/keystore/" >&2
    exit 1
  fi

  BLS_FLAGS=()
  if [ -d "${BLS_DIR}/bls" ]; then
    echo "[entrypoint] Found BLS directory: ${BLS_DIR}/bls"
    BLS_FLAGS+=(--blspassword /home/sipc2/keys/password.txt)
    BLS_FLAGS+=(--blswallet ${BLS_DIR}/bls/wallet)
  else
    echo "[entrypoint] Warning: BLS directory not found at ${BLS_DIR}/bls"
  fi

  exec geth \
    "${COMMON_FLAGS[@]}" \
    --mine --vote \
    --password /home/sipc2/keys/password.txt \
    --unlock "$CONSENSUS_ADDRESS" \
    --miner.etherbase "$CONSENSUS_ADDRESS" \
    --keystore ${CONSENSUS_DIR}/keystore \
    "${BLS_FLAGS[@]}" \
    "$@"
else
  exec geth "${COMMON_FLAGS[@]}" "$@"
fi


