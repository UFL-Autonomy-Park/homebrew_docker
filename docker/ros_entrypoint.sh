#!/usr/bin/env bash
set -e

readonly HOMEBREW_WS="${HOMEBREW_WS:-/root/homebrew_ws}"
readonly EXPERIMENT_WS="${EXPERIMENT_WS:-/root/experiment_ws}"
readonly USE_EXPERIMENT_WS="${USE_EXPERIMENT_WS:-false}"
# Nodes connect as plain discovery-server CLIENTs, which only receive discovery
# data for topics they use. Debugging tools (ros2 topic list, ...) need the
# whole graph: run them with FASTRTPS_PROFILE_PATH=/etc/fastdds/super_client_config.xml
readonly FASTRTPS_PROFILE_PATH="${FASTRTPS_PROFILE_PATH:-/etc/fastdds/client_config.xml}"

source_if_present() {
    local setup_file="$1"

    if [[ -r "${setup_file}" ]]; then
        # shellcheck disable=SC1090
        source "${setup_file}"
    fi
}

# Base workspaces
source_if_present "/opt/ros/${ROS_DISTRO}/setup.bash"
source_if_present "/opt/ros/${ROS_DISTRO}/install/setup.bash"
source_if_present "/root/ros2_ws/install/setup.bash"

# Persistent homebrew workspace
if [[ -r "${HOMEBREW_WS}/install/setup.bash" ]]; then
    source_if_present "${HOMEBREW_WS}/install/setup.bash"
else
    echo "Warning: ${HOMEBREW_WS} has not been built yet." >&2
    echo "Run the initial colcon build before starting homebrew_bringup." >&2
fi

# Optional experiment workspace
if [[ "${USE_EXPERIMENT_WS}" == "true" ]]; then
    if [[ -r "${EXPERIMENT_WS}/install/setup.bash" ]]; then
        source_if_present "${EXPERIMENT_WS}/install/setup.bash"
    else
        echo "Warning: ${EXPERIMENT_WS} has not been built yet." >&2
        echo "Run the initial colcon build before starting your experiment service." >&2
    fi
fi

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"

# Check if discovery server
unset ROS_DISCOVERY_SERVER
unset ROS_SUPER_CLIENT

if [[ "${USE_DISCOVERY_SERVER:-false}" == "true" ]]; then
    if [[ ! -r "${FASTRTPS_PROFILE_PATH}" ]]; then
        echo "Fast DDS profile not found: ${FASTRTPS_PROFILE_PATH}" >&2
        exit 1
    fi

    export FASTRTPS_DEFAULT_PROFILES_FILE="${FASTRTPS_PROFILE_PATH}"
else
    unset FASTRTPS_DEFAULT_PROFILES_FILE
fi

echo "ROS_DISTRO=${ROS_DISTRO}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
echo "ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY}"
echo "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION}"
echo "HOMEBREW_WS=${HOMEBREW_WS}"
echo "USE_EXPERIMENT_WS=${USE_EXPERIMENT_WS}"

if [[ "${USE_EXPERIMENT_WS}" == "true" ]]; then
    echo "EXPERIMENT_WS=${EXPERIMENT_WS}"
fi

if [[ -n "${FASTRTPS_DEFAULT_PROFILES_FILE:-}" ]]; then
    echo "Fast DDS Discovery Server profile enabled:"
    echo "  ${FASTRTPS_DEFAULT_PROFILES_FILE}"
else
    echo "Fast DDS Discovery Server profile disabled"
fi

readonly NETWORK_INTERFACE="${NETWORK_INTERFACE:-wlan0}"
readonly NETWORK_WAIT_TIMEOUT="${NETWORK_WAIT_TIMEOUT:-120}"
readonly TIME_SERVER="${TIME_SERVER:-192.168.1.201}"
readonly CLOCK_MAX_OFFSET_MS="${CLOCK_MAX_OFFSET_MS:-10}"
readonly CLOCK_WAIT_TIMEOUT="${CLOCK_WAIT_TIMEOUT:-60}"

# Seconds since boot. Elapsed times use this, not $SECONDS or date: those follow
# the wall clock, which jumps when the clock is set.
uptime_s() {
    local up
    read -r up _ < /proc/uptime
    echo "${up%.*}"
}

