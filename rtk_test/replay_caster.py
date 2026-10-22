#!/usr/bin/env python3
"""A local NTRIP caster that replays a recorded correction stream, for bench and SITL tests.

Point the vehicle's NTRIP_HOST at this machine. It serves one mountpoint to
any number of clients, accepts any credentials, and replays --source with its
recorded timing (a caster_tap.py capture: tap.bin + tap_index.csv), looping.
Everything offered to clients is written to --out as ground truth:

  sent.bin, sent_index.csv   send_epoch_ns,offset,len,kind (base | pad)
  replay.log                 client connects/disconnects, outages, settings

Stress and fault options:
  --speed 3                 replay 3x faster (more corrections per second)
  --pad-frames 6 --pad-size 1029
                            after each epoch, add 6 valid RTCM3 frames of
                            1029 bytes (the maximum): exercises splitting into
                            300-byte chunks and PX4's queues. Message type
                            --pad-type (default 4095, proprietary range).
                            Confirm the receiver ignores it before using
                            this with real hardware.
  --outage 120:30           send nothing from 120 s to 150 s (repeatable)
  kill -USR1 <pid>          toggle an outage by hand

Standard library only. Use it on a lab network or localhost only.
"""
import argparse
import csv
import os
import signal
import socket
import sys
import threading
import time

EPOCH_GAP_S = 0.2  # chunks closer than this belong to one epoch


def crc24q(data):
    crc = 0
    for b in data:
        crc ^= b << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def rtcm_frame(msg_type, size, counter):
    """A valid RTCM3 frame of size bytes (8 to 1029) carrying msg_type and a counter."""
    payload_len = size - 6
    payload = bytearray(payload_len)
    payload[0] = msg_type >> 4
    payload[1] = (msg_type & 0xF) << 4
    for i in range(2, payload_len):
        payload[i] = (counter + i) & 0xFF
    head = bytes([0xD3, (payload_len >> 8) & 0x03, payload_len & 0xFF]) + payload
    return head + crc24q(head).to_bytes(3, 'big')


def load_source(bin_path, index_path):
    """[(seconds from start, bytes)] from a .bin and an index whose first column is ns time."""
    data = open(bin_path, 'rb').read()
    out = []
    with open(index_path) as f:
        rows = list(csv.DictReader(f))
    time_key = list(rows[0].keys())[0]
    offset = 0
    t0 = int(rows[0][time_key])
    for r in rows:
        n = int(r['len'])
        start = int(r['offset']) if 'offset' in r else offset
        out.append(((int(r[time_key]) - t0) / 1e9, data[start:start + n]))
        offset = start + n
    return out


