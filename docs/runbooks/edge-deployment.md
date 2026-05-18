# Edge Device Deployment Runbook

## Target Hardware
- NVIDIA Jetson Orin Nano 8GB
- ARM64 architecture
- JetPack 6.x

## Deployment Steps

### 1. Flash Device
```bash
# Use NVIDIA SDK Manager to flash JetPack 6.x
# Configure network settings for site connectivity
```

### 2. Install Edge Package
```bash
scp constructai-edge-*.tar.gz jetson@device:/opt/constructai/
ssh jetson@device
cd /opt/constructai
tar xzf constructai-edge-*.tar.gz
./install.sh
```

### 3. Configure
```bash
# Edit configuration
nano /opt/constructai/config.yaml
# Set: MQTT broker, camera URLs, model paths, sync endpoint
```

### 4. Start Services
```bash
systemctl start constructai-edge
systemctl status constructai-edge
```

### 5. Verify
```bash
# Check inference pipeline
curl http://localhost:8080/health
# Check MQTT connectivity
mosquitto_sub -h localhost -t "constructai/alerts/#" -C 1
```

## Troubleshooting
- **No inference output**: Check GPU with `tegrastats`
- **MQTT disconnected**: Verify broker address and credentials
- **High latency**: Check model is TensorRT-optimized, not running in FP32
