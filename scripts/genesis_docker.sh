#!/usr/bin/env bash
set -euo pipefail

BASEDIR=$(cd "$(dirname "$0")" && cd .. && pwd)
cd "$BASEDIR"

IMAGE_TAG=${IMAGE_TAG:-genesis-builder:local}
DOCKERFILE_PATH="docker/genesis-builder/Dockerfile"

OUT_DIR="${BASEDIR}/genesis-out"
mkdir -p "$OUT_DIR"

echo "[genesis] 构建镜像: $IMAGE_TAG"
docker build -f "$DOCKERFILE_PATH" -t "$IMAGE_TAG" .

echo "[genesis] 运行容器生成 genesis.json"
docker run --rm \
  -v "$OUT_DIR":/workspace/genesis-out \
  "$IMAGE_TAG" "bash -lc 'bash /workspace/bsc_cluster.sh regen-genesis && cp /workspace/genesis/genesis.json /workspace/genesis-out/genesis.json'"

if [ ! -s "$OUT_DIR/genesis.json" ]; then
  echo "[genesis] 生成失败：未找到 genesis.json" >&2
  exit 1
fi

echo "[genesis] 生成完成: $OUT_DIR/genesis.json"


