#!/bin/bash
# Installs on a Pi the IoT credentials Terraform created (SSM) and its node.env.
# Run from a machine with the smart-gardening AWS profile: ./provision.sh <ssh-host> <device-id> <zone>
# Assisted by AI
set -euo pipefail

usage() { echo "usage: $0 <ssh-host> <device-id> <zone>" >&2; exit 64; }
[ $# -eq 3 ] || usage
HOST="$1" DEVICE="$2" ZONE="$3"

export AWS_PROFILE="${AWS_PROFILE:-smart-gardening}"
export AWS_REGION="${AWS_REGION:-eu-south-2}"
ROOT_CA_URL="https://www.amazontrust.com/repository/AmazonRootCA1.pem"
CONF=/etc/smart-gardening

# Patterns of docs/contracts/README.md (device_id and zone)
[[ "$DEVICE" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || { echo "invalid device id '$DEVICE'" >&2; exit 64; }
[[ "$ZONE" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]] || { echo "invalid zone '$ZONE' (lowercase, see docs/contracts)" >&2; exit 64; }
for cmd in aws ssh scp curl; do
    command -v "$cmd" >/dev/null || { echo "missing command: $cmd" >&2; exit 1; }
done

umask 077
LOCAL=$(mktemp -d)
trap 'rm -rf "$LOCAL"' EXIT  # the private key never stays on this machine
param() { aws ssm get-parameter --name "/smart-gardening/iot/$1" --with-decryption --query Parameter.Value --output text; }

echo "1/4  AWS access ($AWS_PROFILE, $AWS_REGION)"
aws sts get-caller-identity --query Arn --output text

echo "2/4  Credentials of $DEVICE (SSM, created by Terraform)"
if ! param "$DEVICE/certificate_pem" > "$LOCAL/$DEVICE.cert.pem" 2>/dev/null; then
    echo "     no certificate for '$DEVICE' in SSM: add it to var.devices in infra/ and apply" >&2
    exit 1
fi
param "$DEVICE/private_key" > "$LOCAL/$DEVICE.private.key"
curl -fsSL "$ROOT_CA_URL" -o "$LOCAL/AmazonRootCA1.pem"
printf 'IOT_ENDPOINT=%s\nTHING_NAME=%s\nZONE=%s\n' "$(param endpoint)" "$DEVICE" "$ZONE" > "$LOCAL/node.env"

echo "3/4  Copy to $HOST"
REMOTE_TMP=$(ssh "$HOST" 'mktemp -d')
scp -q "$LOCAL"/* "$HOST:$REMOTE_TMP/"

echo "4/4  Install in $CONF on $HOST (sudo)"
ssh -t "$HOST" "set -e; u=\$(id -un)
    sudo install -d -o \"\$u\" -g \"\$u\" -m 700 $CONF $CONF/certs
    sudo install -o \"\$u\" -g \"\$u\" -m 600 $REMOTE_TMP/node.env $CONF/
    sudo install -o \"\$u\" -g \"\$u\" -m 600 $REMOTE_TMP/$DEVICE.cert.pem $REMOTE_TMP/$DEVICE.private.key \
        $REMOTE_TMP/AmazonRootCA1.pem $CONF/certs/
    rm -rf $REMOTE_TMP"

echo
echo "Provisioned $DEVICE (zone $ZONE). On the node: cd smart-gardening/node && ./setup.sh"
echo "Then check:   journalctl -u smart-gardening-node -f   (or: .venv/bin/python -m gardening_node.scheduler --once)"
