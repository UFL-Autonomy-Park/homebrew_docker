# homebrew_docker
Docker stack for MAVROS and Zed SDK to be used on Jetson Orin Nano with CUDA 12.2

## Build ZED base image
Clone our fork of the ZED ROS 2 wrapper
```bash
git clone https://github.com/UFL-Autonomy-Park/zed_ros2_wrapper_jetson.git
cd zed_ros2_wrapper_jetson
git switch autonomypark/humble-v5.0.0
```

Build the ROS 2 Humble, ZED SDK v5.0.0, L4T r36.3.0 base image
```bash
./docker/jetson_build_dockerfile_from_sdk_and_l4T_version.sh \
	l4t-r36.3.0 \
	zedsdk-5.0.0
```

Make sure the image exists
```bash
docker image inspect zed_ros2_l4t_36.3.0_sdk_5.0.0
```


## Setup
```bash
# 1. Clone and configure
git https://github.com/UFL-Autonomy-Park/homebrew_docker.git  && cd homebrew_docker
cp .env.example .env
# Edit .env with your settings
```

Set up your Zed camera with a factory calibration file before launching. Get your serial number by plugging in the Zed to the Jetson and running
```bash
lsusb -v 2>/dev/null | grep -A20 -i stereolabs
```

Use the 8-digit number and run
```bash
mkdir -p /config/zed
ZED_SERIAL=${YOUR_ZED_SERIAL_NUMBER}
curl -fL \
  "https://calib.stereolabs.com/?SN=${ZED_SERIAL}" \
  -o "config/zed/SN${ZED_SERIAL}.conf"
```

# 2. Setup USB device (one-time)
sudo chmod +x /scripts/setup-udev-fc.sh
sudo ./scripts/setup-udev-fc.sh
# Unplug/replug flight controller

# 3. Get Zed config file (one-time)
ZED_SERIAL

# 3. Run
sudo docker compose build
sudo docker compose up -d
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