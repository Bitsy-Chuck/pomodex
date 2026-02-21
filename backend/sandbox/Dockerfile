FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    openssh-server \
    python3 python3-pip python3-venv \
    nodejs npm \
    git curl wget jq unzip \
    fuse3 \
    supervisor \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# ttyd — exposes terminal over WebSocket
# Use TARGETARCH to pick correct binary for build platform
ARG TARGETARCH
RUN TTYD_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x86_64") && \
    wget -O /usr/local/bin/ttyd \
    "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.${TTYD_ARCH}" \
    && chmod +x /usr/local/bin/ttyd

# gcsfuse
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
    https://packages.cloud.google.com/apt gcsfuse-noble main" \
    > /etc/apt/sources.list.d/gcsfuse.list \
    && apt-get update && apt-get install -y gcsfuse \
    && rm -rf /var/lib/apt/lists/*

# rclone
RUN curl https://rclone.org/install.sh | bash

# Claude Code (requires Node.js)
RUN npm install -g @anthropic-ai/claude-code

# agent user
RUN useradd -m -s /bin/bash agent \
    && mkdir -p /home/agent/.ssh \
    && chmod 700 /home/agent/.ssh \
    && chown -R agent:agent /home/agent

RUN mkdir -p /var/run/sshd /var/log/supervisor /opt/agent

COPY config/sshd_config          /etc/ssh/sshd_config
COPY config/supervisord.conf     /etc/supervisor/conf.d/supervisord.conf
COPY scripts/backup_daemon.py    /opt/agent/backup_daemon.py
COPY entrypoint.sh               /entrypoint.sh

RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD supervisorctl status | grep -v RUNNING && exit 1 || exit 0

EXPOSE 22 7681

ENTRYPOINT ["/entrypoint.sh"]
