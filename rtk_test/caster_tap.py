#!/usr/bin/env python3
"""Record the NTRIP caster's correction stream on a second, independent connection.

The reference for what the base sent (hop 0): analyze_run.py compares it with
what ntrip_client received on the vehicle. Run it on the lab PC for the whole
test session, before the first run starts and until the last one stops:

    python3 rtk_test/caster_tap.py --env-file .env --out ~/rtk_taps/session1

Writes to --out:
  tap.bin         the raw bytes, exactly as received
  tap_index.csv   recv_epoch_ns,offset,len for every recv() (wall clock)
  tap_clock.csv   this PC's clock offset to --time-server (NTP), every minute;
                  the vehicle's clock is held within 10 ms of the same server
  tap.log         connects, disconnects and errors
  tap_meta.json   settings (no credentials)

Standard library only. The request matches ntrip_client's (NTRIP v1, HTTP/1.0)
so the caster treats both connections alike. A GGA sentence with --lat/--lon
is sent on connect and every 10 s, for casters that wait for one.
"""
import argparse
import base64
import json
import os
import signal
import socket
import struct
import sys
import time

NTP_EPOCH = 2208988800
GGA_PERIOD_S = 10.0
CLOCK_PERIOD_S = 60.0


def env_file_values(path):
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key] = value.strip().strip('"').strip("'")
    return values


