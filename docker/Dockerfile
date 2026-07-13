# The base image is built by:
# UFL-Autonomy-Park/zed_ros2_wrapper_jetson

# Default image value matches that of the `jetson_build_dockerfile_from_sdk_and_l4T_version.sh` script

ARG BASE_IMAGE=zed_ros2_l4t_36.3.0_sdk_5.0.0
FROM ${BASE_IMAGE}

ARG ROS_DISTRO=humble
ENV ROS_DISTRO=${ROS_DISTRO}

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

WORKDIR /root/homebrew_ws

COPY homebrew_ws/src/ src/

RUN source "/opt/ros/$ROS_DISTRO/install/setup.bash" && \
    source "/root/ros2_ws/install/setup.bash" && \
    colcon build \
        --merge-install \ 
        --packages-select homebrew_bringup \
        --event-handlers console_direct+

# Fast DDS Discovery Server super-client profile.
COPY config/fastdds/super_client_config.xml \
    /etc/fastdds/super_client_config.xml

COPY docker/ros_entrypoint.sh \
    /sbin/homebrew_ros_entrypoint.sh

RUN chmod 755 /sbin/homebrew_ros_entrypoint.sh

ENTRYPOINT ["/sbin/homebrew_ros_entrypoint.sh"]

CMD ["ros2", "launch", "homebrew_bringup", "homebrew.launch.py"]