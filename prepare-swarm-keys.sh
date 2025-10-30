#!/bin/bash

# Prepare keys tar packages for Docker Swarm
# Automatically detect validators to package based on docker-stack.yml configuration

set -e

STACK_FILE="${1:-docker-stack.yml}"

echo "=========================================="
echo "Preparing Docker Swarm Keys Packaging"
echo "=========================================="
echo "Using config file: $STACK_FILE"

# Check if stack file exists
if [ ! -f "$STACK_FILE" ]; then
    echo "  ❌ Config file not found: $STACK_FILE"
    echo ""
    echo "Usage: $0 [docker-stack.yml]"
    exit 1
fi

# Extract validator configuration from docker-stack.yml
echo ""
echo "[1/4] Parsing validator configuration from $STACK_FILE..."

VALIDATORS=()

# Find all validatorN-keys-tar configurations
while IFS= read -r line; do
    # Match validator0-keys-tar, validator1-keys-tar, etc.
    if [[ "$line" =~ validator([0-9]+)-keys-tar ]]; then
        index="${BASH_REMATCH[1]}"
        VALIDATORS+=($index)
    fi
done < "$STACK_FILE"

if [ ${#VALIDATORS[@]} -eq 0 ]; then
    echo "  ❌ No validatorN-keys-tar configuration found in $STACK_FILE"
    echo ""
    echo "Please ensure the configs section contains entries like:"
    echo "  validatorN-keys-tar:"
    echo "    file: ./keys/validatorN-keys.tar.gz"
    exit 1
fi

# Deduplicate and sort
VALIDATORS=($(printf '%s\n' "${VALIDATORS[@]}" | sort -nu))

echo "  ✅ Found ${#VALIDATORS[@]} validator(s) in config: ${VALIDATORS[*]}"

# Check required directories and files
echo ""
echo "[2/4] Checking required directories and files..."

ALL_GOOD=true
for i in "${VALIDATORS[@]}"; do
    echo "  Checking validator${i}..."
    
    # Check directories
    if [ ! -d "keys/consensus${i}/keystore" ]; then
        echo "    ❌ Missing directory: keys/consensus${i}/keystore"
        ALL_GOOD=false
    fi
    
    if [ ! -d "keys/bls${i}/bls" ]; then
        echo "    ❌ Missing directory: keys/bls${i}/bls"
        ALL_GOOD=false
    fi
    
    # Check keystore files
    if ! ls keys/consensus${i}/keystore/UTC--* >/dev/null 2>&1; then
        echo "    ❌ No keystore files in keys/consensus${i}/keystore/"
        ALL_GOOD=false
    fi
    
    # Check nodekey
    if [ ! -f "keys/validator-nodekey${i}" ]; then
        echo "    ❌ Missing file: keys/validator-nodekey${i}"
        ALL_GOOD=false
    fi
    
    if [ "$ALL_GOOD" = true ]; then
        echo "    ✅ validator${i} all files present"
    fi
done

# Check common files
if [ ! -f "keys/password.txt" ]; then
    echo "  ❌ Missing file: keys/password.txt"
    ALL_GOOD=false
else
    echo "  ✅ keys/password.txt exists"
fi

if [ "$ALL_GOOD" = false ]; then
    echo ""
    echo "❌ Some files are missing, please check and retry"
    exit 1
fi

# Package keys for all validators
echo ""
echo "[3/4] Packaging validator keys..."

cd keys
for i in "${VALIDATORS[@]}"; do
    echo "  Packaging validator${i}..."
    
    tar -czf "validator${i}-keys.tar.gz" \
        "consensus${i}/keystore/" \
        "bls${i}/bls/"
    
    if [ $? -eq 0 ]; then
        echo "    ✅ Created keys/validator${i}-keys.tar.gz ($(du -h "validator${i}-keys.tar.gz" | cut -f1))"
    else
        echo "    ❌ Packaging failed"
        cd ..
        exit 1
    fi
done
cd ..

# Verify tar package contents
echo ""
echo "[4/4] Verifying package contents..."

for i in "${VALIDATORS[@]}"; do
    echo ""
    echo "  validator${i}-keys.tar.gz contents:"
    tar -tzf "keys/validator${i}-keys.tar.gz" | sed 's/^/    /'
done

echo ""
echo "=========================================="
echo "✅ Keys packaging complete!"
echo "=========================================="
echo ""
echo "Generated files:"
for i in "${VALIDATORS[@]}"; do
    echo "  - keys/validator${i}-keys.tar.gz"
done
echo ""
echo "These files will be automatically distributed to nodes via Docker Config"
echo "No need to rename any keystore or BLS key files!"
echo ""
echo "Next steps:"
echo "  1. Ensure genesis.json is generated:"
echo "     ls -lh genesis-out/genesis.json"
echo ""
echo "  2. Deploy Swarm stack:"
echo "     docker stack deploy -c $STACK_FILE bsc-cluster"
echo ""
echo "Tips:"
echo "  - Script packages validators based on validatorN-keys-tar config in $STACK_FILE"
echo "  - To package other validators, add corresponding config in $STACK_FILE first"
echo "  - You can specify a different config file: $0 custom-stack.yml"
echo ""

