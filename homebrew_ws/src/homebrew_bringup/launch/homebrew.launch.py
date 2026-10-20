import os

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import (
    AnyLaunchDescriptionSource,
    PythonLaunchDescriptionSource,
)
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.substitutions import FindPackageShare
from typing import Tuple
from launch_ros.actions import Node, SetRemap

# config/mavros/ in the repo, bind-mounted read-only by docker-compose.yml
MAVROS_CONFIG_DIR = "/etc/homebrew/mavros"

# Micro XRCE-DDS Agent install prefix (docker/Dockerfile). Its bundled Fast DDS
# lives here, deliberately off the system library path.
XRCE_AGENT_PREFIX = "/opt/micro_xrce_dds_agent"

# config/fastdds/ in the repo, bind-mounted read-only by docker-compose.yml.
# The agent's participant ("px4_participant", requested by PX4 when
# UXRCE_DDS_PTCFG=2) follows USE_DISCOVERY_SERVER like every other node.
FASTDDS_CONFIG_DIR = "/etc/fastdds"

# Topics that nodes publish or subscribe under absolute names, which the
# namespace does not apply to. Every vehicle would share them across the
# network (e.g. each MAVROS would hear the other vehicles' transforms), so
# they are remapped under the vehicle's namespace for every node launched here.
# Check for new ones with: ros2 topic list | grep -v '^/<namespace>/'
ABSOLUTE_TOPICS = (
    "/tf",
    "/tf_static",
    "/diagnostics",
    "/move_base_simple/goal",
)

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

def _is_true(context, name: str) -> bool:
    return LaunchConfiguration(name).perform(context).strip().lower() == "true"


def check_one_bridge(context):
    """MAVROS and the agent both need the FCU's serial port: allow only one."""
    if _is_true(context, "launch_mavros") and _is_true(context, "launch_xrce_agent"):
        raise RuntimeError(
            "LAUNCH_MAVROS and LAUNCH_XRCE_AGENT are both true. Both use the "
            "flight controller's serial port; enable exactly one "
            "(README section 8)."
        )
    return []


def make_xrce_agent(context):
    """Micro XRCE-DDS Agent on the same serial port and baud as FCU_URL."""
    if not _is_true(context, "launch_xrce_agent"):
        return []
    fcu_url = LaunchConfiguration("fcu_url").perform(context)
    device, _, baud = fcu_url.partition(":")
    if not device.startswith("/dev/") or not baud.isdigit():
        raise RuntimeError(
            "LAUNCH_XRCE_AGENT needs a serial FCU_URL like /dev/ttyFC:921600, "
            f"got {fcu_url!r}"
        )
    use_server = os.environ.get("USE_DISCOVERY_SERVER", "false").lower() == "true"
    refs = os.path.join(
        FASTDDS_CONFIG_DIR,
        "xrce_agent_client.xml" if use_server else "xrce_agent_simple.xml",
    )
    return [
        ExecuteProcess(
            cmd=[
                os.path.join(XRCE_AGENT_PREFIX, "bin", "MicroXRCEAgent"),
                "serial",
                "--dev",
                device,
                "-b",
                baud,
                "-r",
                refs,
            ],
            name="micro_xrce_dds_agent",
            # Only the agent's own libraries: never ROS's Fast DDS.
            additional_env={
                "LD_LIBRARY_PATH": os.path.join(XRCE_AGENT_PREFIX, "lib")
            },
            output="screen",
        )
    ]


