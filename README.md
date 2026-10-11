# homebrew_docker

Docker stack for MAVROS and ZED SDK, deployed on a Jetson Orin Nano running JetPack 6.0 rev2 (L4T r36.3.0, CUDA 12.2).

## Table of Contents

- [1. First-Time Jetson Setup](#1-first-time-jetson-setup)
- [2. Build the ZED Base Image](#2-build-the-zed-base-image)
- [3. Configure homebrew_docker](#3-configure-homebrew_docker)
- [4. Optional: Auto-Start on Boot](#4-optional-auto-start-on-boot)
- [5. RTK Corrections](#5-rtk-corrections)
- [6. Persistent Logs](#6-persistent-logs)
- [7. MAVROS Plugin List](#7-mavros-plugin-list)
- [8. Flight-Controller Bridge: MAVROS or Micro XRCE-DDS Agent](#8-flight-controller-bridge-mavros-or-micro-xrce-dds-agent)
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

Edit `.env` with the details your Jetson needs. `USE_DISCOVERY_SERVER`,
`FCU_URL`, `MAVROS_NAMESPACE`, `MAVROS_TGT_SYSTEM` and the `NTRIP_*` settings
are required: `docker compose` refuses to start without them rather than guess.
`MAVROS_TGT_SYSTEM` is covered in [3.4](#34-set-a-unique-mavlink-system-id)
and NTRIP in [5](#5-rtk-corrections).

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

> [!WARNING]
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
keep a record of which IDs are taken. **Never use 1:** Freefly forces every
Astro to `MAV_SYS_ID=1` and it can't be changed, so `/uas1` belongs to the
Astros (see the astro_mavros README).

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

Once per Jetson, before the first `up`, set up persistent logs and a unique
machine ID ([6](#6-persistent-logs)); reboot if the script says so:

```bash
sudo ./scripts/setup-persistent-logs.sh
```

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

## 5. RTK Corrections

The container runs an NTRIP client
([ntrip_client](https://github.com/LORD-MicroStrain/ntrip_client), pinned in
the Dockerfile) that pulls RTCM corrections from the base station's caster
over Wi-Fi and hands them to MAVROS (`/<namespace>/gps_rtk/send_rtcm`), which
sends them to the FCU. No ground-station MAVProxy is needed.

1. On the FCU, set `UAVCAN_PUB_RTCM` to Enabled and reboot. The Here4 is a
   DroneCAN GPS, so PX4 must forward RTCM onto the CAN bus.
2. Set the `NTRIP_*` values in `.env` to the caster (for the Emlid base's
   local caster: its IP, port 2101, mountpoint, username and password).
3. Before takeoff, check the fix:
   ```bash
   ros2 topic echo /<namespace>/gpsstatus/gps1/raw --field fix_type
   ```
   `6` is RTK fixed, `5` RTK float, `3`/`4` no RTK.

The client sends the vehicle's GPS position to the caster (as GGA, from
`global_position/raw/fix`); the Emlid local caster sends no corrections until
it receives one, so RTK only starts once the vehicle has a normal GPS fix.

**Base station (Emlid RS3+) settings.** In Emlid Flow → Base output →
RTCM3, send the MSM messages (1074, 1084, 1094, 1124) and 1230 at **1 Hz**,
1006 at 0.1 Hz or more. Measured from a cold start, float to fixed took
~4 min with MSM at 0.5 Hz and 15–22 s at 1 Hz. Faster than 1 Hz gains
little for time-to-fix and multiplies caster load and bandwidth.

> [!WARNING]
> The RS3+'s local caster served at most **two** clients at once in testing:
> a third client (with or without GGA) received no data at all while two
> vehicles were connected. It does not reject the extra client, so it looks
> connected. For three or more RTK vehicles, run one NTRIP client or caster
> (e.g. SNIP, or Emlid's cloud caster) that fans the stream out, rather than
> connecting every vehicle to the base.

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
down`. Three pieces fix this:

| Log | Where it goes | Set up by |
|---|---|---|
| ROS logs (`launch.log` records node crashes; one file per node) | `logs/ros/` in this repo, on the host | `docker-compose.yml` (automatic) |
| Container output (what `docker compose logs` shows) | the host's systemd journal | `docker-compose.yml` + the script below |
| Kernel and system messages (USB disconnects, throttling, segfaults) | the host's systemd journal | the script below |
| Core dumps (e.g. a MAVROS segfault) | `/var/lib/systemd/coredump/` | `docker-compose.yml` + the script below |

Run this once per Jetson (it needs sudo), then recreate the container:

```bash
sudo ./scripts/setup-persistent-logs.sh
docker compose up -d
```

The script also gives the Jetson its own **machine ID**. Jetsons flashed from
the same image share one (`5dbfb124...` on the homebrews), so their journals
land in identically named `/var/log/journal/<machine-id>` directories, and
anything else that identifies the machine by it sees the same machine twice.
Regenerating it takes two files: `systemd-machine-id-setup` copies the old ID
back from `/var/lib/dbus/machine-id` if that file still exists, so the script
removes both, regenerates, and makes the D-Bus file a symlink to
`/etc/machine-id`. It only does this when the ID is still the image's, so
re-running it is harmless. **Reboot when it says the ID changed.** Journals
written under the old ID stay readable with
`journalctl -D /var/log/journal/5dbfb12414a3456d9014d88183e338b1`.

Reading them:

```bash
# Container output for this boot, and the previous boot
journalctl CONTAINER_NAME=autonomypark-homebrew -b
journalctl CONTAINER_NAME=autonomypark-homebrew -b -1
# Kernel messages from the previous boot (e.g. after a power loss)
journalctl -k -b -1
# Did a ROS node die? (launch.log is per launch, under logs/ros/<timestamp>/)
grep -a "has died" logs/ros/*/launch.log
# Core dumps
coredumpctl list
```

`logs/ros/` is written by root from inside the container, so deleting old
logs needs `sudo rm -rf logs/ros/<dir>`. It grows by a few MB per session; the
journal is capped at 2 GB.

## 7. MAVROS Plugin List

MAVROS loads one plugin per MAVLink feature; the upstream PX4 default loads
about 60, each with its own ROS node, topics, services and startup requests to
the FCU. `MAVROS_PLUGINS` in `.env` picks a list from `config/mavros/`:

- `full` (default): the upstream PX4 list, all plugins but five.
- `minimal`: the 12 plugins that `autonomy_park_sitl`'s `ros2_ws` uses, RTK
  needs, and those depend on. Each entry in
  `config/mavros/minimal_pluginlists.yaml` is commented with the topics and
  services it provides.

Measured on homebrew02 and homebrew03 (20 restarts each, alternating; bench,
FCU connected): with `full`, every start produced a 184–648 ms companion-link
backlog ("TM: RTT too high" warnings) and MAVROS used ~1 CPU core; with
`minimal`, the timesync RTT never exceeded ~10 ms, there were no warnings, and
MAVROS used ~0.26 of a core. Before flying on `minimal`, confirm outdoors with
an RTK fix that `global_position/local`, `global_position/global` and RTK
corrections (`gpsstatus/gps1/raw` reaching fix type 6) all work.

After changing it, rebuild `homebrew_bringup` (3.5) if the launch file changed,
then `docker compose up -d`. To check which plugins loaded:

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
| ROS interface | `mavros_msgs`, `/<namespace>/...` topics and services | `px4_msgs`, `/<namespace>/fmu/out/...` and `/fmu/in/...` topics |
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
`PX4_MSGS_VERSION` in `docker/Dockerfile` and rebuild the image. Building the
agent adds roughly 15–20 minutes to `docker compose build`.

### 8.1 Switch a vehicle to the XRCE agent

1. **FCU parameters** (QGroundControl → Vehicle Setup → Parameters), then reboot:

   | Parameter | MAVROS | XRCE agent |
   |---|---|---|
   | `MAV_1_CONFIG` | `TELEM 2` | `Disabled` |
   | `UXRCE_DDS_CFG` | `Disabled` | `TELEM 2` |
   | `SER_TEL2_BAUD` | 921600 | 921600 (unchanged) |
   | `UXRCE_DDS_DOM_ID` | — | `0` (must equal `ROS_DOMAIN_ID`) |

2. **Namespace.** PX4 v1.16 has no parameter for it, so without this step
   every vehicle publishes the same `/fmu/out/...` topics and they mix on the
   network. Set it with the client's `-n` option in the SD card's startup
   file. In QGroundControl → Analyze Tools → MAVLink Console (use your
   vehicle's namespace):

   ```
   cat /fs/microsd/etc/extras.txt
   mkdir /fs/microsd/etc
   echo "uxrce_dds_client stop" > /fs/microsd/etc/extras.txt
   echo "uxrce_dds_client start -t serial -d /dev/ttyS1 -b p:SER_TEL2_BAUD -n homebrew03" >> /fs/microsd/etc/extras.txt1
   reboot
   ```

   The first `cat` shows whether an `extras.txt` already exists; if it does,
   append the two lines to it instead of overwriting. `/dev/ttyS1` is TELEM2
   on the CubeOrange. After the reboot, `uxrce_dds_client status` should show
   it running on `/dev/ttyS1`.

3. **`.env`** on the Jetson: `LAUNCH_MAVROS=false`, `LAUNCH_XRCE_AGENT=true`,
   then `docker compose up -d`.

4. **Check** that topics arrive (the image's `ros2` CLI, as in
   [Inspecting the ROS graph](#inspecting-the-ros-graph)):

   ```bash
   docker exec -e FASTRTPS_PROFILE_PATH=/etc/fastdds/super_client_config.xml \
     autonomypark-homebrew /sbin/homebrew_ros_entrypoint.sh ros2 topic list | grep fmu
   journalctl CONTAINER_NAME=autonomypark-homebrew -b | grep -i "session established"
   ```

To switch back: reverse the parameter table, delete the two lines from
`extras.txt` (or the file, `rm /fs/microsd/etc/extras.txt`, if you created
it), reboot the FCU, and set `LAUNCH_MAVROS=true`, `LAUNCH_XRCE_AGENT=false`.

### 8.2 RTK with the XRCE agent

PX4 receives RTK corrections as MAVLink `GPS_RTCM_DATA`. Its v1.16 uXRCE-DDS
topic list has no input for them, so with the agent the NTRIP client has
nowhere to send corrections; the launch file does not start it, and the GPS
stays at a 3D fix. Options that need no firmware change: QGroundControl's
built-in NTRIP client (corrections go over the telemetry radio while QGC is
connected), or a second MAVLink link from the Jetson to the FCU's USB port
carrying only corrections (e.g. MAVProxy's `ntrip` module). Neither is set up
here.

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
> Always double-check the discovery server IP in both
> `config/fastdds/client_config.xml` and `config/fastdds/super_client_config.xml`.

### Inspecting the ROS graph

Run ROS 2 commands through the container's entrypoint, which sources every
workspace and applies the discovery server profile, and select the
`SUPER_CLIENT` profile. The vehicle's nodes run as plain discovery-server
`CLIENT`s, which only receive discovery data for topics they use; a `ros2`
command run the same way would see an almost empty graph.

```bash
docker exec -e FASTRTPS_PROFILE_PATH=/etc/fastdds/super_client_config.xml \
  autonomypark-homebrew /sbin/homebrew_ros_entrypoint.sh ros2 topic list
```

The `ros2` daemon keeps the profile it was started with. If commands see an
empty graph, run `ros2 daemon stop` the same way and retry.

Useful commands:

```bash
ros2 topic info -v <topic>   # which nodes (and namespaces) publish/subscribe
ros2 topic hz <topic>        # is anything actually arriving?
ros2 param get <node> <param>
```

### Multiple vehicles on one network

Every vehicle's topics must live under its own namespace
(`MAVROS_NAMESPACE`, also used for the ZED). Two things defeat namespacing,
and both are handled:

- **MAVROS's internal link** (`/uas<N>/mavlink_{source,sink}`) is keyed by
  the system ID, not the namespace — see
  [3.4](#34-set-a-unique-mavlink-system-id).
- **Topics nodes use under absolute names** (`/tf`, `/tf_static`,
  `/diagnostics`, `/move_base_simple/goal`) are remapped under the
  namespace for every node the launch file starts (`ABSOLUTE_TOPICS` in
  `homebrew.launch.py`). Each vehicle therefore has its own TF tree on
  `/<namespace>/tf`; frame names (`base_link`, `map`, ...) are the same on
  every vehicle. Tools that need TF (e.g. RViz) must remap `/tf` and
  `/tf_static` to the vehicle's topics.

Audit after adding nodes or packages: anything outside the vehicle
namespaces other than `/rosout` and `/parameter_events` (shared by design in
ROS 2) and `/uas<N>/*` is shared by every vehicle and must be added to
`ABSOLUTE_TOPICS`:

```bash
ros2 topic list | grep -vE '^/homebrew[0-9]+/|^/uas[0-9]+/|^/rosout$|^/parameter_events$'
```

**One vehicle's data appears in another vehicle's topics**

Check `ros2 topic info -v` on the affected topic. If it has exactly one
publisher in the right namespace, the mixing happens upstream of ROS — look
at the `/uas<N>` link topics. Two vehicles on the same `/uas<N>` means their
system IDs collide; see [3.4](#34-set-a-unique-mavlink-system-id). Otherwise
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