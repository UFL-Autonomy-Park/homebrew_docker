#!/usr/bin/env bash
set -e

readonly HOMEBREW_WS="${HOMEBREW_WS:-/root/homebrew_ws}"
readonly EXPERIMENT_WS="${EXPERIMENT_WS:-/root/experiment_ws}"
readonly USE_EXPERIMENT_WS="${USE_EXPERIMENT_WS:-false}"
readonly FASTRTPS_PROFILE_PATH="/etc/fastdds/super_client_config.xml"

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

# Wait for the network before starting ROS.
#
# Fast DDS picks the network interfaces it advertises when a participant is
# created and never re-scans. Docker's restart policy starts this container as
# soon as dockerd is up, which on the Jetsons is often before Wi-Fi connects;
# the nodes then advertise only unreachable addresses and are invisible to the
# rest of the network until restarted. If no default route appears within
# NETWORK_WAIT_TIMEOUT seconds, exit non-zero so the restart policy retries.
readonly NETWORK_WAIT_TIMEOUT="${NETWORK_WAIT_TIMEOUT:-120}"
readonly CLOCK_WAIT_TIMEOUT="${CLOCK_WAIT_TIMEOUT:-60}"

# Count elapsed time ourselves: $SECONDS follows the wall clock, which jumps
# decades forward when NTP syncs
network_waited=0
until awk '$2 == "00000000" {found=1} END {exit !found}' /proc/net/route; do
    if (( network_waited >= NETWORK_WAIT_TIMEOUT )); then
        echo "No default route after ${NETWORK_WAIT_TIMEOUT}s; exiting so Docker restarts the container." >&2
        exit 1
    fi
    sleep 2
    (( network_waited += 2 ))
done

# The Jetsons have no RTC and boot at 1970 until NTP syncs. Starting before
# then makes MAVROS reset its time sync and stamp early messages with 1970.
# NTP may be unreachable in the field, so warn and continue instead of failing.
readonly MIN_VALID_EPOCH=1735689600  # 2025-01-01T00:00:00Z
clock_waited=0
while (( $(date +%s) < MIN_VALID_EPOCH )); do
    if (( clock_waited >= CLOCK_WAIT_TIMEOUT )); then
        echo "Warning: system clock still unset ($(date -u)) after ${CLOCK_WAIT_TIMEOUT}s; starting anyway." >&2
        break
    fi
    sleep 2
    (( clock_waited += 2 ))
done

exec "$@"
