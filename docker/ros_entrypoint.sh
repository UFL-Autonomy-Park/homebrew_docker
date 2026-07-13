#!/usr/bin/env bash
set -e

source "/opt/ros/${ROS_DISTRO}/install/setup.bash"
source "/root/ros2_ws/install/setup.bash"
source "/root/autonomy_ws/install/setup.bash"

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"

# Check if discovery server
if [[ "${USE_DISCOVERY_SERVER:-false}" == "true" ]]; then
    if [[ -z "${DISCOVERY_SERVER_ADDRESS:-}" ]]; then
        echo "USE_DISCOVERY_SERVER=true, but DISCOVERY_SERVER_ADDRESS is empty." >&2
        exit 1
    fi

    export ROS_DISCOVERY_SERVER="${DISCOVERY_SERVER_ADDRESS}"
else
    unset ROS_DISCOVERY_SERVER
fi

exec "$@"