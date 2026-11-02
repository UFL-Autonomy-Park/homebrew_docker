# RTK corrections over uXRCE-DDS: flight test

This branch (`rtk_tests_for_px4_1_18`) exists only to test
[PX4 PR #29368](https://github.com/PX4/PX4-Autopilot/pull/29368) on a real
vehicle. It is never merged into `main`; main's changes are merged into it.
For everything not about this test (first-time Jetson setup, MAVROS, time
sync, persistent logs) see the README on `main`.

## What is being tested

PR #29368 lets a ROS 2 companion send RTK corrections to PX4 over uXRCE-DDS
on `/fmu/in/rtcm_corrections` (`px4_msgs/msg/RtcmData`), instead of MAVLink
`GPS_RTCM_DATA`. The PR was tested only in SITL; **this test is the first with
a real receiver** (Here4 on DroneCAN).

The path under test, with MAVROS **off**:

```
NTRIP caster (Emlid base, 192.168.1.201:2101)
  -> ntrip_client            (Jetson, container)
  -> rtcm_xrce_bridge        (Jetson, container; splits frames into <=300-byte chunks)
  -> Micro XRCE-DDS Agent    (Jetson, container) -> TELEM2 serial
  -> PX4 uxrce_dds_client -> uORB rtcm_corrections -> UAVCAN RTCMStream
  -> Here4 -> RTK fixed (fix_type 6)
```

**The feature works if the receiver reaches and holds RTK fixed with this as
the only correction path.** Do not fly until [step 4](#4-bench-gate-rtk-fixed-over-xrce)
has shown that on the bench.

`rtk_test/` holds the logging and analysis kit. `analyze_run.py` checks the
correction stream byte for byte at every hop (caster, ntrip_client, DDS, inside
PX4, receiver) and judges each run against `rtk_test/criteria.json`, which
must not change after testing starts.

## Versions

Everything is pinned to the PR's head commit, `4dfcb7b4370ec7e03471038e821578d5794ec738`
(checked on 2026-11-02; if the PR gets new commits, update
`PX4_FIRMWARE_REF` in `docker/Dockerfile` and rebuild both the firmware and
the image):

| What | Where | Pinned to |
|---|---|---|
| PX4 firmware on the FCU | built on the lab PC, [step 2](#2-flight-controller) | `4dfcb7b4` |
| `px4_msgs` in the image | `docker/Dockerfile` (`PX4_FIRMWARE_REF`) | `4dfcb7b4` |
| Micro XRCE-DDS Agent | `docker/Dockerfile` | v2.4.3 |

The message definitions in the image must match the firmware's, or DDS topics
silently fail to deserialize. `analyze_run.py` compares the two and reports a
mismatch.

## 1. Lab PC (ground station)

### 1.1 Get this branch

In a terminal on the **lab PC**:

```bash
cd ~/homebrew_docker
git fetch origin
git checkout rtk_tests_for_px4_1_18
git pull
git submodule update --init --recursive
python3 -m pip install --user pyulog numpy matplotlib
```

### 1.2 Build the PX4 firmware

Once, or after the PR gets new commits. It clones the PR's branch,
`dakejahl/dds-rtcm-corrections` in PX4/PX4-Autopilot. It uses a separate checkout so the
SITL one in `~/autonomy_park_sitl` is untouched, and PX4's own build container
so the compiler is the one PX4 expects. The board is the CubeOrange (from the
main README); `ver all` in [3.4](#34-check-the-flight-controller) prints the
hardware to confirm it.

In a terminal on the **lab PC**:

```bash
git clone --branch dakejahl/dds-rtcm-corrections https://github.com/PX4/PX4-Autopilot.git ~/px4_pr29368
cd ~/px4_pr29368
git rev-parse HEAD
git submodule update --init --recursive
./Tools/docker_run.sh "make cubepilot_cubeorange_default"
ls build/cubepilot_cubeorange_default/*.px4
```

`git rev-parse HEAD` must print `4dfcb7b4370ec7e03471038e821578d5794ec738`,
the `PX4_FIRMWARE_REF` the image's `px4_msgs` is built from. If the branch has
moved on, update `PX4_FIRMWARE_REF` in `docker/Dockerfile` to the new commit,
push, and rebuild the image (3.2) so the two match.

## 2. Flight controller

The test itself uses only the telemetry radio, the link QGroundControl uses.
Flashing (2.1) is the one step that needs USB: QGroundControl flashes over USB
only.

### 2.1 Flash

QGroundControl → Vehicle Setup → Firmware. Plug the FCU into the lab PC by
USB when asked, tick **Advanced settings**, choose **Custom firmware file**,
and select `~/px4_pr29368/build/cubepilot_cubeorange_default/cubepilot_cubeorange_default.px4`.

### 2.2 Parameters

QGroundControl → Vehicle Setup → Parameters, then reboot:

| Parameter | Value | Why |
|---|---|---|
| `MAV_1_CONFIG` | `Disabled` | TELEM2 carries uXRCE-DDS, not MAVLink |
| `UXRCE_DDS_CFG` | `TELEM 2` | |
| `SER_TEL2_BAUD` | `921600` | matches `FCU_URL` |
| `UXRCE_DDS_DOM_ID` | `0` | equals `ROS_DOMAIN_ID` |
| `UXRCE_DDS_PTCFG` | `Custom participant` (2) | discovery-server participant (main README 8.1) |
| `UAVCAN_PUB_RTCM` | `1` | PX4 forwards corrections to the Here4 over CAN |
| `SDLOG_MODE` | `from boot until shutdown` (2) | the default logs only while armed, so bench runs would have no log |

### 2.3 Namespace

PX4's `uxrce_dds_client` needs `-n homebrewNN` so its topics are under the
vehicle's namespace. This overwrites `/fs/microsd/etc/extras.txt`; the first
line prints what is there. In QGroundControl → Analyze Tools → MAVLink Console
(use the vehicle's namespace in place of `homebrew05`):

```text
cat /fs/microsd/etc/extras.txt
mkdir /fs/microsd/etc
echo "uxrce_dds_client stop" > /fs/microsd/etc/extras.txt
echo "uxrce_dds_client start -t serial -d /dev/ttyS1 -b p:SER_TEL2_BAUD -n homebrew05" >> /fs/microsd/etc/extras.txt
reboot
```

### 2.4 Logger topics

Once per vehicle: `rtk_test/logger_topics.txt` on the SD card adds every
correction chunk and every receiver sample to the ULog. PX4 reads it from
`/fs/microsd/etc/logging/logger_topics.txt` at boot. The lines below are the
file's entries without its comments; if you change the file, change these too.
In QGroundControl → Analyze Tools → MAVLink Console:

```text
mkdir /fs/microsd/etc
mkdir /fs/microsd/etc/logging
echo "rtcm_corrections 0 0" > /fs/microsd/etc/logging/logger_topics.txt
echo "rtcm_corrections 0 1" >> /fs/microsd/etc/logging/logger_topics.txt
echo "rtcm_corrections 0 2" >> /fs/microsd/etc/logging/logger_topics.txt
echo "rtcm_corrections 0 3" >> /fs/microsd/etc/logging/logger_topics.txt
echo "sensor_gnss 0 0" >> /fs/microsd/etc/logging/logger_topics.txt
echo "sensor_gnss 0 1" >> /fs/microsd/etc/logging/logger_topics.txt
echo "vehicle_gnss 0 0" >> /fs/microsd/etc/logging/logger_topics.txt
echo "sensors_status_gnss 0 0" >> /fs/microsd/etc/logging/logger_topics.txt
echo "timesync_status 0 0" >> /fs/microsd/etc/logging/logger_topics.txt
echo "timesync_status 0 1" >> /fs/microsd/etc/logging/logger_topics.txt
echo "timesync_status 0 2" >> /fs/microsd/etc/logging/logger_topics.txt
echo "logger_status 0 0" >> /fs/microsd/etc/logging/logger_topics.txt
echo "logger_status 0 1" >> /fs/microsd/etc/logging/logger_topics.txt
cat /fs/microsd/etc/logging/logger_topics.txt
reboot
```

`mkdir` reports an error for a directory that already exists; that is fine.
`cat` must print the 13 lines.

## 3. Jetson

### 3.1 Get this branch

In an SSH session on the **Jetson**:

```bash
cd ~/homebrew_docker
git fetch origin
git checkout rtk_tests_for_px4_1_18
git pull
git submodule update --init --recursive
```

### 3.2 Build the image and homebrew_ws

The image build is always run: when the Dockerfile has not changed, Docker's
cache makes it take seconds; when it has (a new `PX4_FIRMWARE_REF`), it
rebuilds `px4_msgs` and everything after it. Build with exactly this command
(main README 3.7 explains why not `docker compose build`).

On the **Jetson**:

```bash
cd ~/homebrew_docker
sudo docker buildx bake --allow=network.host -f docker-compose.yml
sudo docker run --rm --entrypoint cat ufl-autonomy-park/homebrew-jetson:dev /opt/ros/humble/share/px4_msgs/build_info
```

`build_info` must show `PX4_FIRMWARE_REF=4dfcb7b4370ec7e03471038e821578d5794ec738`.

Then build `homebrew_ws` (it holds `rtcm_xrce_bridge`). On the **Jetson**:

```bash
cd ~/homebrew_docker
sudo docker compose run --rm homebrew_bringup bash
```

In the **container's** shell:

```bash
colcon build
exit
```

Back on the **Jetson**:

```bash
cd ~/homebrew_docker
sudo chown -R "$USER:$USER" homebrew_ws/build homebrew_ws/install homebrew_ws/log
ls homebrew_ws/install/homebrew_bringup/lib/homebrew_bringup/
```

`ls` must list `rtcm_xrce_bridge`.

### 3.3 Select the XRCE agent and start

This sets `.env` to the agent (MAVROS off) with the NTRIP client on, and
recreates the container. On the **Jetson**:

```bash
cd ~/homebrew_docker
sed -i "s/^LAUNCH_MAVROS=.*/LAUNCH_MAVROS=false/; s/^LAUNCH_XRCE_AGENT=.*/LAUNCH_XRCE_AGENT=true/; s/^LAUNCH_NTRIP=.*/LAUNCH_NTRIP=true/" .env
grep -E "^(LAUNCH_MAVROS|LAUNCH_XRCE_AGENT|LAUNCH_NTRIP|NTRIP_HOST|NTRIP_MOUNTPOINT|MAVROS_NAMESPACE|CONTAINER_NAME)=" .env
sudo docker compose up -d --force-recreate
```

Check the `grep` output: `LAUNCH_MAVROS=false`, `LAUNCH_XRCE_AGENT=true`,
`LAUNCH_NTRIP=true`, and `MAVROS_NAMESPACE` equal to the namespace in 2.3.

### 3.4 Check the flight controller

In QGroundControl → Analyze Tools → MAVLink Console:

```text
ver all
param show SDLOG_MODE
param show UAVCAN_PUB_RTCM
param show UXRCE_DDS_CFG
cat /fs/microsd/etc/logging/logger_topics.txt
```

Expect: `HW arch: CUBEPILOT_CUBEORANGE`, `PX4 git-hash:
4dfcb7b4370ec7e03471038e821578d5794ec738`, `SDLOG_MODE` 2,
`UAVCAN_PUB_RTCM` 1, `UXRCE_DDS_CFG` 102 (TELEM 2), and the 13 lines from 2.4.

`dmesg` cannot show whether PX4 read the file: its buffer is small and the
boot messages are gone by the time the console opens. The bench analysis (4.5)
shows it instead: without the file, hop 3 (`rtcm_corrections` inside PX4) is
INCOMPLETE.

## 4. Bench gate: RTK fixed over XRCE

**Do not fly until every check here passes.** Vehicle on its battery, props
off, outdoors with open sky, base station running, QGroundControl connected
over the telemetry radio, no USB cable.

### 4.1 Nothing else can deliver corrections

The test only proves something if XRCE is the sole path:

- MAVROS is off (3.3) and TELEM2 speaks no MAVLink (2.2).
- QGroundControl does not supply NTRIP corrections in this setup, so nothing
  reaches PX4 as MAVLink `GPS_RTCM_DATA` over the telemetry radio either.

### 4.2 The caster tap (whole session)

An independent record of what the base sent (hop 0). Start it before the
first run and leave it running until the last one stops. In its own terminal
on the **lab PC**:

```bash
cd ~/homebrew_docker
python3 rtk_test/caster_tap.py --env-file .env.example --out ~/rtk_sessions/$(date +%F)/tap
```

### 4.3 Live checks

Run each of these on the **Jetson** and check the result before the next.

The agent session with PX4 is up:

```bash
cd ~/homebrew_docker
sudo docker compose logs homebrew_bringup | grep -iE "session established|rtcm_xrce_bridge|ntrip" | tail -20
```

Expect `session established`, then `RTCM to PX4: N frames, ... chunks` from
`rtcm_xrce_bridge` every 10 s. A `Waiting for a PX4 GNSS fix` line means the
Here4 has no 3D fix yet; the caster sends nothing until it does.

PX4 subscribes to the corrections topic (firmware without the PR has no
subscriber; replace `05`):

```bash
cd ~/homebrew_docker
sudo docker compose run --rm --no-deps -e FASTRTPS_PROFILE_PATH=/etc/fastdds/super_client_config.xml homebrew_bringup ros2 topic info -v /homebrew05/fmu/in/rtcm_corrections
```

Expect `Publisher count: 1` (`rtcm_xrce_bridge`) and `Subscription count: 1`
(the agent, on PX4's behalf).

Then in QGroundControl → Analyze Tools → **MAVLink Console**:

```text
uxrce_dds_client status
listener rtcm_corrections
listener sensor_gnss
```

Expect the client connected on `/dev/ttyS1`, `rtcm_corrections` messages
arriving, and `fix_type: 6` (RTK fixed) in `sensor_gnss`. The QGroundControl
GPS indicator shows the same as RTK Fixed. Typical time from power-on to RTK
fixed is ~80–90 s with a clear sky.

### 4.4 Recorded bench run: corrections off, then on

This shows the fix depends on the XRCE corrections: blocking the Jetson's
caster connection must lose RTK fixed, and unblocking it must regain it. Only
the Jetson is blocked, so the tap on the lab PC keeps recording.

On the **Jetson**, with RTK fixed showing in QGroundControl:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh start --label bench
sudo bash rtk_test/record_run.sh corrections off
```

Wait until QGroundControl's GPS indicator leaves RTK Fixed (my guess, not
yet measured: within about a minute, the Here4's default correction timeout).
Then on the **Jetson**:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh corrections on
```

Wait until RTK Fixed returns (`criteria.json` allows 120 s), plus two
minutes of holding it. Then on the **Jetson**:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh stop
sudo bash rtk_test/record_run.sh status
```

`status` must say `Corrections: on`.

### 4.5 Analyze the bench run

First copy the run from the Jetson, while it is still powered. In a terminal
on the **lab PC** (replace `05`):

```bash
NUMBER=05
```

Then:

```bash
cd ~/homebrew_docker
rsync -a --exclude .current_run homebrew${NUMBER}_me:homebrew_docker/logs/rtk_runs/ logs/rtk_runs/
ls logs/rtk_runs/
```

The ULog is closed only at shutdown (`SDLOG_MODE` 2), and these logs are too
big to download over the telemetry radio in reasonable time, so they come off
the SD card. Unplug the battery, take out the FCU's SD card, and copy the
newest `.ulg` from its `log/<date>/` folder (named by UTC time; it covers the
bench run) into `~/rtk_sessions/<today>/` on the lab PC. Put the card back.

Copy that `.ulg` into the bench run's directory (the one ending
`_xrce_bench`), then analyze it. On the **lab PC** (replace the log file and
run name):

```bash
cd ~/homebrew_docker
cp ~/rtk_sessions/$(date +%F)/19_05_10.ulg logs/rtk_runs/20261102T190000Z_xrce_bench/
python3 rtk_test/analyze_run.py logs/rtk_runs/20261102T190000Z_xrce_bench --tap ~/rtk_sessions/$(date +%F)/tap
```

**Go for flight** only if `analysis/report.md` shows no FAIL, the receiver
reached RTK fixed after `corrections on`, and the hop 2 → 3 comparison (DDS to
inside PX4) found every chunk.

## 5. Flight run

Battery in, QGroundControl on the telemetry radio, wait for RTK Fixed in the
GPS indicator. On the **Jetson**:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh start --label flight1
```

Fly. To note an event (e.g. a fix drop you saw), on the **Jetson**:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh mark "fix dropped while hovering"
```

After landing and disarming, on the **Jetson**:

```bash
cd ~/homebrew_docker
sudo bash rtk_test/record_run.sh stop
```

Before powering off, in QGroundControl → Analyze Tools → MAVLink Console:

```text
perf
```

Copy its `uavcan: gnss: rtcm stream pub failed` line (chunks PX4 failed to
send toward the Here4 over CAN; it is not in the ULog) into
`~/rtk_sessions/<today>/perf.txt` on the lab PC.

Then as in 4.5: `rsync` the runs while the Jetson is powered, unplug the
battery, copy the flight's `.ulg` off the SD card into the `_xrce_flight1`
directory, and run
`analyze_run.py` on it with the same `--tap`. Stop `caster_tap.py` (Ctrl-C)
at the end of the session.

## 6. Results for the PX4 developers

Each run's `analysis/` holds `report.md` (paste into the PR, with the PNGs),
`report.html` and `summary.json`. Add the `perf` line from
`~/rtk_sessions/<date>/perf.txt`: the CAN send-failure count is only there.

To compare against MAVROS, record MAVROS runs on the same firmware at the same
place, interleaved with XRCE ones (MAVROS settings in main README 8.1, then
`record_run.sh start` picks the mode from `.env`), analyze them the same way,
then on the **lab PC**:

```bash
cd ~/homebrew_docker
python3 rtk_test/compare_runs.py logs/rtk_runs/* --out logs/rtk_runs/comparison
```
