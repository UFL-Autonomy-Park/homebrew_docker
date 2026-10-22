"""Connect ntrip_client to PX4 over uXRCE-DDS, in place of MAVROS.

Two jobs, both done by MAVROS when it is the bridge:
- RTCM: ntrip_client publishes one RTCM frame per mavros_msgs/RTCM message, of
  any length (RTCM3 frames reach 1029 bytes). PX4 (PR #29368, firmware past
  v1.18.0-rc1) takes px4_msgs/RtcmData on fmu/in/rtcm_corrections, at most 300
  bytes each. Frames are split into chunks of up to chunk_size bytes; PX4's
  injectors rebuild the byte stream, so any split point works.
- GGA: ntrip_client reports the vehicle's position to the caster from a
  NavSatFix, which the caster needs before it streams anything. That fix comes
  from PX4's fmu/out/vehicle_gnss here.

PX4's agent-side reader keeps 64 chunks, and PX4 queues 16 per source before
its GNSS driver drains them. If chunks are dropped, PX4's console prints
"rtcm_corrections lost, generation ...". The periodic log below gives the
ROS-side counts to compare against.
Started by homebrew.launch.py when LAUNCH_NTRIP and LAUNCH_XRCE_AGENT are true.
"""
from collections import deque

import numpy as np
import rclpy
from mavros_msgs.msg import RTCM
from px4_msgs.msg import RtcmData, SensorGnss, VehicleGnss
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
                       qos_profile_sensor_data)
from sensor_msgs.msg import NavSatFix, NavSatStatus

RTCM_DATA_SIZE = len(RtcmData().data)

# Matches PX4's agent-side reader for fmu/in topics: best effort, volatile, and
# a queue of ORB_QUEUE_LENGTH * MAX_INSTANCES (64)
RTCM_OUT_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=RtcmData.ORB_QUEUE_LENGTH * RtcmData.MAX_INSTANCES,
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.VOLATILE,
)
# ntrip_client publishes a whole epoch's frames back to back
RTCM_IN_QOS = QoSProfile(depth=100)

# Seconds without RTCM from ntrip_client, after some arrived, before warning
RTCM_SILENCE_WARN_S = 5.0


class RtcmXrceBridge(Node):
    """Forward ntrip_client's RTCM to PX4 in chunks, and PX4's fix to ntrip_client."""

    def __init__(self):
        super().__init__('rtcm_xrce_bridge')
        # PX4 gives each device_id its own rtcm_corrections instance
        self.device_id = self.declare_parameter('device_id', 1).value
        self.chunk_size = self.declare_parameter('chunk_size', RTCM_DATA_SIZE).value
        self.fix_period_s = self.declare_parameter('fix_period_s', 1.0).value
        stats_period_s = self.declare_parameter('stats_period_s', 10.0).value
        if not 1 <= self.chunk_size <= RTCM_DATA_SIZE:
            raise ValueError(f'chunk_size must be 1 to {RTCM_DATA_SIZE}, got {self.chunk_size}')

        self.frames = self.bytes = self.chunks = self.split_frames = 0
        self.largest_frame = 0
        # Send times of the chunks in the last second, for the peak burst size
        self.recent_chunks = deque()
        self.peak_chunks_per_s = 0
        self.last_rtcm_s = None
        self.silence_warned = False
        self.last_fix_s = None
        self.waiting_for_fix_logged = False

        self.rtcm_pub = self.create_publisher(RtcmData, 'fmu/in/rtcm_corrections', RTCM_OUT_QOS)
        self.fix_pub = self.create_publisher(NavSatFix, 'rtcm_bridge/fix', 10)
        self.create_subscription(RTCM, 'rtcm_bridge/rtcm', self.rtcm_cb, RTCM_IN_QOS)
        self.create_subscription(VehicleGnss, 'fmu/out/vehicle_gnss', self.gnss_cb,
                                 qos_profile_sensor_data)
        self.create_timer(stats_period_s, self.log_stats)

    def now_s(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def rtcm_cb(self, m):
        frame = bytes(m.data)
        now = self.now_s()
        self.last_rtcm_s = now
        if self.silence_warned:
            self.get_logger().info('RTCM from ntrip_client resumed')
            self.silence_warned = False
        self.frames += 1
        self.bytes += len(frame)
        self.largest_frame = max(self.largest_frame, len(frame))
        if len(frame) > self.chunk_size:
            self.split_frames += 1

        for start in range(0, len(frame), self.chunk_size):
            chunk = frame[start:start + self.chunk_size]
            out = RtcmData()
            out.timestamp = 0  # PX4 stamps it on arrival
            out.device_id = self.device_id
            out.len = len(chunk)
            out.flags = 0  # unused over uXRCE-DDS
            out.data = np.frombuffer(chunk.ljust(RTCM_DATA_SIZE, b'\0'), dtype=np.uint8)
            self.rtcm_pub.publish(out)
            self.chunks += 1
            self.recent_chunks.append(now)

        while self.recent_chunks and self.recent_chunks[0] < now - 1.0:
            self.recent_chunks.popleft()
        self.peak_chunks_per_s = max(self.peak_chunks_per_s, len(self.recent_chunks))

    def gnss_cb(self, m):
        r = m.receiver
        if r.fix_type < SensorGnss.FIX_TYPE_2D:
            if not self.waiting_for_fix_logged:
                self.get_logger().info(
                    f'Waiting for a PX4 GNSS fix (fix_type {r.fix_type}) before sending GGA')
                self.waiting_for_fix_logged = True
            return
        self.waiting_for_fix_logged = False
        now = self.now_s()
        if self.last_fix_s is not None and now - self.last_fix_s < self.fix_period_s:
            return
        self.last_fix_s = now

        fix = NavSatFix()
        fix.header.stamp = self.get_clock().now().to_msg()
        fix.header.frame_id = 'gps'
        # Same mapping as MAVROS's global_position/raw/fix
        if r.fix_type >= SensorGnss.FIX_TYPE_RTK_FLOAT:
            fix.status.status = NavSatStatus.STATUS_GBAS_FIX
        elif r.fix_type == SensorGnss.FIX_TYPE_RTCM_CODE_DIFFERENTIAL:
            fix.status.status = NavSatStatus.STATUS_SBAS_FIX
        else:
            fix.status.status = NavSatStatus.STATUS_FIX
        fix.status.service = NavSatStatus.SERVICE_GPS
        fix.latitude = r.latitude
        fix.longitude = r.longitude
        fix.altitude = r.altitude_ellipsoid
        fix.position_covariance[0] = fix.position_covariance[4] = float(r.eph) ** 2
        fix.position_covariance[8] = float(r.epv) ** 2
        fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        self.fix_pub.publish(fix)

    def log_stats(self):
        now = self.now_s()
        if (self.last_rtcm_s is not None and not self.silence_warned
                and now - self.last_rtcm_s > RTCM_SILENCE_WARN_S):
            self.get_logger().warn(
                f'No RTCM from ntrip_client for {now - self.last_rtcm_s:.0f} s')
            self.silence_warned = True
        if not self.frames:
            return
        self.get_logger().info(
            f'RTCM to PX4: {self.frames} frames, {self.bytes} bytes, {self.chunks} chunks; '
            f'{self.split_frames} frames split, largest {self.largest_frame} bytes, '
            f'peak {self.peak_chunks_per_s} chunks in 1 s')
        self.frames = self.bytes = self.chunks = self.split_frames = 0
        self.largest_frame = self.peak_chunks_per_s = 0


def main():
    rclpy.init()
    node = RtcmXrceBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
