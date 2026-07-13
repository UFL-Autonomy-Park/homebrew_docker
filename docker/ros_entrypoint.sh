#!/usr/bin/env bash
set -e

source "/opt/ros/${ROS_DISTRO}/install/setup.bash"
source "/root/ros2_ws/install/setup.bash"
source "/root/autonomy_ws/install/setup.bash"

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

if [[ -n "${FASTRTPS_DEFAULT_PROFILES_FILE:-}" ]]; then
    echo "Fast DDS Discovery Server profile enabled:"
    echo "  ${FASTRTPS_DEFAULT_PROFILES_FILE}"
else
    echo "Fast DDS Discovery Server profile disabled"
fi

exec "$@"