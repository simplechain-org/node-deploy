#!/usr/bin/env bash
set -euo pipefail

# Source the main script to get environment variables
source /workspace/.env 2>/dev/null || true

workspace=/workspace
basedir=/workspace

# Load genesis configuration
GENESIS_CONFIG=${GENESIS_CONFIG:-testnet}
CONFIG_FILE="/workspace/config-${GENESIS_CONFIG}.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[genesis] Config file not found: $CONFIG_FILE, using default values"
  CONFIG_FILE=""
fi

# Helper function to read YAML config
get_config() {
  local key=$1
  local default=$2
  if [ -n "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ]; then
    # Simple YAML parser (requires yq or fallback to grep/awk)
    if command -v yq >/dev/null 2>&1; then
      yq eval ".${key}" "$CONFIG_FILE" 2>/dev/null || echo "$default"
    else
      # Fallback: simple grep-based parser
      grep -A 1 "^  ${key##*.}:" "$CONFIG_FILE" 2>/dev/null | tail -n 1 | sed 's/.*: *"\?\([^"]*\)"\?.*/\1/' || echo "$default"
    fi
  else
    echo "$default"
  fi
}

# Read configuration with fallback to env vars or defaults
size=$(get_config "validators.cluster_size" "${BSC_CLUSTER_SIZE:-13}")

# Override reset_genesis to skip git operations in container
function reset_genesis() {
    cd /workspace/genesis
    # Skip git operations, genesis files already copied
    if [ ! -f "genesis-template.json" ]; then
        echo "[entrypoint] ERROR: genesis-template.json not found" >&2
        exit 1
    fi
    
    # Install dependencies if not already done
    if [ ! -d ".venv" ]; then
        poetry install --no-root
    fi
    if [ ! -d "node_modules" ]; then
        npm ci
    fi
    
    # Install forge-std if not present
    if [ ! -d "lib/forge-std" ]; then
        forge install --no-git foundry-rs/forge-std@v1.7.3 || true
    fi
}

# Override prepare_config to skip git checkout
function prepare_config() {
    rm -f ${workspace}/genesis/validators.conf

    # Read config values
    CHAIN_ID=$(get_config "chain.chain_id" "${CHAIN_ID:-1913}")
    INIT_HOLDER=$(get_config "init_holders.primary_holder" "${INIT_HOLDER:-0x04d63aBCd2b9b1baa327f2Dda0f873F197ccd186}")
    PASSED_FORK_DELAY=$(get_config "fork.passed_fork_delay" "${PASSED_FORK_DELAY:-60}")
    EnableInitHolderForValidator=$(get_config "init_holders.enable_for_validators" "${EnableInitHolderForValidator:-false}")
    EnableSentryNode=$(get_config "validators.enable_sentry_node" "${EnableSentryNode:-false}")
    EnableFullNode=$(get_config "validators.enable_full_node" "${EnableFullNode:-false}")
    
    passedHardforkTime=$(expr $(date +%s) + ${PASSED_FORK_DELAY})
    echo "passedHardforkTime "${passedHardforkTime} > ${workspace}/.local/hardforkTime.txt
    initHolders=${INIT_HOLDER}
    
    for ((i = 0; i < size; i++)); do
        # Check if validator is a file or directory and extract operator address accordingly
        if [ -f "${workspace}/keys/validator${i}" ]; then
            # Read operator address from the file
            operator_addr=$(cat ${workspace}/keys/validator${i})
        elif [ -d "${workspace}/keys/validator${i}" ]; then
            # Extract operator address from keystore
            for f in ${workspace}/keys/validator${i}/keystore/*; do
                operator_addr="0x$(cat ${f} | jq -r .address)"
            done
        else
            echo "Error: Validator ${i} is neither a file nor a directory"
            exit 1
        fi

        # if [ ${EnableInitHolderForValidator:-false} = true ]; then
        #     initHolders=${initHolders}","${operator_addr}
        # fi

        fee_addr=${operator_addr}

        for f in ${workspace}/.local/consensus${i}/keystore/*; do
            cons_addr="0x$(cat ${f} | jq -r .address)"
        done

        targetDir=${workspace}/.local/node${i}
        mkdir -p ${targetDir} && cd ${targetDir}
        cp ${workspace}/keys/password.txt ./
        cp ${workspace}/.local/hardforkTime.txt ./
        bbcfee_addrs=${fee_addr}
        powers="0x000001d1a94a2000" #2000000000000
        mv ${workspace}/.local/bls${i}/bls ./ && rm -rf ${workspace}/.local/bls${i}
        vote_addr=0x$(cat ./bls/keystore/*json | jq .pubkey | sed 's/"//g')
        echo "${cons_addr},${bbcfee_addrs},${fee_addr},${powers},${vote_addr}" >> ${workspace}/genesis/validators.conf
        if [ ${EnableSentryNode:-false} = true ]; then
            mkdir -p ${workspace}/.local/sentry${i}
        fi
    done
    if [ ${EnableFullNode:-false} = true ]; then
        mkdir -p ${workspace}/.local/fullnode0
    fi
    rm -f ${workspace}/.local/hardforkTime.txt

    cd ${workspace}/genesis/
    # Skip: git checkout HEAD contracts
    # Directly modify contracts
    sed -i -e  's/alreadyInit = true;/turnLength = 16;alreadyInit = true;/' ${workspace}/genesis/contracts/SPCValidatorSet.sol
    sed -i -e  's/public onlyCoinbase onlyZeroGasPrice {/public onlyCoinbase onlyZeroGasPrice {if (block.number < 300) return;/' ${workspace}/genesis/contracts/SPCValidatorSet.sol
    
    # Read all genesis generation parameters from config
    INIT_BURN_RATIO=$(get_config "slash.init_burn_ratio" "500")
    INIT_FELONY_SLASH_SCOPE=$(get_config "slash.init_felony_slash_scope" "60")
    BREATHE_BLOCK_INTERVAL=$(get_config "chain.breathe_block_interval" "10 minutes")
    BLOCK_INTERVAL=$(get_config "chain.block_interval" "3 seconds")
    STAKE_HUB_PROTECTOR=$(get_config "staking.stake_hub_protector" "${INIT_HOLDER}")
    UNBOND_PERIOD=$(get_config "staking.unbond_period" "2 minutes")
    DOWNTIME_JAIL_TIME=$(get_config "jail.downtime_jail_time" "2 minutes")
    FELONY_JAIL_TIME=$(get_config "jail.felony_jail_time" "3 minutes")
    MISDEMEANOR_THRESHOLD=$(get_config "slash.misdemeanor_threshold" "50")
    FELONY_THRESHOLD=$(get_config "slash.felony_threshold" "150")
    INIT_VOTING_DELAY=$(get_config "governance.init_voting_delay" "1 minutes / BLOCK_INTERVAL")
    INIT_VOTING_PERIOD=$(get_config "governance.init_voting_period" "2 minutes / BLOCK_INTERVAL")
    INIT_MIN_PERIOD_AFTER_QUORUM=$(get_config "governance.init_min_period_after_quorum" "uint64(1 minutes / BLOCK_INTERVAL)")
    GOVERNOR_PROTECTOR=$(get_config "governance.governor_protector" "${INIT_HOLDER}")
    INIT_MINIMAL_DELAY=$(get_config "governance.init_minimal_delay" "1 minutes")
    TOKEN_RECOVER_PROTECTOR=$(get_config "token_recovery.protector" "${INIT_HOLDER}")
    
    echo "[genesis] Using configuration: $GENESIS_CONFIG"
    echo "[genesis] Chain ID: $CHAIN_ID"
    echo "[genesis] Validator cluster size: $size"
    
    poetry run python -m scripts.generate generate-validators
    poetry run python -m scripts.generate generate-init-holders "${initHolders}"
    poetry run python -m scripts.generate dev \
      --dev-chain-id "${CHAIN_ID}" \
      --init-burn-ratio "${INIT_BURN_RATIO}" \
      --init-felony-slash-scope "${INIT_FELONY_SLASH_SCOPE}" \
      --breathe-block-interval "${BREATHE_BLOCK_INTERVAL}" \
      --block-interval "${BLOCK_INTERVAL}" \
      --stake-hub-protector "${STAKE_HUB_PROTECTOR}" \
      --unbond-period "${UNBOND_PERIOD}" \
      --downtime-jail-time "${DOWNTIME_JAIL_TIME}" \
      --felony-jail-time "${FELONY_JAIL_TIME}" \
      --misdemeanor-threshold "${MISDEMEANOR_THRESHOLD}" \
      --felony-threshold "${FELONY_THRESHOLD}" \
      --init-voting-delay "${INIT_VOTING_DELAY}" \
      --init-voting-period "${INIT_VOTING_PERIOD}" \
      --init-min-period-after-quorum "${INIT_MIN_PERIOD_AFTER_QUORUM}" \
      --governor-protector "${GOVERNOR_PROTECTOR}" \
      --init-minimal-delay "${INIT_MINIMAL_DELAY}" \
      --token-recover-portal-protector "${TOKEN_RECOVER_PROTECTOR}"
}

# Override create_validator
function create_validator() {
    rm -rf ${workspace}/.local
    mkdir -p ${workspace}/.local

    for ((i = 0; i < size; i++)); do
        # Copy validator keys
        if [ -f "${workspace}/keys/validator${i}" ]; then
            cp ${workspace}/keys/validator${i} ${workspace}/.local/
        elif [ -d "${workspace}/keys/validator${i}" ]; then
            cp -r ${workspace}/keys/validator${i} ${workspace}/.local/
        fi
        cp -r ${workspace}/keys/consensus${i} ${workspace}/.local/
        cp -r ${workspace}/keys/bls${i} ${workspace}/.local/
    done
}

# Export functions so bsc_cluster.sh can use them
export -f reset_genesis
export -f prepare_config
export -f create_validator

# Run only the necessary parts for genesis generation
cd /workspace
create_validator
reset_genesis
prepare_config

# Copy output
if [ -f /workspace/genesis/genesis.json ]; then
    cp /workspace/genesis/genesis.json /workspace/genesis-out/genesis.json
    echo "[entrypoint] genesis.json generated successfully"
else
    echo "[entrypoint] ERROR: genesis.json not generated" >&2
    exit 1
fi

