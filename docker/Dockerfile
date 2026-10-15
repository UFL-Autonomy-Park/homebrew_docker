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

# Build tools for the packages built from source below (ntrip_client, px4_msgs)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git python3-colcon-common-extensions

# MAVROS 2.16.0 from the ROS apt repo, pinned to exact builds for arm64 (the
# Jetsons; the build stamps differ per architecture, so any other architecture
# fails here on purpose).
#
# The ROS apt repo keeps only the newest build of each package. When a newer
# one is published, these versions disappear and this step FAILS instead of
# silently installing a different MAVROS. MAVROS renames topics between
# versions (2.15 moved everything under <namespace>/mavros/), so before
# changing the versions: read MAVROS's CHANGELOG.rst, check the topic names
# the autonomy_park_sitl stack uses, then update all four together. Current
# versions: `apt-cache policy ros-humble-mavros` in a container from this base
# image. apt-mark hold keeps later layers from changing them.
#
# Until 2026-10-15 MAVROS 2.14.0 was built from source here, because of
# https://github.com/mavlink/mavros/issues/2293 (binaries missing from apt;
# closed 2026-09-27).
ARG LIBMAVCONN_VERSION=2.16.0-1jammy.20260927.155237
ARG MAVROS_VERSION=2.16.0-1jammy.20260927.163040
ARG MAVROS_EXTRAS_VERSION=2.16.0-1jammy.20260927.170609
ARG MAVROS_MSGS_VERSION=2.16.0-1jammy.20260927.155213
RUN if [[ "$(dpkg --print-architecture)" != "arm64" ]]; then \
        echo "ERROR: the MAVROS versions pinned here are arm64 builds; this is $(dpkg --print-architecture)." >&2; \
        exit 1; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-libmavconn=${LIBMAVCONN_VERSION} \
        ros-${ROS_DISTRO}-mavros=${MAVROS_VERSION} \
        ros-${ROS_DISTRO}-mavros-extras=${MAVROS_EXTRAS_VERSION} \
        ros-${ROS_DISTRO}-mavros-msgs=${MAVROS_MSGS_VERSION} && \
    apt-mark hold \
        ros-${ROS_DISTRO}-libmavconn ros-${ROS_DISTRO}-mavros \
        ros-${ROS_DISTRO}-mavros-extras ros-${ROS_DISTRO}-mavros-msgs

RUN source "/opt/ros/$ROS_DISTRO/setup.bash" && \
    source "/root/ros2_ws/install/setup.bash" && \
    ros2 run mavros install_geographiclib_datasets.sh && \
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
