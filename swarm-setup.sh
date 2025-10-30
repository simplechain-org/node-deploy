#!/bin/bash
set -e

# BSC Cluster Docker Swarm Setup Script

MANAGER_IP=${1:-}
WORKER_IPS=${2:-}

echo "=== BSC Cluster Docker Swarm Setup ==="

if [ -z "$MANAGER_IP" ]; then
  echo "Usage: $0 <manager-ip> <worker-ips-comma-separated>"
  echo "Example: $0 192.168.0.103 192.168.0.204,192.168.0.55"
  exit 1
fi

# 1. Initialize Swarm on manager node
echo "[1/5] Initializing Docker Swarm on manager node: $MANAGER_IP"
ssh root@$MANAGER_IP "docker swarm init --advertise-addr $MANAGER_IP" || echo "Swarm already initialized"

# 2. Get join token
echo "[2/5] Getting worker join token..."
JOIN_TOKEN=$(ssh root@$MANAGER_IP "docker swarm join-token worker -q")
JOIN_CMD="docker swarm join --token $JOIN_TOKEN $MANAGER_IP:2377"

# 3. Join worker nodes
if [ -n "$WORKER_IPS" ]; then
  echo "[3/5] Adding worker nodes..."
  IFS=',' read -ra WORKERS <<< "$WORKER_IPS"
  for WORKER_IP in "${WORKERS[@]}"; do
    echo "  Adding worker: $WORKER_IP"
    ssh root@$WORKER_IP "$JOIN_CMD" || echo "  Node already joined"
  done
else
  echo "[3/5] No worker nodes specified, skipping..."
fi

# 4. Label nodes (for placement constraints)
echo "[4/5] Labeling nodes..."
ssh root@$MANAGER_IP "docker node update --label-add role=manager $(docker node ls -q --filter role=manager | head -n 1)"

# Label worker nodes by hostname
if [ -n "$WORKER_IPS" ]; then
  INDEX=1
  for WORKER_IP in "${WORKERS[@]}"; do
    NODE_ID=$(ssh root@$MANAGER_IP "docker node ls --format '{{.Hostname}} {{.ID}}' | grep $WORKER_IP | awk '{print \$2}'")
    if [ -n "$NODE_ID" ]; then
      ssh root@$MANAGER_IP "docker node update --label-add validator-index=$INDEX $NODE_ID"
    fi
    INDEX=$((INDEX + 1))
  done
fi

# 5. Deploy stack
echo "[5/5] Ready to deploy stack"
echo ""
echo "Next steps:"
echo "1. Copy project files to manager node: rsync -avz ./ root@$MANAGER_IP:~/node-deploy/"
echo "2. SSH to manager: ssh root@$MANAGER_IP"
echo "3. Deploy stack: cd ~/node-deploy && docker stack deploy -c docker-stack.yml bsc-cluster"
echo "4. Check services: docker service ls"
echo "5. View logs: docker service logs -f bsc-cluster_bsc-validator-1"
echo ""
echo "Swarm setup completed!"

