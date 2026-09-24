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

### 1.1 Remote in to the Jetson via SSH

```bash
ssh autonomypark@192.168.XXX.XXX
```

You will need the autonomypark user password, but that will not be given here.

### 1.2 Prepare the hostname

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install nano -y
```

### 1.3 Connect to Wi-Fi

Connect using whichever network applies.

**APark Wi-Fi**

```
sudo nmcli device wifi connect "UFLAutonomyPark_5GHz" password "$APARK_WIFI_PW"
```

**Indoor Lab Wi-Fi**

```
sudo nmcli device wifi connect "UFLAutonomyPark_5GHz" password "$NCR_WIFI_PW"
```

### 1.4 Set the hostname

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

### 1.5 Update the router IP reservation

Log in to the router and fix the IP address. Log this in the IP reservations document in the Autonomy Park Google Drive.

> [!IMPORTANT]
> On the router (Ubiquiti), under Client Devices, your Jetson will flicker between its new and old hostname. Not much can be done about this — wait until the flicker stops (e.g., the next day) before fixing the local IP address. This may have been patched by Ubiquiti since this note was written.

### 1.6 Docker 28+ compatibility fix

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

Edit `.env` with the details your Jetson needs. `MAVROS_TGT_SYSTEM` is
covered in [3.4](#34-set-a-unique-mavlink-system-id) — do not leave it at the
default.

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

### 3.4 Set a unique MAVLink system ID

> [!CAUTION]
> Every vehicle on the network — homebrews, the Astro, anything else running
> MAVROS — must have a **unique** MAVLink system ID. Otherwise vehicles receive
> each other's telemetry *and commands* (arming, mode changes, setpoints,
> parameter writes), even though their topics are namespaced.

**Why:** `mavros_node` is a router (talks to the FCU over `/dev/ttyFC`) plus a
UAS node (runs the plugins that publish `/<namespace>/...`). The two exchange
raw MAVLink over the ROS topics `/uas<N>/mavlink_source` and
`/uas<N>/mavlink_sink`, where `N` is MAVROS's `tgt_system`. MAVROS hardcodes
these as **absolute** names, so `MAVROS_NAMESPACE` does not apply to them.
Every vehicle left at the default `tgt_system=1` shares `/uas1` across the
DDS network, and each vehicle's MAVROS reads and writes every vehicle's FCU.

**How:** use the homebrew number (homebrew02 → 2, homebrew03 → 3, ...), and
keep a record of which IDs are taken (the Astro needs one too).

1. In QGC, set the FCU parameter `MAV_SYS_ID` to the vehicle's ID and reboot
   the flight controller (the new ID only takes effect after a reboot).
2. Set the same value in `.env`:
   ```
   MAVROS_TGT_SYSTEM=2
   ```
3. Recreate the container (`docker compose up -d`).

The two values **must match**. If `MAVROS_TGT_SYSTEM` differs from the FCU's
`MAV_SYS_ID`, MAVROS drops the FCU's messages and the vehicle's topics exist
but stay silent.

Verify from any machine on the network (see
[Inspecting the ROS graph](#inspecting-the-ros-graph)):

```bash
docker compose logs homebrew_bringup | grep -E "UAS Prefix|MY ID"
#   UAS Prefix: /uas2
#   MAVROS UAS via /uas2 started. MY ID 2.191, TARGET ID 2.1
ros2 topic list | grep '^/uas'
```

Each `/uas<N>` pair should have exactly one vehicle's `mavros_router` and
`mavros` nodes on it (`ros2 topic info -v /uas2/mavlink_source`).

### 3.5 Build the homebrew_bringup package

> [!WARNING]
> Nothing in the Dockerfile, `docker-compose.yml`, or `ros_entrypoint.sh` builds
> `homebrew_ws` for you. `homebrew_ws` is bind-mounted into the container so you
> can edit and rebuild without a full image rebuild — but that means **you must
> build it yourself, once initially and again after any change to `homebrew_ws`
> source**, or the container will crash-loop with `homebrew_bringup` not found.

```bash
cd homebrew_ws
colcon build --packages-select homebrew_bringup
```

> [!WARNING]
> Do not add `--symlink-install`. The host and the container see this same
> directory at different absolute paths (`~/homebrew_docker/homebrew_ws` vs.
> `/root/homebrew_ws`), and symlink-install bakes in whichever path was current
> at build time — it'll work from one side and break from the other.

### 3.6 Build and run

```bash
sudo docker compose build
sudo docker compose up -d
```

If you edit anything under `homebrew_ws/src/` after this point (including
after a `git pull`), re-run the `colcon build` command from 3.5 before
`docker compose up -d`, or you'll get the crash loop described above again.

## 4. Optional: Auto-Start on Boot

Remove any old auto-starts
```bash
sudo chmod +x scripts/remove-autostart.sh
sudo ./scripts/remove-autostart.sh
```

Add yours.
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

Docker stuck in a crash loop? Look at the logs
```bash
docker compose logs -f homebrew_bringup
```

Or, maybe the container is boot looping because dockerd shutdown in the middle of critical work (likely an issue with BuildKit's cache-metadata database)
```bash
sudo mv /var/lib/docker/buildkit/cache.db /var/lib/docker/buildkit/cache.db.corrupt-backup
sudo systemctl restart docker.service
sudo systemctl status docker.service
```

> [!NOTE]
> When running the image for the first time, the ZED AI models need to download. Make sure the Jetson is connected to the Internet for that first run. Once the models have downloaded successfully, the container can run with Internet.

> [!IMPORTANT]
> Always double-check the discovery server IP in `/config/fastdds/super_client_config.xml`.

### Inspecting the ROS graph

Run ROS 2 commands through the container's entrypoint, which sources every
workspace and applies the discovery server profile — a plain shell without the
profile sees a different (partial) graph:

```bash
docker exec autonomypark-homebrew /sbin/homebrew_ros_entrypoint.sh ros2 topic list
```

Useful commands:

```bash
ros2 topic info -v <topic>   # which nodes (and namespaces) publish/subscribe
ros2 topic hz <topic>        # is anything actually arriving?
ros2 param get <node> <param>
```

**One vehicle's data appears in another vehicle's topics**

Check `ros2 topic info -v` on the affected topic. If it has exactly one
publisher in the right namespace, the mixing happens upstream of ROS — look
at the `/uas<N>` link topics. Two vehicles on the same `/uas<N>` means their
system IDs collide; see [3.4](#34-set-a-unique-mavlink-system-id). In
general, any topic that shows up in `ros2 topic list` outside your vehicle
namespaces is shared by every machine on the network.

**A vehicle's topics are missing after boot**

Fast DDS chooses which network addresses to advertise when a node starts and
never re-scans. If ROS starts before Wi-Fi connects, the vehicle's nodes run
but are invisible to everything else, including `ros2` commands in its own
container. The entrypoint prevents this by waiting up to
`NETWORK_WAIT_TIMEOUT` seconds (default 120) for a default route (Wi-Fi up);
if none appears it exits and Docker restarts the container. It also
waits up to `CLOCK_WAIT_TIMEOUT` seconds (default 60) for NTP to set the
clock, since the Jetsons boot at 1970. Check with:

```bash
docker compose logs homebrew_bringup | grep -E "No default route|clock"
```

If a vehicle's IP address changes while the container is running (e.g. its
router reservation is missing), restart the container.

> [!NOTE]
> `docker ps` showing a container "Up 56 years" means it started before the
> clock was set (epoch 0) and is harmless on its own.

## TO-DO:
- Add static transform publisher for Zed using Homebrew CAD file