class Caster:
    def __init__(self, a):
        self.a = a
        os.makedirs(a.out, exist_ok=True)
        self.sent = open(os.path.join(a.out, 'sent.bin'), 'wb')
        self.index = open(os.path.join(a.out, 'sent_index.csv'), 'w')
        self.index.write('send_epoch_ns,offset,len,kind\n')
        self.log_file = open(os.path.join(a.out, 'replay.log'), 'a')
        self.offset = 0
        self.clients = []
        self.lock = threading.Lock()
        self.manual_outage = False
        self.stopping = False
        self.pad_counter = 0
        self.outages = []
        for spec in a.outage:
            start, dur = spec.split(':')
            self.outages.append((float(start), float(start) + float(dur)))

    def log(self, text):
        line = f'{time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}.{int(time.time() % 1 * 1e6):06d}Z {text}'
        print(line, flush=True)
        self.log_file.write(line + '\n')
        self.log_file.flush()

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.a.bind, self.a.port))
        srv.listen(8)
        srv.settimeout(0.5)
        while not self.stopping:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self.client, args=(conn, addr), daemon=True).start()
        srv.close()

    def client(self, conn, addr):
        conn.settimeout(10)
        try:
            req = conn.recv(4096).decode('latin-1')
            path = req.split(' ')[1] if ' ' in req else ''
            if path in ('/', ''):
                body = f'STR;{self.a.mountpoint};\r\nENDSOURCETABLE\r\n'
                conn.sendall(f'SOURCETABLE 200 OK\r\nContent-Length: {len(body)}\r\n\r\n{body}'.encode())
                conn.close()
                return
            conn.sendall(b'ICY 200 OK\r\n')
        except OSError:
            conn.close()
            return
        conn.settimeout(1.0)
        with self.lock:
            self.clients.append(conn)
        self.log(f'client {addr[0]}:{addr[1]} connected ({path})')
        # Drain what the client sends (GGA) until it disconnects
        while not self.stopping:
            try:
                if not conn.recv(4096):
                    break
            except socket.timeout:
                continue
            except OSError:
                break
        self.drop(conn, addr, 'disconnected')

    def drop(self, conn, addr, why):
        with self.lock:
            if conn not in self.clients:
                return
            self.clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass
        self.log(f'client {addr[0] if addr else "?"} {why}')

    def broadcast(self, data, kind):
        self.index.write(f'{time.time_ns()},{self.offset},{len(data)},{kind}\n')
        self.sent.write(data)
        self.offset += len(data)
        with self.lock:
            clients = list(self.clients)
        for c in clients:
            try:
                c.sendall(data)
            except OSError as e:
                self.drop(c, None, f'dropped on send: {e}')

    def in_outage(self, elapsed):
        return self.manual_outage or any(s <= elapsed < e for s, e in self.outages)

    def replay(self, source):
        a = self.a
        period = source[-1][0] + 1.0
        start = time.monotonic()
        loop = 0
        was_out = False
        while not self.stopping:
            for i, (t, data) in enumerate(source):
                due = start + (loop * period + t) / a.speed
                while not self.stopping and time.monotonic() < due:
                    time.sleep(min(0.01, due - time.monotonic()))
                if self.stopping:
                    return
                elapsed = time.monotonic() - start
                out = self.in_outage(elapsed)
                if out != was_out:
                    self.log(f'outage {"started" if out else "ended"} at {elapsed:.1f} s')
                    was_out = out
                if out:
                    continue
                self.broadcast(data, 'base')
                next_t = source[i + 1][0] if i + 1 < len(source) else period
                if a.pad_frames and next_t - t > EPOCH_GAP_S:
                    for _ in range(a.pad_frames):
                        self.pad_counter += 1
                        self.broadcast(rtcm_frame(a.pad_type, a.pad_size, self.pad_counter), 'pad')
                self.sent.flush()
                self.index.flush()
            loop += 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--source', required=True, help='directory with tap.bin + tap_index.csv, or a .bin '
                   'with a matching _index.csv')
    p.add_argument('--out', required=True)
    p.add_argument('--bind', default='0.0.0.0')
    p.add_argument('--port', type=int, default=2101)
    p.add_argument('--mountpoint', default='BASE')
    p.add_argument('--speed', type=float, default=1.0)
    p.add_argument('--pad-frames', type=int, default=0)
    p.add_argument('--pad-size', type=int, default=1029)
    p.add_argument('--pad-type', type=int, default=4095)
    p.add_argument('--outage', action='append', default=[], metavar='START_S:DURATION_S')
    a = p.parse_args()
    if not 8 <= a.pad_size <= 1029:
        p.error('--pad-size must be 8 to 1029')
    if os.path.isdir(a.source):
        bin_path, index_path = os.path.join(a.source, 'tap.bin'), os.path.join(a.source, 'tap_index.csv')
    else:
        bin_path, index_path = a.source, a.source[:-4] + '_index.csv'
    source = load_source(bin_path, index_path)

    caster = Caster(a)
    caster.log(f'replaying {bin_path} ({len(source)} chunks, {source[-1][0]:.0f} s) at {a.speed}x on '
               f'{a.bind}:{a.port}/{a.mountpoint}; pad {a.pad_frames} x {a.pad_size} B type {a.pad_type}; '
               f'outages {a.outage}; pid {os.getpid()}')

    def toggle(*_):
        caster.manual_outage = not caster.manual_outage
        caster.log(f'manual outage {"on" if caster.manual_outage else "off"}')

    def stop(*_):
        caster.stopping = True
    signal.signal(signal.SIGUSR1, toggle)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    threading.Thread(target=caster.serve, daemon=True).start()
    caster.replay(source)
    caster.sent.flush()
    caster.index.flush()
    caster.log(f'stopped, {caster.offset} bytes offered')
    return 0


if __name__ == '__main__':
    sys.exit(main())
