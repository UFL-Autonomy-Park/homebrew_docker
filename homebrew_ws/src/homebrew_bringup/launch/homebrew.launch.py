from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import (
    AnyLaunchDescriptionSource,
    PythonLaunchDescriptionSource,
)
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from typing import Tuple
from launch_ros.actions import Node

# Sensor mounting transforms for homebrew
#
# xyz:
#   Position of sensor frame origin expressed in base_link; in meters
#
# rpy:
#   Orientation of sensor frame relative to base_link; in radians
MODEL_TRANSFORMS = {
    "zed": {
        "xyz": (
            0.0,
            0.0,
            0.0,
        ),
        "rpy": (
            0.0,
            0.0,
            0.0,
        ),
    },
}

def make_static_transform_node(
    *,
    node_name: str,
    namespace,
    parent_frame: str,
    child_frame: str,
    xyz: Tuple[float, float, float],
    rpy: Tuple[float, float, float],
    condition=None,
) -> Node:

    x, y, z = xyz
    roll, pitch, yaw = rpy

    return Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        namespace=namespace,
        name=node_name,
        arguments=[
            "--x",
            str(x),
            "--y",
            str(y),
            "--z",
            str(z),
            "--roll",
            str(roll),
            "--pitch",
            str(pitch),
            "--yaw",
            str(yaw),
            "--frame-id",
            parent_frame,
            "--child-frame-id",
            child_frame,
        ],
        condition=IfCondition(condition)
    )


def generate_launch_description() -> LaunchDescription:
    launch_zed = LaunchConfiguration("launch_zed")
    launch_mavros = LaunchConfiguration("launch_mavros")
    launch_ntrip = LaunchConfiguration("launch_ntrip")

    zed_model = LaunchConfiguration("zed_model")
    zed_camera_name = LaunchConfiguration("zed_camera_name")
    zed_namespace = LaunchConfiguration("zed_namespace")

    fcu_url = LaunchConfiguration("fcu_url")
    mavros_namespace = LaunchConfiguration("mavros_namespace")
    mavros_tgt_system = LaunchConfiguration("mavros_tgt_system")
    mavros_respawn = LaunchConfiguration("mavros_respawn")

    ntrip_host = LaunchConfiguration("ntrip_host")
    ntrip_port = LaunchConfiguration("ntrip_port")
    ntrip_mountpoint = LaunchConfiguration("ntrip_mountpoint")
    ntrip_username = LaunchConfiguration("ntrip_username")
    ntrip_password = LaunchConfiguration("ntrip_password")

    zed_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [
                    FindPackageShare("zed_wrapper"),
                    "launch",
                    "zed_camera.launch.py",
                ]
            )
        ),
        condition=IfCondition(launch_zed),
        launch_arguments={
            "camera_model": zed_model,
            "camera_name": zed_camera_name,
            "namespace": zed_namespace,
        }.items(),
    )

    mavros_launch = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(
            PathJoinSubstitution(
                [
                    FindPackageShare("mavros"),
                    "launch",
                    "px4.launch",
                ]
            )
        ),
        condition=IfCondition(launch_mavros),
        launch_arguments={
            "fcu_url": fcu_url,
            "namespace": mavros_namespace,
            "tgt_system": mavros_tgt_system,
            "respawn_mavros": mavros_respawn,
        }.items(),
    )

    # RTCM corrections from the NTRIP caster, forwarded to the FCU by MAVROS's
    # gps_rtk plugin. Runs as its own process: if the caster is unreachable it
    # exits and respawns without affecting MAVROS.
    ntrip_client_node = Node(
        package="ntrip_client",
        executable="ntrip_ros.py",
        name="ntrip_client",
        namespace=mavros_namespace,
        parameters=[
            {
                "host": ntrip_host,
                "port": ntrip_port,
                "mountpoint": ntrip_mountpoint,
                "authenticate": True,
                "username": ntrip_username,
                "password": ntrip_password,
                "rtcm_message_package": "mavros_msgs",
            }
        ],
        remappings=[("rtcm", "gps_rtk/send_rtcm")],
        respawn=True,
        respawn_delay=10.0,
        condition=IfCondition(launch_ntrip),
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "launch_zed",
                default_value="false",
                description="Launch the ZED camera wrapper",
            ),
            DeclareLaunchArgument(
                "launch_mavros",
                default_value="false",
                description="Launch MAVROS",
            ),
            DeclareLaunchArgument(
                "launch_ntrip",
                default_value="false",
                description="Launch the NTRIP client for RTK corrections",
            ),
            DeclareLaunchArgument(
                "zed_model",
                default_value="zed2i",
                description="ZED camera model",
            ),
            DeclareLaunchArgument(
                "zed_camera_name",
                default_value="zed",
                description="ZED camera name",
            ),
            DeclareLaunchArgument(
                "zed_namespace",
                default_value="homebrew",
                description="Optional ZED namespace",
            ),
            DeclareLaunchArgument(
                "fcu_url",
                description="MAVROS flight-controller URL",
            ),
            DeclareLaunchArgument(
                "mavros_namespace",
                description="MAVROS namespace",
            ),
            # mavros_node hardcodes its router<->plugin link topics to the
            # absolute name /uas<tgt_system>, ignoring the namespace. Every
            # vehicle on the network must use a unique tgt_system (matching
            # the FCU's MAV_SYS_ID), or their MAVLink streams will mix.
            DeclareLaunchArgument(
                "mavros_tgt_system",
                description="MAVLink system ID of the FCU; must match MAV_SYS_ID",
            ),
            DeclareLaunchArgument(
                "mavros_respawn",
                default_value="true",
                description="Respawn MAVROS if it exits",
            ),
            DeclareLaunchArgument("ntrip_host", description="NTRIP caster address"),
            DeclareLaunchArgument(
                "ntrip_port", default_value="2101", description="NTRIP caster port"
            ),
            DeclareLaunchArgument("ntrip_mountpoint", description="NTRIP mountpoint"),
            DeclareLaunchArgument("ntrip_username", description="NTRIP username"),
            DeclareLaunchArgument("ntrip_password", description="NTRIP password"),
            zed_launch,
            mavros_launch,
            ntrip_client_node,
            make_static_transform_node(
                node_name="static_zed_tf_publisher",
                namespace=mavros_namespace,
                parent_frame="base_link",
                child_frame="zed_camera_link",
                xyz=MODEL_TRANSFORMS["zed"]["xyz"],
                rpy=MODEL_TRANSFORMS["zed"]["rpy"],
                condition=launch_zed
            ),
        ]
    )
