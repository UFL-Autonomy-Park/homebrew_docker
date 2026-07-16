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

## Setup
```bash
# 1. Clone and configure
git clone https://github.com/UFL-Autonomy-Park/homebrew_docker.git  && cd homebrew_docker
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

If you are the first person setting up the Zed, print the serial number using the label maker and attach it to the Zed so the next person does not have to do this

Then, set up udev rule for the flight controller
```bash
sudo chmod +x /scripts/setup-udev-fc.sh
sudo ./scripts/setup-udev-fc.sh
# Unplug/replug flight controller
```

Build the image and run the container
```bash
sudo docker compose build
sudo docker compose up -d
```

## Optional: Auto-start on boot
```bash
sudo chmod +x /scripts/setup-autostart.sh
sudo ./scripts/setup-autostart.sh
```

## Notes
If you are troubleshooting the flight controller and are using the Micro USB connection instead of the FTDI, change the following values in `setup-udev-fc.sh`:
```
VENDOR_ID="0403" -> VENDOR_ID="2dae" 
PRODUCT_ID="6001" -> PRODUCT_ID="1016"
SERIAL_NUM="B0040P4E" -> SERIAL_NUM="0"
```

When running this image for the first time, the Zed AI models will need to download. Ensure the Jetson is connected to the internet. After the models are successfully downloaded, you can run the container on LAN only. 

ALWAYS DOUBLE CHECK THE DISCOVERY SERVER IP IN `/config/fastdds/super_client_config.xml`