def ntp_offset_ms(server, timeout=1.0):
    """(offset ms, delay ms) of this clock relative to server; offset > 0: server ahead."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    req = bytearray(48)
    req[0] = 0x23  # NTP v4, client mode
    t0 = time.time()
    tx = struct.pack('!II', int(t0) + NTP_EPOCH, int((t0 % 1) * 2**32))
    req[40:48] = tx
    try:
        sock.sendto(req, (server, 123))
        d, _ = sock.recvfrom(512)
    finally:
        sock.close()
    t3 = time.time()
    if len(d) < 48 or d[0] & 7 != 4 or d[24:32] != tx:
        raise OSError('unusable NTP reply')

    def from_ntp(b):
        s, f = struct.unpack('!II', b)
        return s - NTP_EPOCH + f / 2**32
    t1, t2 = from_ntp(d[32:40]), from_ntp(d[40:48])
    return ((t1 - t0) + (t2 - t3)) / 2 * 1e3, ((t3 - t0) - (t2 - t1)) * 1e3


def gga(lat, lon):
    t = time.gmtime()
    lat_d, lon_d = abs(lat), abs(lon)
    lat_s = f'{int(lat_d):02d}{(lat_d % 1) * 60:07.4f}'
    lon_s = f'{int(lon_d):03d}{(lon_d % 1) * 60:07.4f}'
    body = (f'GPGGA,{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}.00,{lat_s},{"N" if lat >= 0 else "S"},'
            f'{lon_s},{"E" if lon >= 0 else "W"},1,10,1.0,30.0,M,-30.0,M,,')
    checksum = 0
    for c in body.encode():
        checksum ^= c
    return f'${body}*{checksum:02X}\r\n'.encode()


class Tap:
    def __init__(self, args):
        self.args = args
        os.makedirs(args.out, exist_ok=True)
        self.bin = open(os.path.join(args.out, 'tap.bin'), 'ab')
        self.offset = self.bin.tell()
        index_path = os.path.join(args.out, 'tap_index.csv')
        new_index = not os.path.exists(index_path)
        self.index = open(index_path, 'a')
        if new_index:
            self.index.write('recv_epoch_ns,offset,len\n')
        clock_path = os.path.join(args.out, 'tap_clock.csv')
        new_clock = not os.path.exists(clock_path)
        self.clock = open(clock_path, 'a')
        if new_clock:
            self.clock.write('epoch_ns,offset_ms,delay_ms,error\n')
        self.log_file = open(os.path.join(args.out, 'tap.log'), 'a')
        self.stopping = False
        self.last_clock = 0.0

    def log(self, text):
        line = f'{time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}.{int(time.time() % 1 * 1e6):06d}Z {text}'
        print(line, flush=True)
        self.log_file.write(line + '\n')
        self.log_file.flush()

    def check_clock(self):
        self.last_clock = time.monotonic()
        try:
            off, delay = ntp_offset_ms(self.args.time_server)
            self.clock.write(f'{time.time_ns()},{off:.3f},{delay:.3f},\n')
        except OSError as e:
            self.clock.write(f'{time.time_ns()},,,{e}\n')
        self.clock.flush()

    def request(self):
        a = self.args
        req = f'GET /{a.mountpoint} HTTP/1.0\r\nUser-Agent: NTRIP ntrip_client_ros\r\n'
        if a.user:
            cred = base64.b64encode(f'{a.user}:{a.password}'.encode()).decode()
            req += f'Authorization: Basic {cred}\r\n'
        return (req + '\r\n').encode()

    def session(self):
        a = self.args
        sock = socket.create_connection((a.host, a.port), timeout=10)
        sock.settimeout(1.0)
        try:
            sock.sendall(self.request())
            # NTRIP v1 casters answer "ICY 200 OK\r\n" and stream right after it;
            # HTTP answers end their header with a blank line. Stream bytes can
            # contain \r\n\r\n, so only HTTP answers are split there.
            received = b''
            data = None
            deadline = time.monotonic() + 10
            while data is None and time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                received += chunk
                if b'\r\n' not in received:
                    continue
                first_line, rest = received.split(b'\r\n', 1)
                if not first_line.startswith(b'HTTP/'):
                    data = rest
                elif b'\r\n\r\n' in received:
                    header, data = received.split(b'\r\n\r\n', 1)
                    if b'chunked' in header.lower():
                        # Not supported: decoding it wrong would corrupt the reference
                        raise OSError('caster uses chunked transfer encoding; not supported')
            if data is None:
                raise OSError(f'no complete response from caster: {received[:80]!r}')
            first_line = received.split(b'\r\n', 1)[0].decode('latin-1')
            if not any(ok in first_line for ok in ('ICY 200 OK', 'HTTP/1.0 200 OK', 'HTTP/1.1 200 OK')):
                raise OSError(f'caster refused: {first_line!r}')
            self.log(f'connected to {a.host}:{a.port}/{a.mountpoint}: {first_line}')
            last_gga = 0.0
            if data:
                self.write(data)
            while not self.stopping:
                now = time.monotonic()
                if now - last_gga >= GGA_PERIOD_S:
                    sock.sendall(gga(a.lat, a.lon))
                    last_gga = now
                if now - self.last_clock >= CLOCK_PERIOD_S:
                    self.check_clock()
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise OSError('caster closed the connection')
                self.write(data)
        finally:
            sock.close()

    def write(self, data):
        self.index.write(f'{time.time_ns()},{self.offset},{len(data)}\n')
        self.bin.write(data)
        self.offset += len(data)
        self.bin.flush()
        self.index.flush()

    def run(self):
        self.check_clock()
        while not self.stopping:
            try:
                self.session()
            except OSError as e:
                if self.stopping:
                    break
                self.log(f'disconnected: {e}; reconnecting in 2 s')
                time.sleep(2)
        self.check_clock()
        self.log(f'stopped, {self.offset} bytes total')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--env-file', help='read NTRIP_HOST/PORT/MOUNTPOINT/USERNAME/PASSWORD from this .env')
    p.add_argument('--host')
    p.add_argument('--port', type=int)
    p.add_argument('--mountpoint')
    p.add_argument('--user')
    p.add_argument('--password')
    p.add_argument('--lat', type=float, default=29.6282736, help='GGA latitude (deg)')
    p.add_argument('--lon', type=float, default=-82.3606362, help='GGA longitude (deg)')
    p.add_argument('--time-server', default='192.168.1.201', help='NTP server the vehicles sync to')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    env = env_file_values(a.env_file) if a.env_file else {}
    a.host = a.host or env.get('NTRIP_HOST')
    a.port = a.port or int(env.get('NTRIP_PORT', 2101))
    a.mountpoint = a.mountpoint or env.get('NTRIP_MOUNTPOINT')
    a.user = a.user if a.user is not None else env.get('NTRIP_USERNAME')
    a.password = a.password if a.password is not None else env.get('NTRIP_PASSWORD', '')
    if not a.host or not a.mountpoint:
        p.error('need --host and --mountpoint (or --env-file)')

    tap = Tap(a)
    with open(os.path.join(a.out, 'tap_meta.json'), 'w') as f:
        json.dump({'host': a.host, 'port': a.port, 'mountpoint': a.mountpoint, 'gga_lat': a.lat,
                   'gga_lon': a.lon, 'time_server': a.time_server, 'pc': socket.gethostname(),
                   'started_epoch_ns': time.time_ns()}, f, indent=2)

    def stop(*_):
        tap.stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    tap.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
