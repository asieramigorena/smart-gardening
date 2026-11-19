#!/usr/bin/env bash
# Deploy (or update) the server stack from a branch of the repository.
#
#   sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh [branch]   # default: main
#
# 1. installs git, the AWS CLI and jq if missing (dnf, Amazon Linux 2023)
# 2. clones the repo into /opt/smart-gardening/repo, or fast-forwards it to origin/<branch>
# 3. refreshes /opt/smart-gardening/.env from SSM (fetch-secrets.sh)
# 4. docker compose up -d --build
set -euo pipefail

BRANCH="${1:-main}"
BASE=/opt/smart-gardening
REPO_DIR="$BASE/repo"
REPO_URL="${REPO_URL:-https://github.com/asieramigorena/smart-gardening.git}"
ENV_FILE="$BASE/.env"

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0 $*" >&2
  exit 1
fi

need=()
command -v git >/dev/null || need+=(git)
command -v aws >/dev/null || need+=(awscli-2)
command -v jq >/dev/null || need+=(jq)
if ((${#need[@]})); then
  echo "installing: ${need[*]}"
  dnf install -y "${need[@]}"
fi
command -v docker >/dev/null || { echo "docker is not installed (see infra/main/user_data.sh)" >&2; exit 1; }
docker compose version >/dev/null || { echo "docker compose plugin is missing" >&2; exit 1; }

mkdir -p "$BASE"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
  git -C "$REPO_DIR" fetch --prune origin "$BRANCH"
  git -C "$REPO_DIR" checkout -q "$BRANCH" 2>/dev/null \
    || git -C "$REPO_DIR" checkout -q -b "$BRANCH" "origin/$BRANCH"
  # Fails (instead of merging) if someone changed files on the server.
  git -C "$REPO_DIR" merge --ff-only "origin/$BRANCH"
fi
echo "deploying $BRANCH at $(git -C "$REPO_DIR" rev-parse --short HEAD)"

"$REPO_DIR/infra/server/scripts/fetch-secrets.sh" "$ENV_FILE"

cd "$REPO_DIR/infra/server"
docker compose --env-file "$ENV_FILE" up -d --build --remove-orphans
docker compose --env-file "$ENV_FILE" ps
