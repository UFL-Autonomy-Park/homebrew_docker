# The base image is built by:
# UFL-Autonomy-Park/zed_ros2_wrapper_jetson

# Default image value matches that of the `jetson_build_dockerfile_from_sdk_and_l4T_version.sh` script

ARG BASE_IMAGE=zed_ros2_l4t_36.3.0_sdk_5.0.0
FROM ${BASE_IMAGE}

ARG ROS_DISTRO=humble
ENV ROS_DISTRO=${ROS_DISTRO}
ENV HOMEBREW_WS=/root/homebrew_ws
ENV EXPERIMENT_WS=/root/experiment_ws

SHELL ["/bin/bash", "-c"]

# Install platform-specific ROS packages
# In this case, MAVROS and geographiclib datasets
#
# TEMPORARY: ros-humble-mavros / mavros-extras / libmavconn are absent from
# the ROS 2 apt repo for jammy (both amd64 and arm64) as of 2026-09-16 -
# https://github.com/mavlink/mavros/issues/2293 - only mavros_msgs still
# publishes. Building all four from source at the last known-good tag
# (2.14.0) instead of apt-installing them. Once mavros/mavros-extras
# reappear in the apt index at a healthy version, revert this block back to
# the simple apt-get install ros-${ROS_DISTRO}-mavros ros-${ROS_DISTRO}-mavros-msgs
# ros-${ROS_DISTRO}-mavros-extras that used to live here.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git python3-colcon-common-extensions && \
    mkdir -p /tmp/mavros_src/src && \
    git clone --branch 2.14.0 --depth 1 https://github.com/mavlink/mavros.git /tmp/mavros_src/src/mavros && \
    source /opt/ros/${ROS_DISTRO}/setup.bash && \
    rosdep install --from-paths /tmp/mavros_src/src --ignore-src -r -y

RUN source /opt/ros/${ROS_DISTRO}/setup.bash && \
    cd /tmp/mavros_src && \
    colcon build --merge-install --install-base /opt/ros/${ROS_DISTRO}

RUN source "/opt/ros/$ROS_DISTRO/setup.bash" && \
    source "/root/ros2_ws/install/setup.bash" && \
    ros2 run mavros install_geographiclib_datasets.sh && \
    rm -rf /tmp/mavros_src && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ntrip_client (RTK corrections -> MAVROS gps_rtk), built from source at a
# pinned tag, installed alongside MAVROS.
# Patch: its 'fix' subscription is RELIABLE, which can't receive MAVROS's
# BEST_EFFORT global_position/raw/fix, so it never sends the caster a GGA.
# Subscribe with sensor-data QoS instead; the grep fails the build if a
# version bump makes the sed a no-op.
ARG NTRIP_CLIENT_VERSION=ros2-1.4.1
RUN apt-get update && \
    git clone --branch ${NTRIP_CLIENT_VERSION} --depth 1 https://github.com/LORD-MicroStrain/ntrip_client.git /tmp/ntrip_src/src/ntrip_client && \
    sed -i "s/'fix', self.subscribe_fix, 10)/'fix', self.subscribe_fix, rclpy.qos.qos_profile_sensor_data)/" \
        /tmp/ntrip_src/src/ntrip_client/scripts/ntrip_ros_base.py && \
    grep -q "subscribe_fix, rclpy.qos.qos_profile_sensor_data)" /tmp/ntrip_src/src/ntrip_client/scripts/ntrip_ros_base.py && \
    source /opt/ros/${ROS_DISTRO}/setup.bash && \
    rosdep install --from-paths /tmp/ntrip_src/src --ignore-src -y && \
    cd /tmp/ntrip_src && \
    colcon build --merge-install --install-base /opt/ros/${ROS_DISTRO} && \
    rm -rf /tmp/ntrip_src && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p ${HOMEBREW_WS}
RUN mkdir -p ${EXPERIMENT_WS}


# No rosdep for homebrew_ws: its dependencies are all installed explicitly
# above or by the base image, which builds ROS itself into
# /opt/ros/humble/install. rosdep would apt-install duplicate copies of those.

# iproute2 for the entrypoint's network wait
RUN apt-get update && \
    apt-get install -y --no-install-recommends iproute2 && \
    rm -rf /var/lib/apt/lists/*

COPY docker/ros_entrypoint.sh \
    /sbin/homebrew_ros_entrypoint.sh

RUN chmod 755 /sbin/homebrew_ros_entrypoint.sh

WORKDIR ${HOMEBREW_WS}

ENTRYPOINT ["/sbin/homebrew_ros_entrypoint.sh"]
