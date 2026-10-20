"""Ask the FCU to stream chosen MAVLink messages at chosen rates, via MAVROS.

PX4 keeps rates set with MAV_CMD_SET_MESSAGE_INTERVAL in RAM only: a PX4
reboot silently restores its defaults. So the rates are (re)applied every time
MAVROS reports the FCU connected, not once at startup. Started by
homebrew.launch.py when MAVLINK_RATES is set in .env (README section 9).
"""
import rclpy
from mavros_msgs.msg import State
from mavros_msgs.srv import MessageInterval
from rclpy.node import Node

# Seconds between attempts while some rates are not yet confirmed by the FCU
RETRY_PERIOD_S = 2.0


class MavlinkRates(Node):
    """Apply message_ids[i] at rates_hz[i] on every FCU (re)connection."""

    def __init__(self):
        super().__init__('mavlink_rates')
        ids = self.declare_parameter('message_ids', [0]).value
        rates = self.declare_parameter('rates_hz', [0.0]).value
        if len(ids) != len(rates):
            raise ValueError('message_ids and rates_hz must be the same length')
        self.rates = dict(zip(ids, rates))

        # message_id -> True once the FCU accepted it since the last connection
        self.done = {}
        self.in_flight = set()
        self.connected = False

        self.client = self.create_client(MessageInterval, 'mavros/set_message_interval')
        self.create_subscription(State, 'mavros/state', self.state_cb, 10)
        self.create_timer(RETRY_PERIOD_S, self.apply)

    def state_cb(self, m):
        if m.connected and not self.connected:
            self.get_logger().info('FCU connected: applying MAVLink message rates')
            self.done = {i: False for i in self.rates}
            self.connected = True
            self.apply()
        elif not m.connected and self.connected:
            self.get_logger().warn('FCU disconnected: rates will be reapplied on reconnect')
            self.connected = False

    def apply(self):
        if not self.connected or not self.client.service_is_ready():
            return
        for msg_id, rate in self.rates.items():
            if self.done.get(msg_id) or msg_id in self.in_flight:
                continue
            req = MessageInterval.Request(message_id=msg_id, message_rate=rate)
            self.in_flight.add(msg_id)
            self.client.call_async(req).add_done_callback(
                lambda fut, i=msg_id, r=rate: self.result_cb(fut, i, r))

    def result_cb(self, fut, msg_id, rate):
        self.in_flight.discard(msg_id)
        res = fut.result()
        if res is not None and res.success:
            self.done[msg_id] = True
            self.get_logger().info(f'message {msg_id}: {rate:g} Hz accepted by FCU')
        else:
            self.get_logger().warn(
                f'message {msg_id}: {rate:g} Hz not accepted, retrying in {RETRY_PERIOD_S:g} s')


def main():
    rclpy.init()
    node = MavlinkRates()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
