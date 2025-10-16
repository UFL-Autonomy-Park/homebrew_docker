FROM arm64v8/ros:humble-ros-base

#COPY super_client_config.xml ./

RUN apt update && apt upgrade -y

#Install fastrtps for discovery server
#RUN apt install -y ros-humble-rmw-fastrtps-cpp ros-humble-rmw-fastrtps-shared-cpp

#Install mavros and required geographic datasets
RUN apt install -y ros-humble-mavros ros-humble-mavros-msgs ros-humble-mavros-extras
RUN . /opt/ros/humble/setup.sh && ros2 run mavros install_geographiclib_datasets.sh

ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

#Configure ROS2 environment variables & discovery server
ENV ROS_LOCALHOST_ONLY=0
ENV ROS_DOMAIN_ID=0
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
#ENV ROS_DISCOVERY_SERVER=192.168.1.201:11811
#ENV FASTRTPS_DEFAULT_PROFILES_FILE=/super_client_config.xml

RUN . /opt/ros/humble/setup.sh && fastdds shm clean && ros2 daemon stop && ros2 daemon start
CMD . /opt/ros/humble/setup.sh && ros2 launch mavros px4.launch fcu_url:=/dev/ttyUSB0:921600 respawn_mavros:=true namespace:=homebrew
