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

# px4_msgs, pinned to the PX4 release the vehicles' flight controllers run.
# uXRCE-DDS topics only deserialize if these definitions match the firmware's.
ARG PX4_MSGS_VERSION=v1.16.2
RUN git clone --branch ${PX4_MSGS_VERSION} --depth 1 https://github.com/PX4/px4_msgs.git /tmp/px4_msgs_src/src/px4_msgs && \
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
