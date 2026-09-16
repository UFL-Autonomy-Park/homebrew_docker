# homebrew_docker

Docker stack for MAVROS and ZED SDK, deployed on a Jetson Orin Nano running JetPack 6.0 rev2 (L4T r36.3.0, CUDA 12.2).

## Table of Contents

- [1. First-Time Jetson Setup](#1-first-time-jetson-setup)
- [2. Build the ZED Base Image](#2-build-the-zed-base-image)
- [3. Configure homebrew_docker](#3-configure-homebrew_docker)
- [4. Optional: Auto-Start on Boot](#4-optional-auto-start-on-boot)
- [Notes & Troubleshooting](#notes--troubleshooting)

---

## 1. First-Time Jetson Setup

### 1.1 Prepare the hostname

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install nano -y
```

### 1.2 Connect to Wi-Fi

Connect using whichever network applies.

**APark Wi-Fi**

```
sudo nmcli device wifi connect "UFLAutonomyPark_5GHz" password "$APARK_WIFI_PW"
```

**Indoor Lab Wi-Fi**

```
sudo nmcli device wifi connect "UFLAutonomyPark_5GHz" password "$NCR_WIFI_PW"
```

### 1.3 Set the hostname

Use a two-digit `$NUMBER`.

```
sudo hostnamectl set-hostname homebrew-jetson-"$NUMBER"
```

Also update the hostname in the hosts file:

```bash
sudo nano /etc/hosts
```

Reboot to apply changes:

```bash
sudo reboot
```

### 1.4 Update the router IP reservation

Log in to the router and fix the IP address. Log this in the IP reservations document in the Autonomy Park Google Drive.

> [!IMPORTANT]
> On the router (Ubiquiti), under Client Devices, your Jetson will flicker between its new and old hostname. Not much can be done about this — wait until the flicker stops (e.g., the next day) before fixing the local IP address. This may have been patched by Ubiquiti since this note was written.

### 1.5 Docker 28+ compatibility fix

If you are using Docker 28+ (check with `apt-cache policy docker-cli`; almost certain if you ran `sudo apt upgrade`), the following is required. The Jetson's kernel cannot be upgraded with `apt` (unlike a regular Ubuntu machine) without changing JetPack versions. Do not attempt to upgrade the kernel.

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/override.conf <<'EOF'
[Service]
Environment="DOCKER_INSECURE_NO_IPTABLES_RAW=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

Confirm the change was successful before proceeding.

```bash
sudo journalctl -u docker.service --since "5 minutes ago" | grep DOCKER_INSECURE_NO_IPTABLES_RAW
```

## 2. Build the ZED Base Image

Clone our fork of the ZED ROS 2 wrapper:

```bash
git clone https://github.com/UFL-Autonomy-Park/zed_ros2_wrapper_jetson.git
cd zed_ros2_wrapper_jetson
git switch autonomypark/humble-v5.0.0
```

Build the ROS 2 Humble, ZED SDK v5.0.0, L4T r36.3.0 base image:

```bash
./docker/jetson_build_dockerfile_from_sdk_and_l4T_version.sh \
	l4t-r36.3.0 \
	zedsdk-5.0.0
```

---

## 3. Configure homebrew_docker

### 3.1 Clone and configure

```bash
git clone https://github.com/UFL-Autonomy-Park/homebrew_docker.git && cd homebrew_docker
cp .env.example .env
```

Edit `.env` with the details your Jetson needs.

### 3.2 ZED camera calibration

Set up your ZED camera with a factory calibration file before launching.

Get your serial number by plugging the ZED into the Jetson and running:

```bash
lsusb -v 2>/dev/null | grep -A20 -i stereolabs
```

Using the 8-digit serial number:

```
mkdir -p /config/zed
ZED_SERIAL=${YOUR_ZED_SERIAL_NUMBER}
curl -fL \
  "https://calib.stereolabs.com/?SN=${ZED_SERIAL}" \
  -o "config/zed/SN${ZED_SERIAL}.conf"
```

> [!NOTE]
> If you are the first person setting up this ZED, print the serial number with the label maker and attach it to the camera so the next person doesn't have to repeat this step.

### 3.3 Flight controller udev rule

First, find the serial number for the FTDI cable you're using:

```bash
lsusb -v | grep -i BG
```

If it doesn't appear, find the device in the full `lsusb -v` output and copy its `iSerial`:

```
idVendor           0x0403 Future Technology Devices International, Ltd
idProduct          0x6001 FT232 Serial (UART) IC
```

> [!WARNING]
> Do not swap FTDI cables between quadcopters — serial numbers are unique to each cable.

Edit the udev script and paste in the serial number:

```bash
nano scripts/setup-udev-fc.sh
```

Save and exit, then run it:

```bash
sudo chmod +x scripts/setup-udev-fc.sh
sudo ./scripts/setup-udev-fc.sh
```

If instructed by the script, unplug and replug the FTDI cable connecting to the flight controller. Otherwise, it should print `✓ Symlink /dev/ttyFC exists!`.

### 3.4 Build and run

```bash
sudo docker compose build
sudo docker compose up -d
```

## 4. Optional: Auto-Start on Boot

```bash
sudo chmod +x scripts/setup-autostart.sh
sudo ./scripts/setup-autostart.sh
```

## Notes & Troubleshooting

**Using the Micro USB connection instead of FTDI**

If you are troubleshooting the flight controller with the Micro USB connection instead of the FTDI cable, change the following values in `setup-udev-fc.sh`:

```
VENDOR_ID="0403"    -> VENDOR_ID="2dae"
PRODUCT_ID="6001"   -> PRODUCT_ID="1016"
SERIAL_NUM="PLACEHOLDER" -> SERIAL_NUM="0"
```

> [!NOTE]
> When running the image for the first time, the ZED AI models need to download. Make sure the Jetson is connected to the Internet for that first run. Once the models have downloaded successfully, the container can run with Internet.

> [!IMPORTANT]
> Always double-check the discovery server IP in `/config/fastdds/super_client_config.xml`.

## TO-DO:
- Add static transform publisher for Zed using Homebrew CAD file