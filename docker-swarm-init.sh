#!/usr/bin/env bash
set -e

# Docker Swarm 容器初始化脚本
# 用于在容器启动时设置 entrypoint、解压密钥等

echo "[swarm-init] Starting container initialization..."

# 1. Setup entrypoint script
echo "[swarm-init] Setting up entrypoint..."
if [ -f /entrypoint/docker-entrypoint.sh ]; then
    cp /entrypoint/docker-entrypoint.sh /usr/local/bin/
    chmod +x /usr/local/bin/docker-entrypoint.sh
    echo "[swarm-init] ✅ Entrypoint ready"
else
    echo "[swarm-init] ❌ Error: /entrypoint/docker-entrypoint.sh not found" >&2
    exit 1
fi

# 2. Extract validator keys from tar archive
echo "[swarm-init] Extracting validator keys..."
if [ -f /tmp/keys.tar.gz ]; then
    mkdir -p /home/sipc2/keys
    tar -xzf /tmp/keys.tar.gz -C /home/sipc2/keys
    echo "[swarm-init] ✅ Keys extracted to /home/sipc2/keys/"
    
    # List extracted contents for debugging
    echo "[swarm-init] Extracted files:"
    ls -la /home/sipc2/keys/ | head -n 10 | sed 's/^/  /'
else
    echo "[swarm-init] ❌ Error: /tmp/keys.tar.gz not found" >&2
    exit 1
fi

# 3. Copy password file
echo "[swarm-init] Setting up password file..."
if [ -f /tmp/password.txt ]; then
    cp /tmp/password.txt /home/sipc2/keys/password.txt
    echo "[swarm-init] ✅ Password file ready"
else
    echo "[swarm-init] ⚠️  Warning: /tmp/password.txt not found"
fi

# 4. Copy nodekey
echo "[swarm-init] Setting up nodekey..."
if [ -f /tmp/nodekey ]; then
    cp /tmp/nodekey /home/sipc2/keys/nodekey
    echo "[swarm-init] ✅ Nodekey ready"
else
    echo "[swarm-init] ❌ Error: /tmp/nodekey not found" >&2
    exit 1
fi

# 5. Fix permissions
echo "[swarm-init] Fixing permissions..."
chown -R sipc2:sipc2 /home/sipc2/keys 2>/dev/null || true
echo "[swarm-init] ✅ Permissions fixed"

# 6. Verify key structure
echo "[swarm-init] Verifying key structure..."
VALIDATOR_INDEX=${VALIDATOR_INDEX:-0}
EXPECTED_CONSENSUS_DIR="/home/sipc2/keys/consensus${VALIDATOR_INDEX}"
EXPECTED_BLS_DIR="/home/sipc2/keys/bls${VALIDATOR_INDEX}"

if [ -d "${EXPECTED_CONSENSUS_DIR}/keystore" ]; then
    echo "[swarm-init] ✅ Consensus keystore found at ${EXPECTED_CONSENSUS_DIR}/keystore/"
else
    echo "[swarm-init] ⚠️  Warning: Consensus keystore not found at ${EXPECTED_CONSENSUS_DIR}/keystore/"
fi

if [ -d "${EXPECTED_BLS_DIR}/bls" ]; then
    echo "[swarm-init] ✅ BLS keys found at ${EXPECTED_BLS_DIR}/bls/"
else
    echo "[swarm-init] ⚠️  Warning: BLS keys not found at ${EXPECTED_BLS_DIR}/bls/"
fi

echo "[swarm-init] =========================================="
echo "[swarm-init] Initialization complete!"
echo "[swarm-init] Starting main entrypoint..."
echo "[swarm-init] =========================================="

# 7. Execute main entrypoint
exec /usr/local/bin/docker-entrypoint.sh "$@"

