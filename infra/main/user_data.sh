#!/bin/bash
# First-boot setup of the smart gardening server (Amazon Linux 2023, x86_64).
# Runs once, as root, via cloud-init. Log: /var/log/cloud-init-output.log
# Changes here do NOT reach the existing instance (lifecycle.ignore_changes in server.tf).
set -euo pipefail

# Docker Compose plugin: not packaged for Amazon Linux 2023, so install the release binary,
# pinned and verified by checksum.
COMPOSE_VERSION="v5.7.1"
COMPOSE_SHA256="af426722a8af5de812fcee30cc1c39f0639f5f723cbadefb25e5d4db054064cf"

dnf install -y docker
systemctl enable --now docker

plugin_dir=/usr/local/lib/docker/cli-plugins
mkdir -p "$plugin_dir"
curl -fsSL --retry 5 -o "$plugin_dir/docker-compose" \
  "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64"
echo "$COMPOSE_SHA256  $plugin_dir/docker-compose" | sha256sum -c -
chmod 0755 "$plugin_dir/docker-compose"
docker compose version

# Working directory for the compose project (database, API).
install -d -m 0755 /opt/smart-gardening
