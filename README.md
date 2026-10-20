# homebrew_docker

Docker stack for MAVROS and ZED SDK, deployed on a Jetson Orin Nano running JetPack 6.0 rev2 (L4T r36.3.0, CUDA 12.2).

## Table of Contents

- [1. First-Time Jetson Setup](#1-first-time-jetson-setup)
- [2. Build the ZED Base Image](#2-build-the-zed-base-image)
- [3. Set Up homebrew_docker](#3-set-up-homebrew_docker)
- [4. Auto-Start on Boot](#4-auto-start-on-boot)
- [After setup: changing `homebrew_ws`](#after-setup-changing-homebrew_ws)
- [5. RTK Corrections](#5-rtk-corrections)
- [6. Persistent Logs](#6-persistent-logs)
- [7. MAVROS Plugin List](#7-mavros-plugin-list)
- [8. Flight-Controller Bridge: MAVROS or Micro XRCE-DDS Agent](#8-flight-controller-bridge-mavros-or-micro-xrce-dds-agent)
- [9. MAVLink Stream Rates (optional)](#9-mavlink-stream-rates-optional)
- [Notes & Troubleshooting](#notes--troubleshooting)

---

## 1. First-Time Jetson Setup

Sections 1–4 are one linear procedure for a freshly flashed Jetson. Run every
block in order, top to bottom, exactly as written; nothing is skipped
conditionally, except one choice in 3.5 for a vehicle without a ZED camera.
All commands run on the Jetson over SSH unless a step says
otherwise.

> [!IMPORTANT] 
> Throughout, it is assumed that the discovery server IP is `192.168.1.201`.

### 1.1 SSH in to the Jetson

Connect the Jetson to the LAN via Ethernet. Find its IP in the router Client Devices (UniFi bookmark in Autonomy Park Google Chrome). Then SSH in:

```bash
ssh autonomypark@192.168.XXX.XXX
```

You will need the autonomypark user password, but that will not be given here.

### 1.2 Update packages

```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y nano git curl
```

### 1.3 Connect to Wi-Fi

```bash
sudo nmcli device wifi connect "UFLAutonomyPark_5GHz" password "$APARK_WIFI_PW"
```

You may then disconnect from Ethernet and proceed over Wi-Fi.

### 1.4 Set the hostname

> [!IMPORTANT]
> Set `NUMBER` to this homebrew's **two-digit** number (homebrew05 → `05`).
> Later steps read the number back from the hostname, so a typo here
> propagates everywhere.

```bash
NUMBER=05
```

Then:

```bash
sudo hostnamectl set-hostname "homebrew-jetson-$NUMBER"
sudo sed -i '/^127\.0\.1\.1\s/d' /etc/hosts
echo -e "127.0.1.1\thomebrew-jetson-$NUMBER" | sudo tee -a /etc/hosts
hostname
```

`hostname` should print `homebrew-jetson-05` (with your number).

### 1.5 Docker compatibility fix

The Jetson's kernel lacks a feature Docker 28+ expects. This setting works
around it and is harmless on older Docker. Do not try to upgrade the kernel:
on a Jetson that means changing JetPack versions.

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/override.conf <<'EOF'
[Service]
Environment="DOCKER_INSECURE_NO_IPTABLES_RAW=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo systemctl show docker --property=Environment
```

The last command should print `Environment=DOCKER_INSECURE_NO_IPTABLES_RAW=1`.

### 1.6 Disable the firewall

> [!WARNING]
> Some Jetson images ship with the `ufw` firewall **enabled**, dropping all
> incoming traffic except SSH and ping. ROS 2 then fails silently: the
> vehicle's nodes reach the discovery server, but the server's replies are
> dropped, so no other computer ever sees the vehicle's topics. Ping and SSH
> still work, so nothing looks wrong. Run this block on every Jetson, even if
> you think the firewall is off.

```bash
sudo ufw disable
sudo ufw status
```

`ufw status` should print `Status: inactive`. This setting survives reboots.

> [!NOTE]
> On homebrew05 the firewall was first worked around with
> `sudo ufw allow from 192.168.1.0/24`, which lets in everything from the lab
> network. Disabling the firewall is simpler and also works on any other
> network.

### 1.7 Disable Wi-Fi power saving

By default NetworkManager turns on Wi-Fi power saving
(`/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf`). The radio then
sleeps between the router's beacons. The router holds incoming packets until
the radio wakes, which adds delay and bursts to everything sent **to** the
vehicle: commands, setpoints, RTK corrections and discovery. On a vehicle the
Wi-Fi radio's power is negligible next to the motors. This file overrides the
default (files in `conf.d` load in name order, later ones win) and takes
effect at the reboot in 1.9:

```bash
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/zz-wifi-powersave-off.conf
NetworkManager --print-config | grep -E "Failed|etc:|powersave"
```

The second command only reads the configuration; Wi-Fi stays up. It must show
`zz-wifi-powersave-off.conf` in the `etc:` list and `wifi.powersave=2`.

> [!CAUTION]
> If it prints `Failed to read configuration`, **do not reboot**:
> NetworkManager refuses to start with an unreadable file in `conf.d`, and the
> Jetson comes back with no Wi-Fi. Delete the file and check it is gone (a
> plain `rm` without `sudo` silently leaves it in place):
> ```bash
> sudo rm /etc/NetworkManager/conf.d/zz-wifi-powersave-off.conf
> ls /etc/NetworkManager/conf.d/
> ```

### 1.8 Time sync with chrony

Every computer that runs ROS 2 nodes uses chrony, synced to the lab time
server on the discovery server (192.168.1.201). Message ages are computed
across machines, so their clocks must agree, and chrony only steps the clock
in its first few updates after boot; afterwards it slews it gradually instead
of jumping it. The full guide, including the server and how to undo
everything, is [docs/TIME_SYNC.md](docs/TIME_SYNC.md).

Installing chrony removes `systemd-timesyncd` (the two packages conflict):

```bash
sudo apt-get install -y chrony
printf 'server 192.168.1.201 iburst prefer\n' | sudo tee /etc/chrony/conf.d/lab-time-server.conf
sudo systemctl restart chrony
sleep 30
chronyc sources
```

The `192.168.1.201` line should start with `^*` (selected source).

### 1.9 Reboot

> [!CAUTION]
> **Reboot now**, so the hostname and the Wi-Fi setting apply everywhere. Your
> SSH session drops; wait about a minute and SSH back in (1.1).

```bash
sudo reboot
```

After you SSH back in:

```bash
hostname
/sbin/iw dev wlan0 get power_save
chronyc tracking | grep -E "Reference ID|Stratum|System time"
```

Expect `homebrew-jetson-05` (with your number), `Power save: off`, and a
`Reference ID` of `C0A801C9 (192.168.1.201)`.

### 1.10 Update the router IP reservation

Log in to the router and fix the IP address. Log this in the IP reservations
document in the Autonomy Park Google Drive.

> [!IMPORTANT]
> On the router (Ubiquiti), under Client Devices, your Jetson will flicker between its new and old hostname. Not much can be done about this — wait until the flicker stops (e.g., the next day) before fixing the local IP address. This may have been patched by Ubiquiti since this note was written.

## 2. Build the ZED Base Image

```bash
cd ~
git clone https://github.com/UFL-Autonomy-Park/zed_ros2_wrapper_jetson.git
cd ~/zed_ros2_wrapper_jetson
git switch autonomypark/humble-v5.0.0
```

Build the ROS 2 Humble, ZED SDK v5.0.0, L4T r36.3.0 base image:

```bash
cd ~/zed_ros2_wrapper_jetson
sudo ./docker/jetson_build_dockerfile_from_sdk_and_l4T_version.sh \
	l4t-r36.3.0 \
	zedsdk-5.0.0
```

---

## 3. Set Up homebrew_docker

### 3.1 Clone and create `.env`

```bash
cd ~
git clone https://github.com/UFL-Autonomy-Park/homebrew_docker.git
cd ~/homebrew_docker
cp .env.example .env
```

`.env` holds this vehicle's settings. The steps below fill in the
vehicle-specific ones; the rest (discovery server, NTRIP caster, MAVROS
plugin list) are the lab's defaults and stay as they are.

### 3.2 Persistent logs and machine ID

Makes the system journal survive reboots and power loss, collects core dumps,
and gives the Jetson its own machine ID (Jetsons flashed from the same image
share one). Details in [6](#6-persistent-logs).

```bash
cd ~/homebrew_docker
sudo bash scripts/setup-persistent-logs.sh
```

> [!CAUTION]
> **Reboot now**, every time, whatever the script printed: the new machine ID
> only takes effect after a reboot. SSH back in (1.1) afterwards.

```bash
sudo reboot
```

After the reboot, check:

```bash
cat /etc/machine-id
journalctl --list-boots | tail -2
```

The machine ID must **not** be `5dbfb12414a3456d9014d88183e338b1`, and the
boot list should show the boot before this one.

### 3.3 Flight controller parameters

> [!WARNING]
> Every vehicle on the network — homebrews, the Astro, anything else running
> MAVROS — must have a **unique** MAVLink system ID. Otherwise vehicles receive
> each other's telemetry *and commands* (arming, mode changes, setpoints,
> parameter writes), even though their topics are namespaced. Use the
> homebrew number (homebrew05 → `5`) and record it with the IP reservation.
> **Never use 1:** Freefly forces every Astro to `MAV_SYS_ID=1`.

In QGroundControl (Vehicle Setup → Parameters), set:

| Parameter | Value | Why |
|---|---|---|
| `MAV_SYS_ID` | the homebrew number, e.g. `5` | unique MAVLink ID; must equal `MAVROS_TGT_SYSTEM` (3.6) |
| `MAV_1_CONFIG` | `TELEM 2` | MAVLink to the Jetson on TELEM2 |
| `MAV_1_MODE` | `Onboard` | |
| `SER_TEL2_BAUD` | `921600 8N1` | must match `FCU_URL` in `.env` |
| `UXRCE_DDS_CFG` | `Disabled` | TELEM2 is MAVROS's ([8](#8-flight-controller-bridge-mavros-or-micro-xrce-dds-agent)) |
| `UAVCAN_PUB_RTCM` | `Enabled` | forwards RTK corrections to the Here4 ([5](#5-rtk-corrections)) |
| `COM_OF_LOSS_T` | `0.5` s | offboard setpoints missing this long trigger the offboard-loss action (default 1.0 s) |
| `COM_OBL_RC_ACT` | `Land` (4) | offboard-loss action; does not depend on the Jetson or Wi-Fi |

Then reboot the flight controller (Vehicle Setup → Parameters → Tools →
Reboot Vehicle). `MAV_SYS_ID` only takes effect after a reboot.

**Why the ID matters:** `mavros_node` is a router (talks to the FCU over
`/dev/ttyFC`) plus a UAS node (runs the plugins that publish
`/<namespace>/mavros/...`). The two exchange raw MAVLink over the ROS topics
`/uas<N>/mavlink_source` and `/uas<N>/mavlink_sink`, where `N` is MAVROS's
`tgt_system`. MAVROS hardcodes these as **absolute** names, so
`MAVROS_NAMESPACE` does not apply to them, and two vehicles with the same ID
share one link across the network.

### 3.4 Flight controller serial port (`/dev/ttyFC`)

> [!WARNING]
> Plug in **only** the FTDI cable to this vehicle's flight controller, and do
> not swap FTDI cables between quadcopters: the rule matches this cable's
> serial number.

```bash
cd ~/homebrew_docker
ls /dev/serial/by-id/
FTDI_SERIAL=$(ls /dev/serial/by-id/ | grep -m1 '^usb-FTDI' | sed -E 's/.*_([^_]+)-if[0-9]+-port[0-9]+$/\1/')
echo "FTDI serial: $FTDI_SERIAL"
sudo bash scripts/setup-udev-fc.sh "$FTDI_SERIAL"
```

`ls` should show one `usb-FTDI_FT232R_USB_UART_<serial>-if00-port0` entry,
and `FTDI serial:` its `<serial>` part. **Unplug the FTDI cable from the
Jetson, plug it back in**, then check:

```bash
ls -l /dev/ttyFC
```

It should point to a `ttyUSB` device.

### 3.5 ZED camera calibration

> [!IMPORTANT]
> **No ZED on this vehicle?** Run only this block, skip the rest of 3.5 and
> continue at 3.6. Section 2's base image is still needed: it is the image's
> ROS 2 base, not only the ZED.
> ```bash
> cd ~/homebrew_docker
> sed -i "s/^LAUNCH_ZED=.*/LAUNCH_ZED=false/" .env
> grep '^LAUNCH_ZED=' .env
> ```
> `grep` should print `LAUNCH_ZED=false`. The ZED wrapper and its static
> transform then don't start. Docker still creates an empty, root-owned
> directory `config/zed/SN12345678.conf` for the container's ZED mount; with
> the ZED off nothing reads it, so leave it.

With a ZED: plug it into the Jetson, find its serial number (8 digits, also
printed on the camera):

```bash
lsusb -v 2>/dev/null | grep -A20 -i stereolabs
```

> [!IMPORTANT]
> Replace `12345678` below with your camera's serial number.

```bash
cd ~/homebrew_docker
ZED_SERIAL=12345678
sed -i "s/^ZED_SERIAL=.*/ZED_SERIAL=$ZED_SERIAL/" .env
mkdir -p config/zed
curl -fL "https://calib.stereolabs.com/?SN=$ZED_SERIAL" -o "config/zed/SN$ZED_SERIAL.conf"
head -3 "config/zed/SN$ZED_SERIAL.conf"
```

`head` should show a `[LEFT_CAM_...]` section, not an error page.

> [!WARNING]
> The container mounts `config/zed/SN<ZED_SERIAL>.conf`. If that file does not
> exist when the container first starts, Docker creates an empty, root-owned
> **directory** with that name instead, and the ZED can't be calibrated. Do
> not continue until `head` shows the calibration file.

### 3.6 Vehicle settings in `.env`

The namespace and MAVLink ID come from the hostname set in 1.4:

```bash
cd ~/homebrew_docker
NUMBER=$(hostname | grep -oE '[0-9]+$')
sed -i "s/^MAVROS_NAMESPACE=.*/MAVROS_NAMESPACE=homebrew$NUMBER/" .env
sed -i "s/^MAVROS_TGT_SYSTEM=.*/MAVROS_TGT_SYSTEM=$((10#$NUMBER))/" .env
grep -E '^(MAVROS_NAMESPACE|MAVROS_TGT_SYSTEM|LAUNCH_ZED|ZED_SERIAL|FCU_URL|MAVROS_PLUGINS|USE_DISCOVERY_SERVER)=' .env
```

For homebrew05 this shows `MAVROS_NAMESPACE=homebrew05`,
`MAVROS_TGT_SYSTEM=5`, `LAUNCH_ZED=true` and your `ZED_SERIAL` (without a
ZED: `LAUNCH_ZED=false`, `ZED_SERIAL=12345678`), `FCU_URL=/dev/ttyFC:921600`,
`MAVROS_PLUGINS=minimal` and `USE_DISCOVERY_SERVER=true`.

> [!WARNING]
> `MAVROS_TGT_SYSTEM` must equal the flight controller's `MAV_SYS_ID` (3.3).
> If they differ, MAVROS drops the FCU's messages: the vehicle's topics exist
> but stay silent.

### 3.7 Build the image

Takes about 45 minutes (it builds MAVROS and the Micro XRCE-DDS Agent from
source).

> [!WARNING]
> Build with **this exact command**, never `docker compose build` or
> `docker compose up --build`. The build needs host networking
> (`build.network: host` in `docker-compose.yml`). Docker Compose v5 builds
> through Bake, which refuses host networking unless it is granted with
> `--allow=network.host`, and `docker compose build` has no way to grant it.
> Run it from `~/homebrew_docker` so the image gets the name and tag
> `docker-compose.yml` expects.

```bash
cd ~/homebrew_docker
sudo docker buildx bake --allow=network.host -f docker-compose.yml
sudo docker images ufl-autonomy-park/homebrew-jetson
```

The last command should list the image with tag `dev`.

### 3.8 Build the homebrew_bringup package

> [!IMPORTANT]
> The Jetson has no ROS 2: `ros2` and `colcon` exist **only inside the
> container**. Your SSH session is on the Jetson itself, so you open a bash
> shell inside a container first. Its prompt looks like
> `root@homebrew-jetson-05:~/homebrew_ws#`; `exit` returns to the Jetson.
> Inside, `/root/homebrew_ws` **is** `~/homebrew_docker/homebrew_ws` on the
> Jetson (bind-mounted), so the build output lands on the Jetson where the
> real container finds it.

Nothing builds `homebrew_ws` automatically: without this step the container
crash-loops with `homebrew_bringup` not found. Open a build shell, a
temporary container with the same image and mounts:

```bash
cd ~/homebrew_docker
sudo docker compose run --rm homebrew_bringup bash
```

It prints `Warning: /root/homebrew_ws has not been built yet` (expected, that's
what this step fixes) and gives you the container's prompt. In the
**container's** shell:

```bash
colcon build --packages-select homebrew_bringup
exit
```

The build should end with `Summary: 1 package finished`. Back on the
**Jetson**: the container ran as root, so give the build output back to your
user, then check the launch file was installed:

```bash
cd ~/homebrew_docker
sudo chown -R "$USER:$USER" homebrew_ws/build homebrew_ws/install homebrew_ws/log
ls homebrew_ws/install/homebrew_bringup/share/homebrew_bringup/launch/
```

`ls` should list `homebrew.launch.py`.

### 3.9 Start the container

```bash
cd ~/homebrew_docker
sudo docker compose up -d
sudo docker compose ps
```

`ps` should show `autonomypark-homebrew` as `Up` (not `Restarting`).

### 3.10 Check the vehicle

Wait one minute after 3.9, then on the Jetson:

```bash
cd ~/homebrew_docker
sudo docker compose logs homebrew_bringup | grep -E "Got HEARTBEAT|UAS Prefix|MY ID"
```

Expect `CON: Got HEARTBEAT, connected`, `UAS Prefix: /uas5` and
`MY ID 5.191, TARGET ID 5.1` (with your number).

The rest of the check runs on the **lab PC**, not on the Jetson.

> [!IMPORTANT]
> Open a **new terminal on the lab PC**. The SSH session is on the Jetson; do
> not type these commands there. Replace `05` with the number you used in
> 1.4.

```bash
NUMBER=05
```

Then:

```bash
ros2 topic echo --once /homebrew$NUMBER/mavros/state --field connected
ros2 topic echo --once /homebrew$NUMBER/mavros/gpsstatus/gps1/raw --field fix_type
ros2 topic info /uas$((10#$NUMBER))/mavlink_source
```

| Command | Expected |
|---|---|
| `state --field connected` | `true` |
| `gps1/raw --field fix_type` | `6` (RTK fixed) outdoors with the base running, ~90 s after power-on; `3`/`4` indoors |
| `topic info /uas<N>/mavlink_source` | `Publisher count: 1` and `Subscription count: 1` |

> [!CAUTION]
> A publisher or subscription count above 1 on `/uas<N>/mavlink_source` means
> another vehicle on the network uses the same MAVLink ID. Stop and fix
> `MAV_SYS_ID` (3.3) before flying.

## 4. Auto-Start on Boot

Starts the stack at boot and stops it cleanly at shutdown. The first script
removes any previous auto-start, so this is safe to run again.

```bash
cd ~/homebrew_docker
sudo bash scripts/remove-autostart.sh
sudo bash scripts/setup-autostart.sh
sudo systemctl start homebrew-docker.service
systemctl is-enabled homebrew-docker.service
```

The last command should print `enabled`.

## After setup: changing `homebrew_ws`

After any change under `homebrew_ws/src/`, including a `git pull`, rebuild and
recreate the container. A plain `docker compose up -d` does not restart an
unchanged container, so it would keep running the old launch file.

```bash
cd ~/homebrew_docker
sudo docker compose run --rm homebrew_bringup bash -c "colcon build --packages-select homebrew_bringup"
sudo chown -R "$USER:$USER" homebrew_ws/build homebrew_ws/install homebrew_ws/log
sudo docker compose up -d --force-recreate
```

> [!NOTE]
> Build only in the container, even on a Jetson that has ROS 2 installed: the
> host doesn't have the image's packages (MAVROS, `ntrip_client`, the ZED
> wrapper), and mixing host and container builds in one `build/` directory is
> untested. Do not add `--symlink-install`.

## 5. RTK Corrections

The container runs an NTRIP client
([ntrip_client](https://github.com/LORD-MicroStrain/ntrip_client), pinned in
the Dockerfile) that pulls RTCM corrections from the base station's caster
over Wi-Fi and hands them to MAVROS (`/<namespace>/mavros/gps_rtk/send_rtcm`), which
sends them to the FCU. No ground-station MAVProxy is needed.

Setup is part of 3.3 (`UAVCAN_PUB_RTCM = Enabled`: the Here4 is a DroneCAN
GPS, so PX4 must forward RTCM onto the CAN bus) and `.env` (the `NTRIP_*`
values point at the Emlid base's local caster: IP, port 2101, mountpoint,
username and password).

**Before every takeoff, check the fix.** In a terminal on the **lab PC**
(replace `05` with the vehicle's number):

```bash
NUMBER=05
```

Then:

```bash
ros2 topic echo --once /homebrew$NUMBER/mavros/gpsstatus/gps1/raw --field fix_type
```

`6` is RTK fixed, `5` RTK float, `3`/`4` no RTK.

The client sends the vehicle's GPS position to the caster (as GGA, from
`global_position/raw/fix`); the Emlid local caster sends no corrections until
it receives one, so RTK only starts once the vehicle has a normal GPS fix.

**Expected timing** (clear sky, base at 1 Hz, two vehicles on the caster):
from power-on, 3D fix at ~60 s, RTK float within seconds of that, RTK fixed
at ~80–90 s. An FCU reboot alone leaves the Here4 powered (warm), and it
re-fixes within seconds.

The client always runs (`LAUNCH_NTRIP=true`). If the base is off or out of
reach it logs connection errors and retries every ~10 s; MAVROS and the FCU
are unaffected and the fix simply stays at 3/4. Set `LAUNCH_NTRIP=false` to
silence it (e.g. indoors). Checking `fix_type` before flight is the only
gate — nothing arms or takes off based on it.

## 6. Persistent Logs

Without this setup, every log that matters is lost sooner or later: the
Jetson's system journal lives in RAM (gone at every reboot or power loss), and
ROS logs written inside the container are gone whenever it is recreated, which
`homebrew-docker.service` does on every clean shutdown via `docker compose
down`. Four pieces fix this:

| Log | Where it goes | Set up by |
|---|---|---|
| ROS logs (`launch.log` records node crashes; one file per node) | `logs/ros/` in this repo, on the host | `docker-compose.yml` (automatic) |
| Container output (what `docker compose logs` shows) | the host's systemd journal | `docker-compose.yml` + the script below |
| Kernel and system messages (USB disconnects, throttling, segfaults) | the host's systemd journal | the script below |
| Core dumps (e.g. a MAVROS segfault) | `/var/lib/systemd/coredump/` | `docker-compose.yml` + the script below |

Set up by `scripts/setup-persistent-logs.sh` in [3.2](#32-persistent-logs-and-machine-id),
followed by a reboot.

**Power loss (battery pull, crash).** The journal keeps new messages in
memory and saves them to disk periodically; whatever is not saved yet when
power is cut is lost. Ubuntu's default saves every 5 minutes (plus Linux's own
write-out after about 30 s), so the last tens of seconds before a crash could
be missing. The script sets `SyncIntervalSec=1s`: the journal is saved every
second. Measured on homebrew05 (2026-10-14, one battery pull, a numbered
message logged every 0.2 s and also sent to the lab PC over SSH): the last
saved message was 0.4–0.6 s before power was cut. The cost is one disk sync
per second of a few KB (the journal grows by about 3 KB/s), done by
journald, not by the ROS nodes.

> [!NOTE]
> Files in `logs/ros/` are ordinary files and are **not** covered by this
> setting: Linux writes them out after about 30 s (`vm.dirty_expire_centisecs`).
> After a crash, the journal is the more complete record of the final
> seconds. The PX4 flight log on the flight controller's SD card is separate.

The script also gives the Jetson its own **machine ID**. Jetsons flashed from
the same image share one (`5dbfb124...` on the homebrews), so their journals
land in identically named `/var/log/journal/<machine-id>` directories, and
anything else that identifies the machine by it sees the same machine twice.
Regenerating it takes two files: `systemd-machine-id-setup` copies the old ID
back from `/var/lib/dbus/machine-id` if that file still exists, so the script
removes both, regenerates, and makes the D-Bus file a symlink to
`/etc/machine-id`. It only does this when the ID is still the image's, so
re-running it is harmless. A reboot is needed for the new ID. Journals
written under the old ID stay readable with
`journalctl -D /var/log/journal/5dbfb12414a3456d9014d88183e338b1`.

Reading them:

Container output for this boot, and the previous boot
```bash
journalctl CONTAINER_NAME=autonomypark-homebrew -b
```

Container output for previous boot
```bash
journalctl CONTAINER_NAME=autonomypark-homebrew -b -1
```

Kernel messages from the previous boot (e.g. after a power loss):
```bash
journalctl -k -b -1
```

Did a ROS node die? (launch.log is per launch, under logs/ros/<timestamp>/)
```bash
grep -a "has died" logs/ros/*/launch.log
```

Core dumps
```bash
coredumpctl list
```

> [!WARNING]
> After a battery pull the Jetson boots with its clock at 1970, which can
> make `-b -1` pick the **wrong boot** (seen on homebrew05). To read an
> earlier boot reliably, list the boots and pass the boot ID (second column):
>
> ```bash
> journalctl --list-boots | tail -5
> journalctl -b <boot ID> -o short-monotonic
> ```

`logs/ros/` is written by root from inside the container, so deleting old
logs needs `sudo rm -rf logs/ros/<dir>`. It grows by a few MB per session; the
journal is capped at 2 GB.

## 7. MAVROS Plugin List

MAVROS loads one plugin per MAVLink feature; the upstream PX4 default loads
about 60, each with its own ROS node, topics, services and startup requests to
the FCU. `MAVROS_PLUGINS` in `.env` picks a list from `config/mavros/`:

- `full`: the upstream PX4 list, all plugins but five. Used if
  `MAVROS_PLUGINS` is unset.
- `minimal` (set in `.env.example`): 14 plugins, those that
  `autonomy_park_sitl`'s `ros2_ws` uses, RTK
  needs, and those depend on, plus `distance_sensor` and `px4flow` for
  watching the rangefinder and optical flow. Each entry in
  `config/mavros/minimal_pluginlists.yaml` is commented with the topics and
  services it provides.

**MAVROS version:** 2.16.0, built from source at that tag (the comment in
`docker/Dockerfile` says why not from apt, and what to check before changing
it). Its topics and services are under `/<namespace>/mavros/` (e.g.
`/homebrew05/mavros/state`); 2.14.0, used until 2026-10-15, had them under
`/<namespace>/`. Nodes elsewhere must match: see
`autonomy_park_sitl`'s README for checking the ground PC's MAVROS version.

Measured with MAVROS 2.14.0 (not repeated with 2.16.0) on homebrew02 and
homebrew03 (20 restarts each, alternating; bench, FCU connected): with `full`, every start produced a 184–648 ms companion-link
backlog ("TM: RTT too high" warnings) and MAVROS used ~1 CPU core; with
`minimal`, the timesync RTT never exceeded ~10 ms, there were no warnings, and
MAVROS used ~0.26 of a core. Before flying on `minimal`, confirm outdoors with
an RTK fix that `mavros/global_position/local`, `mavros/global_position/global`
and RTK corrections (`mavros/gpsstatus/gps1/raw` reaching fix type 6) all work.

After changing it, run the commands in
[After setup: changing `homebrew_ws`](#after-setup-changing-homebrew_ws). To check which plugins loaded:

```bash
grep -oE "Plugin [a-z_0-9]+ initialized" logs/ros/mavros_node_*.log | sort | uniq -c
```

Before adding a plugin to `minimal`, check what it depends on: for example,
`global_position` subscribes to `home_position/home`, so `home_position` must
stay even though nothing in `ros2_ws` uses it.

References:
- How the lists are applied (`is_plugin_allowed`): a plugin matching
  `plugin_denylist` loads only if it also matches `plugin_allowlist`; patterns
  are case-insensitive globs.
  <https://github.com/mavlink/mavros/blob/ros2/mavros/src/lib/mavros_uas.cpp>
- Upstream PX4 list: <https://github.com/mavlink/mavros/blob/ros2/mavros/launch/px4_pluginlists.yaml>
- Plugin names: `mavros_plugins.xml` in
  <https://github.com/mavlink/mavros/tree/ros2/mavros> and
  <https://github.com/mavlink/mavros/tree/ros2/mavros_extras>
- Topics per plugin (written for ROS 1; names largely carry over to ROS 2):
  <http://wiki.ros.org/mavros#Plugins> and <http://wiki.ros.org/mavros_extras>

## 8. Flight-Controller Bridge: MAVROS or Micro XRCE-DDS Agent

The Jetson can talk to the flight controller in one of two ways:

| | MAVROS (default) | Micro XRCE-DDS Agent |
|---|---|---|
| Protocol on TELEM2 | MAVLink | uXRCE-DDS (PX4's native DDS bridge) |
| ROS interface | `mavros_msgs`, `/<namespace>/mavros/...` topics and services | `px4_msgs`, `/<namespace>/fmu/out/...` and `/fmu/in/...` topics |
| Topics | chosen by `MAVROS_PLUGINS` ([7](#7-mavros-plugin-list)) | fixed by the PX4 firmware's built-in list (v1.16.2: 24 out, 27 in) |
| RTK corrections | NTRIP client → MAVROS → FCU | **none** (see below) |
| Used by `autonomy_park_sitl` | yes | no |

> [!IMPORTANT]
> **Enable exactly one.** Both use the same serial port (`FCU_URL`). The
> launch file refuses to start if `LAUNCH_MAVROS` and `LAUNCH_XRCE_AGENT` are
> both `true`, and the container restart-loops with that error in its log.
> The FCU's TELEM2 must be configured for the same choice.

The image includes the agent pinned at **v2.4.3** and `px4_msgs` pinned at
**v1.16.2**, matching the vehicles' PX4 firmware. `px4_msgs` must match the
firmware's message definitions; if the firmware is upgraded, bump
`PX4_MSGS_VERSION` in `docker/Dockerfile` and rebuild the image (3.7). Building the
agent adds roughly 15–20 minutes.

Measured on homebrew02/03 (outdoors, 20 container restarts per bridge per
vehicle; PX4 v1.16.2 stock topic list):

| Median | MAVROS (`minimal`) | XRCE agent |
|---|---|---|
| Bridge CPU / memory | 29 % of a core / 202 MB | 4 % / 27 MB |
| Odometry delivered | 30 Hz | ~95–100 Hz |
| Odometry age: median / max | 4.0 / 39 ms | 9.2 / 21 ms |
| Link round trip (timesync) | 2.2 ms | 8.8 ms |
| Container start → first odometry | 4.6 s | 9.5 s |
| RTK | yes | no ([8.2](#82-rtk-with-the-xrce-agent)) |

### 8.1 Switch a vehicle to the XRCE agent

1. **FCU parameters** (QGroundControl → Vehicle Setup → Parameters), then reboot:

   | Parameter | MAVROS | XRCE agent |
   |---|---|---|
   | `MAV_1_CONFIG` | `TELEM 2` | `Disabled` |
   | `UXRCE_DDS_CFG` | `Disabled` | `TELEM 2` |
   | `SER_TEL2_BAUD` | 921600 | 921600 (unchanged) |
   | `UXRCE_DDS_DOM_ID` | — | `0` (must equal `ROS_DOMAIN_ID`) |
   | `UXRCE_DDS_PTCFG` | — | `Custom participant` (2) |

2. **Namespace.** PX4 v1.16 has no parameter for it, so without this step
   every vehicle publishes the same `/fmu/out/...` topics and they mix on the
   network. Set it with the client's `-n` option in the SD card's startup
   file. In QGroundControl → Analyze Tools → MAVLink Console (use your
   vehicle's namespace):

   > [!WARNING]
   > This **overwrites** `/fs/microsd/etc/extras.txt`. The homebrews have none
   > by default; the first line prints any existing one so you can see what
   > would be lost.

   ```
   cat /fs/microsd/etc/extras.txt
   mkdir /fs/microsd/etc
   echo "uxrce_dds_client stop" > /fs/microsd/etc/extras.txt
   echo "uxrce_dds_client start -t serial -d /dev/ttyS1 -b p:SER_TEL2_BAUD -n homebrew05" >> /fs/microsd/etc/extras.txt
   reboot
   ```

   Replace `homebrew05` with your vehicle's namespace. `/dev/ttyS1` is TELEM2
   on the CubeOrange. After the reboot, `uxrce_dds_client status` should show
   it running on `/dev/ttyS1`.

3. **`.env`** on the Jetson:

   ```bash
   cd ~/homebrew_docker
   sed -i "s/^LAUNCH_MAVROS=.*/LAUNCH_MAVROS=false/; s/^LAUNCH_XRCE_AGENT=.*/LAUNCH_XRCE_AGENT=true/" .env
   sudo docker compose up -d --force-recreate
   ```

4. **Check** that the session is up. On the Jetson:

   ```bash
   journalctl CONTAINER_NAME=autonomypark-homebrew -b | grep -i "session established"
   ```

   Then in a terminal on the **lab PC** (replace `05` with the vehicle's
   number):

   ```bash
   NUMBER=05
   ```

   Then:

   ```bash
   ros2 topic list | grep "^/homebrew$NUMBER/fmu/" | wc -l
   ros2 topic list | grep "^/homebrew$NUMBER/fmu/" | head
   ```

   Expect `/<namespace>/fmu/out/...` and `/<namespace>/fmu/in/...` (51 topics
   with v1.16.2). Right after the agent starts, PX4's client typically
   establishes its session three times within ~20 s before it settles.

> [!NOTE]
> **Discovery.** With `UXRCE_DDS_PTCFG = Custom participant`, PX4's client
> asks the agent for a DDS participant named `px4_participant` instead of
> sending its own definition. The launch file passes the agent a reference
> file (`-r`) that defines it to match `USE_DISCOVERY_SERVER`:
> `config/fastdds/xrce_agent_client.xml` (discovery-server client; keep its
> server address in sync with `client_config.xml`) or
> `config/fastdds/xrce_agent_simple.xml` (ordinary multicast discovery).
> With `UXRCE_DDS_PTCFG = Default` (0) the reference file is ignored and the
> agent always uses multicast discovery, so its topics are invisible to
> discovery-server clients.
>
> The agent bundles its own Fast DDS (2.14), separate from ROS 2 Humble's
> (2.6); both speak standard RTPS to the discovery server.

**To switch back to MAVROS:**

1. Set the parameters in the table's MAVROS column (`UXRCE_DDS_PTCFG` back
   to `Default`).
2. In the MAVLink Console, remove the namespace file and reboot:
   ```
   rm /fs/microsd/etc/extras.txt
   reboot
   ```
3. On the Jetson:
   ```bash
   cd ~/homebrew_docker
   sed -i "s/^LAUNCH_MAVROS=.*/LAUNCH_MAVROS=true/; s/^LAUNCH_XRCE_AGENT=.*/LAUNCH_XRCE_AGENT=false/" .env
   sudo docker compose up -d --force-recreate
   ```
4. Check as in [3.10](#310-check-the-vehicle).

### 8.2 RTK with the XRCE agent

PX4 receives RTK corrections as MAVLink `GPS_RTCM_DATA`. Its v1.16 uXRCE-DDS
topic list has no input for them, so with the agent the NTRIP client has
nowhere to send corrections; the launch file does not start it, and the GPS
stays at a 3D fix. Options that need no firmware change: QGroundControl's
built-in NTRIP client (corrections go over the telemetry radio while QGC is
connected), or a second MAVLink link from the Jetson to the FCU's USB port
carrying only corrections (e.g. MAVProxy's `ntrip` module). Neither is set up
here.

## 9. MAVLink Stream Rates (optional)

MAVROS does not choose how often its topics publish: it republishes each
MAVLink message as it arrives, so the rate is whatever PX4 streams on TELEM2.
PX4's defaults for that link (measured on homebrew08, 2026-10-20):

| MAVROS topic(s) | MAVLink message (id) | Default |
|---|---|---|
| `local_position/pose`, `/velocity_local`, `/odom` | `LOCAL_POSITION_NED` (32) | 30 Hz |
| `global_position/global`, `/local`, `/rel_alt`, `/compass_hdg` | `GLOBAL_POSITION_INT` (33) | 50 Hz |
| `altitude` | `ALTITUDE` (141) | 10 Hz |
| `imu/data`; the orientation in MAVROS's pose/odometry topics | `ATTITUDE_QUATERNION` (31) | 50 Hz |

`MAVLINK_RATES` in `.env` asks PX4 for other rates. It is **empty by default**,
and then nothing changes. When set (and `LAUNCH_MAVROS=true`), the launch
starts the `mavlink_rates` node
(`homebrew_ws/src/homebrew_bringup/homebrew_bringup/mavlink_rates.py`), which
calls MAVROS's `mavros/set_message_interval` service for each entry.

PX4 keeps these rates in RAM only, so a PX4 reboot quietly restores its
defaults. That is why the node reapplies them **every time `mavros/state`
reports the FCU connected**, not just once at startup, and retries every 2 s
any rate the FCU does not accept.

### 9.1 Enable

1. In `.env`, as `<message id>:<Hz>` pairs separated by commas. For example,
   100 Hz for `altitude`, `global_position/*` and `local_position/*`:

   ```bash
   MAVLINK_RATES=141:100,33:100,32:100
   ```

2. Run the commands in
   [After setup: changing `homebrew_ws`](#after-setup-changing-homebrew_ws)
   (the node is new code, so the package must be rebuilt; after that,
   changing only `.env` needs just `sudo docker compose up -d --force-recreate`).

3. Check the node's log. It should print one `accepted by FCU` line per
   message:

   ```bash
   sudo docker compose logs homebrew_bringup | grep mavlink_rates
   ```

   A malformed value stops the launch with
   `MAVLINK_RATES must look like 141:100,33:100`.

4. Measure the result from the lab PC, one topic at a time:

   ```bash
   ros2 topic hz /<namespace>/mavros/local_position/velocity_local
   ```

To turn it off, empty `MAVLINK_RATES` and recreate the container; PX4 returns
to its defaults only when it reboots.

To try a rate once without the node (lost when PX4 reboots):

```bash
ros2 service call /<namespace>/mavros/set_message_interval \
  mavros_msgs/srv/MessageInterval "{message_id: 32, message_rate: 100.0}"
```

### 9.2 Caveats

- **Serial bandwidth.** TELEM2 runs at 921600 baud, about 92 KB/s. With
  `MAV_1_RATE = 0` (TELEM2's instance, 3.3), PX4 allows itself only part of that, and if the streams
  ask for more than its limit it **slows every stream on the link
  proportionally**, including the ones you did not change. Check in
  QGroundControl → Analyze Tools → MAVLink Console with `mavlink status`: the
  TELEM2 instance shows its tx rate and rate multiplier, which should stay
  1.0. The example above adds about 12 KB/s.
- **Topics you didn't name speed up too.** Each message feeds several
  topics (table above).
- **Orientation stays at the attitude rate.** MAVROS fills pose/odometry
  orientation from the latest `ATTITUDE_QUATERNION`, so at 100 Hz half the
  samples repeat orientation unless you also add `31:100`.
- **Millisecond timestamps.** `LOCAL_POSITION_NED` and `GLOBAL_POSITION_INT`
  carry `time_boot_ms`. At 100 Hz, the time between header stamps can be off
  by up to about 10 %. Controllers that differentiate or integrate should
  smooth dt or use a nominal value.
- **Some messages can't go faster.** PX4 sends a message only when its source
  updates; `ALTITUDE` in particular may come out below the requested rate.
  Measure it.
- **The estimator runs at a fixed rate.** EKF2 updates at roughly
  100–250 Hz, so asking for more than about 100–150 Hz gains little. For
  faster or lower-latency odometry, see the XRCE agent
  ([8](#8-flight-controller-bridge-mavros-or-micro-xrce-dds-agent)).
- **Watch link health after enabling.** Check that `mavros/timesync_status`
  `round_trip_time_ms` stays at a few ms as it does at the defaults.

## Notes & Troubleshooting

**MAVROS does not restart if it crashes**

If `mavros_node` dies (e.g. a segfault, `exit code -11`), its topics vanish
and stay gone until the container or Jetson is restarted. Docker's
`restart: unless-stopped` does not help, because `ros2 launch` keeps running
after one of its nodes dies. Earlier releases had a `MAVROS_RESPAWN` setting,
but it never did anything: MAVROS's upstream `node.launch` accepts a
`respawn_mavros` argument and never applies it to the node. It has been
removed; a leftover `MAVROS_RESPAWN=` line in an old `.env` is ignored.
To check whether MAVROS died, look in the launch logs (on the host since
[6](#6-persistent-logs)), and for a core dump:

```bash
grep -a "mavros_node-1" logs/ros/*/launch.log | grep -a "has died"
coredumpctl list
```

**Using the Micro USB connection instead of FTDI**

To troubleshoot the flight controller over its Micro USB port instead of the
FTDI cable, point `/dev/ttyFC` at it (the FTDI rule is replaced; run 3.4
again to switch back):

```bash
cd ~/homebrew_docker
sudo VENDOR_ID=2dae PRODUCT_ID=1016 bash scripts/setup-udev-fc.sh 0
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
> When running the image for the first time, the ZED AI models need to download. Make sure the Jetson is connected to the Internet for that first run. Once the models have downloaded successfully, the container can run without Internet.

> [!IMPORTANT]
> Always double-check the discovery server IP in both
> `config/fastdds/client_config.xml` and `config/fastdds/super_client_config.xml`.

### Inspecting the ROS graph from the lab PC

Run `ros2` commands on the **lab PC**, not on the Jetson. The Jetson has no
ROS 2. The only `ros2`/`colcon` use on the Jetson is
building `homebrew_ws` in the build shell (3.8 and
[After setup](#after-setup-changing-homebrew_ws)).

Useful commands:

```bash
ros2 topic list
ros2 topic info -v <topic>   # which nodes (and namespaces) publish/subscribe
ros2 topic hz <topic>        # is anything actually arriving? (Ctrl-C to stop)
ros2 param get <node> <param>
```

### No topics from a vehicle on the lab PC

The container is running, the logs show `Got HEARTBEAT, connected`, but
`ros2 topic list` on the lab PC shows nothing from the vehicle. Check whether
the Jetson's firewall is dropping the discovery server's replies. On the
Jetson:

```bash
sudo ufw status verbose
journalctl -k -b | grep "UFW BLOCK" | grep "SRC=192.168.1.201" | tail -5
```

Any `UFW BLOCK` lines from `192.168.1.201` mean the firewall is the cause. Run
1.6, then wait about 30 seconds: the nodes keep retrying, so no restart is
needed.

### Multiple vehicles on one network

Every vehicle's topics must live under its own namespace
(`MAVROS_NAMESPACE`, also used for the ZED). Two things defeat namespacing,
and both are handled:

- **MAVROS's internal link** (`/uas<N>/mavlink_{source,sink}`) is keyed by
  the system ID, not the namespace — see
  [3.3](#33-flight-controller-parameters).
- **Topics nodes use under absolute names** (`/tf`, `/tf_static`,
  `/diagnostics`, `/move_base_simple/goal`) are remapped under the
  namespace for every node the launch file starts (`ABSOLUTE_TOPICS` in
  `homebrew.launch.py`). With MAVROS 2.16 this works for `/tf`,
  `/tf_static` and `/diagnostics` (published by MAVROS's main and router
  nodes), but MAVROS's plugin nodes ignore remaps: with `MAVROS_PLUGINS=full`
  the `guided_target` plugin's `/move_base_simple/goal` stays absolute and is
  shared by every vehicle. `minimal` does not load that plugin (checked
  2026-10-15 with MAVROS 2.16.0 on the lab PC: no MAVROS topic outside the
  namespace except `/uas<N>/mavlink_*`). Each vehicle therefore has its own TF tree on
  `/<namespace>/tf`; frame names (`base_link`, `map`, ...) are the same on
  every vehicle. Tools that need TF (e.g. RViz) must remap `/tf` and
  `/tf_static` to the vehicle's topics.

Audit after adding nodes or packages (on the lab PC, [Inspecting the ROS graph](#inspecting-the-ros-graph-from-the-lab-pc)): anything outside the vehicle
namespaces other than `/rosout` and `/parameter_events` (shared by design in
ROS 2) and `/uas<N>/*` is shared by every vehicle and must be added to
`ABSOLUTE_TOPICS`:

```bash
ros2 topic list | grep -vE '^/homebrew[0-9]+/|^/uas[0-9]+/|^/rosout$|^/parameter_events$'
```

**One vehicle's data appears in another vehicle's topics**

Check `ros2 topic info -v` on the affected topic (on the lab PC,
[Inspecting the ROS graph](#inspecting-the-ros-graph-from-the-lab-pc)). If it has exactly one
publisher in the right namespace, the mixing happens upstream of ROS — look
at the `/uas<N>` link topics. Two vehicles on the same `/uas<N>` means their
system IDs collide; see [3.3](#33-flight-controller-parameters). Otherwise
run the audit above.

**Scaling to many vehicles.** Data only crosses Wi-Fi for topics something
on another machine subscribes to, so MAVROS telemetry to a ground station
is small; keep camera images and point clouds on the vehicle or compress
them. RTCM is ~0.5–1 KB/s per vehicle at 1 Hz, but see the caster limit in
[5](#5-rtk-corrections).

**A vehicle's topics are missing after boot**

Fast DDS chooses which network addresses to advertise when a node starts and
never re-scans. If ROS starts before Wi-Fi connects, the vehicle's nodes run
but are invisible to everything else, including `ros2` commands in its own
container. Before starting ROS, the entrypoint waits for two things:

1. **Network:** `wlan0` has an IPv4 address and a default route.
2. **Clock:** the clock agrees with the lab time server (192.168.1.201)
   within 10 ms on 3 checks in a row
   (see [docs/TIME_SYNC.md](docs/TIME_SYNC.md)).

> [!IMPORTANT]
> **No time server, no ROS.** If either wait times out
> (`NETWORK_WAIT_TIMEOUT`, default 120 s; `CLOCK_WAIT_TIMEOUT`, default 60 s),
> the container exits without starting ROS and Docker restarts it, which
> repeats until both pass. A vehicle that cannot reach the discovery server
> (192.168.1.201) therefore publishes **no topics at all**. This is on
> purpose: topics stamped by a wrong clock look like a working vehicle.

Check with:

```bash
cd ~/homebrew_docker
sudo docker compose logs homebrew_bringup | grep -E "Network up|No IPv4|Clock"
```

Expect a `Network up` line and a `Clock in sync with 192.168.1.201` line.
Repeated `Clock not yet in sync` lines ending in `ROS is not started` mean the
vehicle cannot get time from the discovery server. On the vehicle:

```bash
chronyc sources
```

The `192.168.1.201` line must start with `^*`. If it does not, check that the
discovery server is on and reachable (`ping -c 3 192.168.1.201`) and that
chrony runs there (`chronyc tracking` on the server; docs/TIME_SYNC.md §1).

> [!NOTE]
> Docker's restart policy (`restart: unless-stopped`) starts the container as
> soon as Docker starts if the container still exists, i.e. after the vehicle
> was powered off without a shutdown (battery unplugged). A clean shutdown
> removes it (`homebrew-docker.service` runs `docker compose down`). Either
> way the entrypoint waits for the network and the clock first. After a
> battery pull the Jetson's clock starts at 1970 and is corrected about 20 s
> after boot; ROS starts after that.

If a vehicle's IP address changes while the container is running (e.g. its
router reservation is missing), restart the container.

> [!NOTE]
> `docker ps` showing a container "Up 56 years" means it started before the
> clock was set (epoch 0) and is harmless on its own.

## TO-DO:
- Add static transform publisher for Zed using Homebrew CAD file