#!/usr/bin/env bash
# Deploy (or update) the server stack from a branch of the repository.
#
#   sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh [branch]   # default: main
#
# 1. installs git, the AWS CLI and jq if missing (dnf, Amazon Linux 2023)
# 2. installs a pinned docker buildx if the one present is missing or older than 0.17.0
#    (`docker compose build` needs it; the Amazon Linux docker package ships 0.12)
# 3. clones the repo into /opt/smart-gardening/repo, or fast-forwards it to origin/<branch>
# 4. refreshes /opt/smart-gardening/.env from SSM (fetch-secrets.sh)
# 5. docker compose up -d --build
set -euo pipefail

BRANCH="${1:-main}"
BASE=/opt/smart-gardening
REPO_DIR="$BASE/repo"
REPO_URL="${REPO_URL:-https://github.com/asieramigorena/smart-gardening.git}"
ENV_FILE="$BASE/.env"
# Pinned buildx, installed only if the present one is missing or older than BUILDX_MIN.
# To upgrade, change the version: the binary is checked against the release's checksums.txt.
BUILDX_VERSION="${BUILDX_VERSION:-v0.38.2}"
BUILDX_MIN=0.17.0
BUILDX_PLUGIN=/usr/local/lib/docker/cli-plugins/docker-buildx

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

# Version of the buildx the docker CLI uses (e.g. "0.12.1"), empty if there is none.
buildx_version() {
  docker buildx version 2>/dev/null | awk '{print $2}' | sed -E 's/^v//; s/[-+].*$//'
}

# True if version $1 >= version $2.
version_ge() {
  [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$2" ]]
}

current=$(buildx_version)
if [[ -n "$current" ]] && version_ge "$current" "$BUILDX_MIN"; then
  echo "docker buildx $current"
else
  [[ "$(uname -m)" == x86_64 ]] || { echo "buildx install supports x86_64 only, not $(uname -m)" >&2; exit 1; }
  echo "docker buildx ${current:-missing} < $BUILDX_MIN: installing $BUILDX_VERSION"
  asset="buildx-$BUILDX_VERSION.linux-amd64"
  url="https://github.com/docker/buildx/releases/download/$BUILDX_VERSION"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --retry 5 -o "$tmp/$asset" "$url/$asset"
  curl -fsSL --retry 5 -o "$tmp/checksums.txt" "$url/checksums.txt"
  # checksums.txt lines are "<sha256> *<file>"; exactly one must name the binary.
  awk -v f="$asset" '$2 == f || $2 == "*" f' "$tmp/checksums.txt" > "$tmp/expected"
  if [[ $(wc -l < "$tmp/expected") -ne 1 ]]; then
    echo "$asset not found in checksums.txt" >&2
    exit 1
  fi
  (cd "$tmp" && sha256sum --check --strict expected)
  install -D -m 0755 "$tmp/$asset" "$BUILDX_PLUGIN"
  rm -rf "$tmp"
  trap - EXIT
  current=$(buildx_version)
  if [[ -z "$current" ]] || ! version_ge "$current" "$BUILDX_MIN"; then
    echo "docker still uses buildx ${current:-none} after installing $BUILDX_PLUGIN" >&2
    exit 1
  fi
  echo "docker buildx $current installed in $BUILDX_PLUGIN"
fi

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
