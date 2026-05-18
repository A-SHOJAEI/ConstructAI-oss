#!/usr/bin/env bash
# Deploy edge pipeline to Jetson device
set -euo pipefail

DEVICE_HOST="${1:?Usage: deploy.sh <device-host> [model-path]}"
MODEL_PATH="${2:-/models/rtmdet_construction.engine}"
DEPLOY_DIR="/opt/constructai/edge"
SSH_USER="${SSH_USER:-constructai}"

echo "=== ConstructAI Edge Deployment ==="
echo "Target: ${SSH_USER}@${DEVICE_HOST}"
echo "Model: ${MODEL_PATH}"
echo ""

# Ensure target directories exist
ssh "${SSH_USER}@${DEVICE_HOST}" "mkdir -p ${DEPLOY_DIR}/{src,config,scripts,models}"

# Sync source files
echo "Syncing source files..."
rsync -avz --delete \
    src/ config/ scripts/ requirements-edge.txt \
    "${SSH_USER}@${DEVICE_HOST}:${DEPLOY_DIR}/"

# Install dependencies
echo "Installing Python dependencies..."
ssh "${SSH_USER}@${DEVICE_HOST}" \
    "cd ${DEPLOY_DIR} && pip3 install -r requirements-edge.txt"

# Copy model if specified and exists locally
if [ -f "${MODEL_PATH}" ]; then
    echo "Uploading model..."
    rsync -avz "${MODEL_PATH}" "${SSH_USER}@${DEVICE_HOST}:${DEPLOY_DIR}/models/"
fi

# Create systemd service
echo "Setting up systemd service..."
ssh "${SSH_USER}@${DEVICE_HOST}" "cat > /tmp/constructai-edge.service << 'UNIT'
[Unit]
Description=ConstructAI Edge Vision Pipeline
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SSH_USER}
WorkingDirectory=${DEPLOY_DIR}
ExecStart=/usr/bin/python3 -m src.edge_pipeline
Restart=always
RestartSec=10
Environment=MQTT_HOST=localhost
Environment=MQTT_PORT=1883
Environment=MODEL_PATH=${DEPLOY_DIR}/models/rtmdet_construction.engine
Environment=DEVICE_ID=jetson-001

[Install]
WantedBy=multi-user.target
UNIT
sudo mv /tmp/constructai-edge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable constructai-edge
sudo systemctl restart constructai-edge"

echo ""
echo "Deployment complete! Service status:"
ssh "${SSH_USER}@${DEVICE_HOST}" "systemctl status constructai-edge --no-pager || true"