# Wait for the network before starting ROS.
#
# Fast DDS picks the network interfaces it advertises when a participant is
# created and never re-scans. Docker's restart policy starts this container as
# soon as dockerd is up (after an unclean power-off the container is still
# there and is restarted before homebrew-docker.service runs), which on the
# Jetsons is often before Wi-Fi connects; the nodes then advertise only
# unreachable addresses and are invisible to the rest of the network until
# restarted. Wait until NETWORK_INTERFACE has an IPv4 address and a default
# route exists; if not within NETWORK_WAIT_TIMEOUT seconds, exit non-zero so
# the restart policy retries.
network_start="$(uptime_s)"
until [[ -n "$(ip -4 -o addr show dev "${NETWORK_INTERFACE}" 2>/dev/null)" && -n "$(ip -4 route show default)" ]]; do
    if (( $(uptime_s) - network_start >= NETWORK_WAIT_TIMEOUT )); then
        echo "No IPv4 address on ${NETWORK_INTERFACE} or no default route after ${NETWORK_WAIT_TIMEOUT}s; exiting so Docker restarts the container." >&2
        exit 1
    fi
    sleep 2
done
echo "Network up: ${NETWORK_INTERFACE} has an IPv4 address and a default route (uptime $(uptime_s)s)."

# Wait until the clock agrees with the lab time server (docs/TIME_SYNC.md).
#
# Message ages are computed across machines, so every clock must agree with
# TIME_SERVER, and a clock change while MAVROS runs makes it reset its time
# sync. The clock counts as synced only when all of these hold on 3
# consecutive checks, 1 s apart:
#   - the kernel reports it synchronized (adjtimex state is not TIME_ERROR;
#     chrony maintains this),
#   - TIME_SERVER answers NTP with a valid reply, and
#   - this clock is within CLOCK_MAX_OFFSET_MS of it (offset + delay/2, the
#     worst case given the network delay).
# The lab network may be unreachable in the field, so after CLOCK_WAIT_TIMEOUT
# seconds warn and start anyway: the flight nodes check sync themselves before
# arming.
read -r -d '' CLOCK_CHECK_PY <<'EOF' || true
import ctypes, socket, struct, sys, time
server, max_ms = sys.argv[1], float(sys.argv[2])
# struct timex with modes = 0: read-only query. TIME_ERROR (5) means the kernel
# considers the clock unsynchronized (STA_UNSYNC set; man 2 adjtimex).
state = ctypes.CDLL(None, use_errno=True).adjtimex(ctypes.create_string_buffer(256))
if state < 0 or state == 5:
    print(f"kernel clock not synchronized (adjtimex state {state})")
    sys.exit(1)
NTP_EPOCH = 2208988800
def to_ntp(t):
    return struct.pack("!II", int(t) + NTP_EPOCH, int((t % 1) * 2**32))
def from_ntp(b):
    s, f = struct.unpack("!II", b)
    return s - NTP_EPOCH + f / 2**32
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(1.0)
req = bytearray(48)
req[0] = 0x23  # NTP v4, client mode
t0 = time.time()
tx = to_ntp(t0)
req[40:48] = tx
try:
    sock.sendto(req, (server, 123))
    d, _ = sock.recvfrom(512)
except OSError as e:
    print(f"no NTP reply from {server}: {e}")
    sys.exit(1)
t3 = time.time()
# Server mode, leap indicator not "alarm", stratum 1-15, and our request echoed
if len(d) < 48 or d[0] & 7 != 4 or d[0] >> 6 == 3 or not 1 <= d[1] <= 15 or d[24:32] != tx:
    print(f"unusable NTP reply from {server} (header 0x{d[0]:02x}, stratum {d[1]})")
    sys.exit(1)
t1, t2 = from_ntp(d[32:40]), from_ntp(d[40:48])
offset = ((t1 - t0) + (t2 - t3)) / 2 * 1e3
delay = ((t3 - t0) - (t2 - t1)) * 1e3
msg = f"offset to {server} {offset:+.2f} ms, delay {delay:.2f} ms, server stratum {d[1]}"
# The true offset lies within offset +/- delay/2
if abs(offset) + delay / 2 > max_ms:
    print(f"too far off: {msg}")
    sys.exit(1)
print(msg)
EOF

clock_start="$(uptime_s)"
clock_ok=0
while (( clock_ok < 3 )); do
    if clock_status="$(python3 -c "${CLOCK_CHECK_PY}" "${TIME_SERVER}" "${CLOCK_MAX_OFFSET_MS}" 2>&1)"; then
        clock_ok=$(( clock_ok + 1 ))
    else
        clock_ok=0
        echo "Clock not yet in sync (uptime $(uptime_s)s): ${clock_status}"
    fi
    if (( clock_ok < 3 )); then
        if (( $(uptime_s) - clock_start >= CLOCK_WAIT_TIMEOUT )); then
            echo "WARNING: clock not confirmed in sync with ${TIME_SERVER} after ${CLOCK_WAIT_TIMEOUT}s (last check: ${clock_status}). Starting anyway; ages computed against other machines' clocks may be wrong." >&2
            break
        fi
        sleep 1
    fi
done
if (( clock_ok >= 3 )); then
    echo "Clock in sync with ${TIME_SERVER} (uptime $(uptime_s)s, 3 consecutive checks; last: ${clock_status})."
fi

exec "$@"
