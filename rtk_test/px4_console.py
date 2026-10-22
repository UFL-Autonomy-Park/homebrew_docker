#!/usr/bin/env python3
"""Run PX4 console (NuttShell) commands over MAVLink and save their output.

Uses MAVLink SERIAL_CONTROL like PX4's Tools/mavlink_shell.py, so it needs a
MAVLink link: with the XRCE agent on TELEM2, use the flight controller's USB
port (close QGroundControl first, it holds the port) or a telemetry radio.

  # Before and after every run, into the run directory
  python3 rtk_test/px4_console.py snapshot --device /dev/ttyACM0 \\
      --out logs/rtk_runs/<run>/meta/px4_before.txt

  # Once per vehicle: put logger_topics.txt on the SD card, then reboot PX4
  python3 rtk_test/px4_console.py install-logger-topics --device /dev/ttyACM0

  # Any commands
  python3 rtk_test/px4_console.py run --device udp:127.0.0.1:14550 "perf" "top once"

snapshot records the build (ver all), all parameters, perf counters (uavcan:
gnss: rtcm stream pub failed counts chunks lost toward CAN), uavcan, uXRCE-DDS
client and logger status, CPU load, the boot log (dmesg: the logger prints
"logging N topics from logger_topics.txt" when it reads the file), and the
latest GNSS samples. It also checks SDLOG_MODE: the default (0) only logs
while armed, so a bench run would have no log.
"""
import argparse
import re
import sys
import time

from pymavlink import mavutil

SHELL_DEVICE = 10  # SERIAL_CONTROL_DEV_SHELL
PROMPTS = (b'nsh> ', b'pxh> ')
ANSI = re.compile(rb'\x1b\[[0-9;]*[A-Za-z]')

SNAPSHOT_COMMANDS = [
    'ver all',
    'param show SDLOG_*',
    'param show UXRCE_DDS_*',
    'param show UAVCAN_*',
    'param show GPS_*',
    'param show SER_TEL2_BAUD',
    'perf',
    'uavcan status',
    'uxrce_dds_client status',
    'logger status',
    'top once',
    'free',
    'dmesg',
    'listener sensor_gnss -n 2',
    'listener vehicle_gnss -n 1',
    'listener sensors_status_gnss -n 1',
    'param show',
]


class Shell:
    def __init__(self, device, baud):
        self.mav = mavutil.mavlink_connection(device, baud=baud, autoreconnect=True)
        self.mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GENERIC,
                                    mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        if self.mav.wait_heartbeat(timeout=10) is None:
            raise SystemExit(f'No MAVLink heartbeat on {device}')
        self.write(b'\n')
        self.read_until_prompt(5.0)

    def write(self, data):
        while data:
            n = min(len(data), 70)
            buf = list(data[:n]) + [0] * (70 - n)
            self.mav.mav.serial_control_send(
                SHELL_DEVICE,
                mavutil.mavlink.SERIAL_CONTROL_FLAG_EXCLUSIVE | mavutil.mavlink.SERIAL_CONTROL_FLAG_RESPOND,
                0, 0, n, buf)
            data = data[n:]

    def poll(self):
        # An empty request keeps PX4 sending the shell's output back
        self.mav.mav.serial_control_send(
            SHELL_DEVICE,
            mavutil.mavlink.SERIAL_CONTROL_FLAG_EXCLUSIVE | mavutil.mavlink.SERIAL_CONTROL_FLAG_RESPOND,
            0, 0, 0, [0] * 70)

    def read_until_prompt(self, timeout):
        out = b''
        deadline = time.monotonic() + timeout
        last_poll = 0.0
        while time.monotonic() < deadline:
            m = self.mav.recv_match(type='SERIAL_CONTROL', blocking=True, timeout=0.05)
            if m is not None and m.count:
                out += bytes(m.data[:m.count])
                # Long outputs (param show) keep extending the deadline
                deadline = max(deadline, time.monotonic() + 2.0)
                if out.endswith(PROMPTS):
                    return out, True
            if time.monotonic() - last_poll > 0.1:
                self.poll()
                last_poll = time.monotonic()
        return out, False

    def run(self, command, timeout=10.0):
        self.write(command.encode() + b'\n')
        out, complete = self.read_until_prompt(timeout)
        text = ANSI.sub(b'', out).decode('utf-8', 'replace').replace('\r', '')
        lines = text.split('\n')
        # Drop the echoed command (after any leading prompt or blank lines) and the trailing prompt
        while lines and not lines[0].strip():
            lines = lines[1:]
        if lines and lines[0].strip().endswith(command.strip()):
            lines = lines[1:]
        if lines and lines[-1].strip() in ('nsh>', 'pxh>'):
            lines = lines[:-1]
        return '\n'.join(lines), complete

    def close(self):
        self.mav.mav.serial_control_send(SHELL_DEVICE, 0, 0, 0, 0, [0] * 70)


