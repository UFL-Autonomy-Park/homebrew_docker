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

# MAVROS (with mavros_msgs, mavros_extras, libmavconn), built from source at a
# pinned tag and installed alongside the base image's ROS 2.
#
# Not from apt: the base image's ROS 2 is built from source
# (dustynv/ros:humble-ros-base, under /opt/ros/humble/install) and unknown
# to dpkg, so apt-installing ros-humble-mavros would also install a second
# ROS 2 (rclcpp, rmw, messages, ...) from apt into /opt/ros/humble, mixed
# with the first. (Until 2026-09-27 apt also lacked MAVROS altogether:
# https://github.com/mavlink/mavros/issues/2293.)
#
# Each RUN is a new shell: `source` and `cd` only last within one RUN, so the
# steps that need them stay joined with &&.
#
# MAVROS renames topics between versions (2.15 moved everything under
# <namespace>/mavros/). Before changing the tag: read MAVROS's CHANGELOG.rst
# and check the topic names the autonomy_park_sitl stack uses.
ARG MAVROS_VERSION=2.16.0
RUN apt-get update && \
    apt-get install -y --no-install-recommends git python3-colcon-common-extensions && \
    mkdir -p /tmp/mavros_src/src && \
    git clone --branch ${MAVROS_VERSION} --depth 1 https://github.com/mavlink/mavros.git /tmp/mavros_src/src/mavros && \
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

# px4_msgs, generated from the PX4 firmware under test: PX4 PR #29368 (RTK
# corrections over uXRCE-DDS on /fmu/in/rtcm_corrections), branch
# dakejahl/dds-rtcm-corrections, based on main past v1.18.0-rc1.
# uXRCE-DDS topics only deserialize if these definitions match the firmware's.
# px4_msgs is only synced from PX4's main and release branches, not from PR
# branches, so this does what PX4's sync_to_px4_msgs.yml workflow does: take the
# package from px4_msgs main and replace its .msg/.srv files with the
# firmware's. Pinned to commits; set PX4_FIRMWARE_REF to the commit flashed.
ARG PX4_MSGS_PACKAGE_REF=2e892300d8c20d3c29e198e324a10854b40ab8d3
ARG PX4_FIRMWARE_REPO=https://github.com/PX4/PX4-Autopilot.git
ARG PX4_FIRMWARE_REF=4dfcb7b4370ec7e03471038e821578d5794ec738
RUN git init -q /tmp/px4_msgs_src/src/px4_msgs && \
    cd /tmp/px4_msgs_src/src/px4_msgs && \
    git fetch -q --depth 1 https://github.com/PX4/px4_msgs.git ${PX4_MSGS_PACKAGE_REF} && \
    git checkout -q FETCH_HEAD && \
    git init -q /tmp/px4_fw && \
    cd /tmp/px4_fw && \
    git fetch -q --depth 1 --filter=blob:none ${PX4_FIRMWARE_REPO} ${PX4_FIRMWARE_REF} && \
    git sparse-checkout set msg srv && \
    git checkout -q FETCH_HEAD && \
    rm -f /tmp/px4_msgs_src/src/px4_msgs/msg/*.msg /tmp/px4_msgs_src/src/px4_msgs/srv/*.srv && \
    cp msg/*.msg /tmp/px4_msgs_src/src/px4_msgs/msg/ && \
    cp msg/versioned/*.msg /tmp/px4_msgs_src/src/px4_msgs/msg/ && \
    cp srv/*.srv /tmp/px4_msgs_src/src/px4_msgs/srv/ && \
    (cp srv/versioned/*.srv /tmp/px4_msgs_src/src/px4_msgs/srv/ 2>/dev/null || true) && \
    test -f /tmp/px4_msgs_src/src/px4_msgs/msg/RtcmData.msg && \
    rm -rf /tmp/px4_fw && \
    source /opt/ros/${ROS_DISTRO}/setup.bash && \
    cd /tmp/px4_msgs_src && \
    colcon build --merge-install --install-base /opt/ros/${ROS_DISTRO} && \
    rm -rf /tmp/px4_msgs_src

# Micro XRCE-DDS Agent (LAUNCH_XRCE_AGENT), the uXRCE-DDS alternative to
# MAVROS. Pinned to v2.4.3: do not change. Its superbuild bundles its own
# Fast DDS 2.14 / Fast CDR 2.2, which must not shadow ROS 2's copies, so it is
# installed under its own prefix, not added to the system library path, and
# the launch file runs it with LD_LIBRARY_PATH set to that prefix only. The
# ldd check fails the build if anything resolves outside it.
ARG XRCE_AGENT_VERSION=v2.4.3
RUN apt-get update && \
    apt-get install -y --no-install-recommends libasio-dev libtinyxml2-dev && \
    rm -rf /var/lib/apt/lists/* && \
    git clone --branch ${XRCE_AGENT_VERSION} --depth 1 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git /tmp/xrce_agent && \
    cmake -S /tmp/xrce_agent -B /tmp/xrce_agent/build \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/micro_xrce_dds_agent && \
    cmake --build /tmp/xrce_agent/build -j"$(nproc)" && \
    cmake --install /tmp/xrce_agent/build && \
    rm -rf /tmp/xrce_agent && \
    ! LD_LIBRARY_PATH=/opt/micro_xrce_dds_agent/lib ldd /opt/micro_xrce_dds_agent/bin/MicroXRCEAgent | grep -E "not found|/opt/ros"

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
