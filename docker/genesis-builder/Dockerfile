FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=18 \
    POETRY_VERSION=1.8.3 \
    FOUNDRY_RELEASE=nightly-43c9dc8 \
    GO_VERSION=1.22.6

# pip install from tsinghua mirror,only for china users
RUN mkdir -p /root/.pip

RUN echo "[global]" > /root/.pip/pip.conf && \
    echo "index-url = https://pypi.tuna.tsinghua.edu.cn/simple" >> /root/.pip/pip.conf && \
    echo "trusted-host = pypi.tuna.tsinghua.edu.cn" >> /root/.pip/pip.conf

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
       curl wget git jq python3 python3-pip python3-venv build-essential \
       pkg-config libssl-dev openssl unzip xz-utils gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js via official binary (avoid external apt repo signature issues)
RUN ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then NODE_ARCH=x64; \
       elif [ "$ARCH" = "arm64" ]; then NODE_ARCH=arm64; \
       else echo "Unsupported arch: $ARCH" && exit 1; fi \
    && curl -fsSL https://nodejs.org/dist/v18.20.4/node-v18.20.4-linux-${NODE_ARCH}.tar.xz -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm -f /tmp/node.tar.xz \
    && node -v && npm -v \
    && npm i -g pnpm@8 yarn@1

# Poetry
RUN pip3 install --no-cache-dir poetry==${POETRY_VERSION}

# Foundry (forge/cast/anvil)
RUN curl -L https://foundry.paradigm.xyz | bash -s -- --default-toolchain ${FOUNDRY_RELEASE} \
    && /root/.foundry/bin/foundryup
ENV PATH="/root/.foundry/bin:${PATH}"

# Go
RUN curl -fsSL https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz -o /tmp/go.tgz \
    && tar -C /usr/local -xzf /tmp/go.tgz \
    && rm -f /tmp/go.tgz
ENV PATH="/usr/local/go/bin:${PATH}"

WORKDIR /workspace

# Cache-friendly: copy only genesis submodule and minimal files
COPY genesis /workspace/genesis
COPY bsc_cluster.sh /workspace/bsc_cluster.sh
COPY keys /workspace/keys
COPY config.toml /workspace/config.toml
COPY .env /workspace/.env

RUN chmod +x /workspace/bsc_cluster.sh

# Prepare genesis module deps
WORKDIR /workspace/genesis
RUN poetry env use python3 && poetry config installer.max-workers 4 && poetry install --no-root \
    && npm ci \
    && /root/.foundry/bin/forge --version

WORKDIR /workspace

# Copy custom entrypoint that skips git operations
COPY docker/genesis-builder/entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["/workspace/entrypoint.sh"]


