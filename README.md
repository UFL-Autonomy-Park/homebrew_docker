# homebrew_docker
Docker stack for MAVROS and Zed SDK to be used on Jetson Orin Nano with CUDA 12.2

## Setup
```bash
# 1. Clone and configure
git clone  && cd homebrew_docker
cp .env.example .env
# Edit .env with your settings

# 2. Setup USB device (one-time)
sudo chmod +x /scripts/setup-udev-fc.sh
sudo ./scripts/setup-udev-fc.sh
# Unplug/replug flight controller

# 3. Run
sudo docker-compose up -d
```

## Optional: Auto-start on boot
```bash
sudo chmod +x /scripts/setup-autostart.sh
sudo ./scripts/setup-autostart.sh
```

## Commands
```bash
docker-compose up -d      # Start
docker-compose down       # Stop
docker-compose logs -f    # View logs
docker-compose restart    # Restart
```

## Configuration (.env)
- `MAVROS_NAMESPACE` - ROS namespace
- `FCU_URL` - Device path (`/dev/ttyFC:921600`)
- `ROS_DOMAIN_ID` - ROS domain
- `ROS_DISCOVERY_SERVER` - Discovery server `IP:port`