def snapshot(shell, out_path):
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    parts = [f'# PX4 console snapshot {stamp} (epoch_ns {time.time_ns()})\n']
    warnings = []
    for cmd in SNAPSHOT_COMMANDS:
        text, complete = shell.run(cmd, timeout=30.0 if cmd == 'param show' else 10.0)
        parts.append(f'\n===== {cmd}{"" if complete else "  [INCOMPLETE: no prompt before timeout]"}\n{text}\n')
        if cmd == 'param show SDLOG_*':
            m = re.search(r'SDLOG_MODE\s*\[[^\]]*\]\s*:\s*(-?\d+)', text)
            if m and m.group(1) == '0':
                warnings.append('SDLOG_MODE is 0: PX4 only logs while armed. For bench runs set it '
                                'to 1 or 2 and reboot.')
        if cmd == 'dmesg' and 'logger_topics.txt' not in text:
            warnings.append('Boot log does not mention logger_topics.txt: the extra topics '
                            '(every correction chunk) may not be logged.')
    for w in warnings:
        parts.insert(1, f'# WARNING: {w}\n')
        print(f'WARNING: {w}', file=sys.stderr)
    with open(out_path, 'w') as f:
        f.write(''.join(parts))
    print(f'Wrote {out_path}')


def install_logger_topics(shell, path, sd_root):
    lines = [ln.rstrip('\n') for ln in open(path)]
    for ln in lines:
        if len(ln) >= 79:
            raise SystemExit(f'Line too long for PX4 (max 78): {ln!r}')
        if '"' in ln:
            raise SystemExit(f'Line contains a double quote: {ln!r}')
    target = f'{sd_root}/etc/logging/logger_topics.txt'
    shell.run(f'mkdir {sd_root}/etc')
    shell.run(f'mkdir {sd_root}/etc/logging')
    shell.run(f'rm {target}')
    for ln in lines:
        # Comments are kept so the file on the card explains itself
        shell.run(f'echo "{ln}" >> {target}')
    text, _ = shell.run(f'cat {target}', timeout=10.0)
    got = [ln.rstrip() for ln in text.strip('\n').split('\n')]
    want = [ln.rstrip() for ln in lines]
    if got != want:
        print('\n'.join(got))
        raise SystemExit(f'Verification failed: {target} differs from {path} (shown above)')
    print(f'Installed and verified {target} ({len(lines)} lines). Reboot PX4 to apply; after the '
          'reboot, a snapshot should show "logging N topics from logger_topics.txt" in dmesg.')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('action', choices=['snapshot', 'install-logger-topics', 'run'])
    p.add_argument('commands', nargs='*', help='for run: the commands')
    p.add_argument('--device', default='/dev/ttyACM0', help='pymavlink connection string')
    p.add_argument('--baud', type=int, default=57600)
    p.add_argument('--out', help='for snapshot: output file')
    p.add_argument('--topics-file', default=None, help='for install-logger-topics '
                   '(default: logger_topics.txt next to this script)')
    p.add_argument('--sd-root', default='/fs/microsd')
    a = p.parse_args()
    shell = Shell(a.device, a.baud)
    try:
        if a.action == 'snapshot':
            snapshot(shell, a.out or f'px4_snapshot_{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}.txt')
        elif a.action == 'install-logger-topics':
            import os
            path = a.topics_file or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 'logger_topics.txt')
            install_logger_topics(shell, path, a.sd_root)
        else:
            for cmd in a.commands:
                text, complete = shell.run(cmd, timeout=30.0)
                print(f'===== {cmd}{"" if complete else "  [INCOMPLETE]"}\n{text}')
    finally:
        shell.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
