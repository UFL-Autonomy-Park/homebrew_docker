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
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ros-${ROS_DISTRO}-mavros \
    ros-${ROS_DISTRO}-mavros-msgs \
    ros-${ROS_DISTRO}-mavros-extras 

RUN source "/opt/ros/$ROS_DISTRO/setup.bash" && \
    source "/root/ros2_ws/install/setup.bash" && \
    ros2 run mavros install_geographiclib_datasets.sh

RUN mkdir -p ${HOMEBREW_WS}
RUN mkdir -p ${EXPERIMENT_WS}


RUN mkdir -p /tmp/homebrew_ws/src
COPY homebrew_ws/src/ tmp/homebrew_ws/src/

# rosdep
RUN apt-get update && \
    source /opt/ros/${ROS_DISTRO}/setup.bash && \
    rosdep install \
        --from-paths /tmp/homebrew_ws/src \
        --ignore-src \
        --rosdistro ${ROS_DISTRO} \
        -r -y && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/homebrew_ws

COPY docker/ros_entrypoint.sh \
    /sbin/homebrew_ros_entrypoint.sh

RUN chmod 755 /sbin/homebrew_ros_entrypoint.sh

WORKDIR ${HOMEBREW_WS}

ENTRYPOINT ["/sbin/homebrew_ros_entrypoint.sh"]