#!/usr/bin/env bash
# Setup script for remote-ssh integration test environment.
# Generates Ed25519 test key pair and prepares Docker volumes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYS_DIR="${SCRIPT_DIR}/keys"

echo "==> Generating Ed25519 test key pair..."

mkdir -p "${KEYS_DIR}"

# Generate key pair (no passphrase for automated testing)
if [ ! -f "${KEYS_DIR}/id_ed25519" ]; then
  ssh-keygen -t ed25519 -f "${KEYS_DIR}/id_ed25519" -N "" -C "integration-test-key"
  echo "    Key pair generated."
else
  echo "    Key pair already exists, skipping generation."
fi

# Ensure correct permissions
chmod 600 "${KEYS_DIR}/id_ed25519"
chmod 644 "${KEYS_DIR}/id_ed25519.pub"

echo "==> Key setup complete."
echo "    Private key: ${KEYS_DIR}/id_ed25519"
echo "    Public key:  ${KEYS_DIR}/id_ed25519.pub"

# Copy public key to authorized_keys format (linuxserver image reads PUBLIC_KEY_FILE)
cp "${KEYS_DIR}/id_ed25519.pub" "${KEYS_DIR}/authorized_keys"
chmod 644 "${KEYS_DIR}/authorized_keys"

echo "==> To start the test environment:"
echo "    cd ${SCRIPT_DIR} && docker compose up -d"
echo ""
echo "==> To apply 50ms RTT network emulation (requires tc/netem, Linux only):"
echo "    docker exec remote-ssh-test-sshd tc qdisc add dev eth0 root netem delay 50ms"
echo "    docker exec remote-ssh-test-bastion tc qdisc add dev eth0 root netem delay 50ms"