def make_mavlink_rates_node(context):
    """mavlink_rates node, only with MAVROS and a non-empty MAVLINK_RATES.

    mavlink_rates is "<message id>:<Hz>,..." e.g. "141:100,33:100,32:100".
    """
    spec = LaunchConfiguration("mavlink_rates").perform(context).strip()
    if not spec or not _is_true(context, "launch_mavros"):
        return []
    ids, rates = [], []
    for item in spec.split(","):
        msg_id, sep, rate = item.strip().partition(":")
        try:
            if not sep:
                raise ValueError
            ids.append(int(msg_id))
            rates.append(float(rate))
        except ValueError:
            raise RuntimeError(
                "MAVLINK_RATES must look like 141:100,33:100 "
                f"(<message id>:<Hz>), got {spec!r}"
            )
    return [
        Node(
            package="homebrew_bringup",
            executable="mavlink_rates",
            name="mavlink_rates",
            namespace=LaunchConfiguration("mavros_namespace"),
            parameters=[{"message_ids": ids, "rates_hz": rates}],
            output="screen",
            respawn=True,
            respawn_delay=5.0,
        )
    ]


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
    launch_xrce_agent = LaunchConfiguration("launch_xrce_agent")

    zed_model = LaunchConfiguration("zed_model")
    zed_camera_name = LaunchConfiguration("zed_camera_name")

    fcu_url = LaunchConfiguration("fcu_url")
    mavros_namespace = LaunchConfiguration("mavros_namespace")
    mavros_tgt_system = LaunchConfiguration("mavros_tgt_system")
    mavros_plugins = LaunchConfiguration("mavros_plugins")

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
            "namespace": mavros_namespace,
        }.items(),
    )

    # MAVROS does not respawn if it dies. Upstream node.launch (mavros 2.x)
    # declares a respawn_mavros argument but never applies it to the node, so
    # passing it did nothing; it was removed rather than left misleading.
    # A dead MAVROS stays dead until the container or Jetson restarts.
    #
    # node.launch is included directly rather than px4.launch, because
    # px4.launch hardcodes its plugin list. The other arguments match
    # px4.launch's defaults; the plugin list comes from config/mavros/.
    mavros_launch = IncludeLaunchDescription(
        AnyLaunchDescriptionSource(
            PathJoinSubstitution(
                [
                    FindPackageShare("mavros"),
                    "launch",
                    "node.launch",
                ]
            )
        ),
        condition=IfCondition(launch_mavros),
        launch_arguments={
            "fcu_url": fcu_url,
            "gcs_url": "",
            "tgt_system": mavros_tgt_system,
            "tgt_component": "1",
            "fcu_protocol": "v2.0",
            "namespace": mavros_namespace,
            "pluginlists_yaml": [
                MAVROS_CONFIG_DIR,
                "/",
                mavros_plugins,
                "_pluginlists.yaml",
            ],
            "config_yaml": PathJoinSubstitution(
                [FindPackageShare("mavros"), "launch", "px4_config.yaml"]
            ),
        }.items(),
    )

    # RTCM corrections from the NTRIP caster, forwarded to the FCU by MAVROS's
    # gps_rtk plugin. Runs as its own process: if the caster is unreachable it
    # exits and respawns without affecting MAVROS. The client sends the
    # vehicle's GPS fix back as GGA; the Emlid caster streams nothing without it.
    # Only runs with MAVROS: nothing else forwards its corrections to the FCU.
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
        # MAVROS 2.16 puts its topics under <namespace>/mavros/
        remappings=[
            ("rtcm", "mavros/gps_rtk/send_rtcm"),
            ("fix", "mavros/global_position/raw/fix"),
        ],
        respawn=True,
        respawn_delay=10.0,
        condition=IfCondition(
            PythonExpression(
                [
                    "'", launch_ntrip, "'.lower() == 'true' and '",
                    launch_mavros, "'.lower() == 'true'",
                ]
            )
        ),
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
                "launch_xrce_agent",
                default_value="false",
                description="Launch the Micro XRCE-DDS Agent instead of MAVROS "
                "(never both)",
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
                "mavros_plugins",
                default_value="full",
                choices=["full", "minimal"],
                description="MAVROS plugin list in config/mavros/: "
                "full (upstream PX4 default) or minimal",
            ),
            DeclareLaunchArgument(
                "mavlink_rates",
                default_value="",
                description="MAVLink stream rates to request from the FCU, "
                "'<message id>:<Hz>,...'; empty leaves PX4's defaults",
            ),
            DeclareLaunchArgument("ntrip_host", description="NTRIP caster address"),
            DeclareLaunchArgument(
                "ntrip_port", default_value="2101", description="NTRIP caster port"
            ),
            DeclareLaunchArgument("ntrip_mountpoint", description="NTRIP mountpoint"),
            DeclareLaunchArgument("ntrip_username", description="NTRIP username"),
            DeclareLaunchArgument("ntrip_password", description="NTRIP password"),
            GroupAction(
                [
                    *(
                        SetRemap(src=topic, dst=["/", mavros_namespace, topic])
                        for topic in ABSOLUTE_TOPICS
                    ),
                    OpaqueFunction(function=check_one_bridge),
                    zed_launch,
                    mavros_launch,
                    OpaqueFunction(function=make_mavlink_rates_node),
                    OpaqueFunction(function=make_xrce_agent),
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
            ),
        ]
    )
