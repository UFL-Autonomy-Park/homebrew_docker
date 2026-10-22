#!/usr/bin/env python3
"""Analyze one RTK corrections test run: hop-by-hop integrity, latency and fix quality.

    python3 rtk_test/analyze_run.py logs/rtk_runs/<run> [--ulog <file.ulg>] [--tap <tap dir>]

Reads the run directory written by record_run.sh (bag/, meta/), the PX4 log
covering the run (default: the only .ulg in the run directory) and optionally a
caster_tap.py capture or replay_caster.py output (sent.bin) as the hop 0
reference. Writes <run>/analysis/: report.md (paste into the GitHub issue,
with the PNGs), report.html (self-contained), summary.json (for
compare_runs.py) and the charts.

The correction stream is checked at every hop it passes:
  hop 0  caster            tap.bin / sent.bin (wall clock of the lab PC)
  hop 1  ntrip_client out  bag: rtcm_bridge/rtcm or mavros/gps_rtk/send_rtcm
  hop 2  to the FCU link   bag: fmu/in/rtcm_corrections (XRCE) or the
                           GPS_RTCM_DATA in /uas<N>/mavlink_sink (MAVROS)
  hop 3  inside PX4        ULog rtcm_corrections (needs logger_topics.txt)
  hop 4  to the receiver   PX4's "rtcm_corrections lost" warnings (uORB
                           queue overflow before the GNSS driver); the CAN
                           send-failure counter is only in px4_console.py
                           snapshots (perf)
  hop 5  receiver          ULog sensor_gnss: fix type on every sample
Each hop's byte stream is split into RTCM3 frames and every frame's CRC-24Q is
checked, so a comparison does not depend on how the stream was chunked. The
XRCE hop (2 to 3) is also compared chunk for chunk, byte for byte.

Only the Python standard library, numpy, matplotlib and pyulog are needed: the
bag's SQLite file is read directly and its messages decoded here, so neither
ROS nor px4_msgs has to be installed on the analysis machine.
"""
import argparse
import base64
import collections
import csv
import datetime
import difflib
import glob
import hashlib
import html
import json
import os
import re
import sqlite3
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MAVLINK_GPS_RTCM_DATA = 233
FIX_RTK_FLOAT = 5
FIX_RTK_FIXED = 6
ARMING_STATE_ARMED = 2
DDS_TIMESYNC = 2
MAVLINK_TIMESYNC = 1
# A pause in corrections longer than this is an outage (fix drops are expected)
CORRECTION_GAP_S = 5.0
# Unmatched items this close to a comparison window's ends are edge effects
EDGE_S = 2.0


# ---------------------------------------------------------------- RTCM3 frames

def _crc_table():
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc & 0xFFFFFF)
    return table


CRC_TABLE = _crc_table()


def crc24q(data):
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ CRC_TABLE[(crc >> 16) ^ b]
    return crc


class Framer:
    """Splits a byte stream into RTCM3 frames, checking each frame's CRC.

    feed() takes the stream in pieces with the time each piece arrived; a frame
    gets the time of the piece that completed it.
    """

    def __init__(self):
        self.buf = bytearray()
        self.frames = []  # (t, frame bytes)
        self.crc_failures = 0
        self.garbage_bytes = 0
        self.bytes_in = 0

    def feed(self, data, t):
        self.bytes_in += len(data)
        self.buf += data
        while True:
            start = self.buf.find(b'\xd3')
            if start < 0:
                self.garbage_bytes += len(self.buf)
                self.buf.clear()
                return
            if start:
                self.garbage_bytes += start
                del self.buf[:start]
            if len(self.buf) < 3:
                return
            length = ((self.buf[1] & 0x03) << 8) | self.buf[2]
            if self.buf[1] & 0xFC:
                # Reserved bits must be 0: not a frame start
                self.garbage_bytes += 1
                del self.buf[:1]
                continue
            if len(self.buf) < length + 6:
                return
            frame = bytes(self.buf[:length + 6])
            if crc24q(frame[:-3]) == int.from_bytes(frame[-3:], 'big'):
                self.frames.append((t, frame))
                del self.buf[:length + 6]
            else:
                self.crc_failures += 1
                self.garbage_bytes += 1
                del self.buf[:1]

    def finish(self):
        self.garbage_bytes += len(self.buf)
        self.buf.clear()
        return self


def frame_type(frame):
    return ((frame[3] << 4) | (frame[4] >> 4)) if len(frame) >= 6 + 2 else -1


def frames_from_chunks(chunks):
    """chunks: [(t, bytes)] -> Framer with frames."""
    f = Framer()
    for t, data in chunks:
        f.feed(data, t)
    return f.finish()


# ---------------------------------------------------------------- bag (rosbag2 sqlite3)

class Cdr:
    """Minimal little-endian CDR reader (ROS 2 serialization)."""

    def __init__(self, data):
        if data[1] != 1:
            raise ValueError('only little-endian CDR is supported')
        self.d = data
        self.p = 4

    def align(self, n):
        self.p = 4 + ((self.p - 4 + n - 1) // n) * n

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def _num(self, fmt, n):
        self.align(n)
        v = struct.unpack_from('<' + fmt, self.d, self.p)[0]
        self.p += n
        return v

    def u16(self):
        return self._num('H', 2)

    def u32(self):
        return self._num('I', 4)

    def i32(self):
        return self._num('i', 4)

    def u64(self):
        return self._num('Q', 8)

    def string(self):
        n = self.u32()
        s = self.d[self.p:self.p + n - 1].decode('utf-8', 'replace') if n else ''
        self.p += n
        return s

    def u8_seq(self, n=None):
        n = self.u32() if n is None else n
        v = bytes(self.d[self.p:self.p + n])
        self.p += n
        return v

    def header(self):
        sec, nsec = self.i32(), self.u32()
        return sec + nsec * 1e-9, self.string()


def decode_rtcm(data):
    c = Cdr(data)
    c.header()
    return c.u8_seq()


def decode_rtcm_data(data):
    """px4_msgs/RtcmData -> (timestamp, device_id, len, flags, payload)."""
    c = Cdr(data)
    ts, dev, n, flags = c.u64(), c.u32(), c.u16(), c.u8()
    payload = c.u8_seq(300)
    return ts, dev, n, flags, payload[:n]


def decode_mavlink(data):
    """mavros_msgs/Mavlink -> (msgid, sysid, compid, payload bytes)."""
    c = Cdr(data)
    c.header()
    c.u8()  # framing_status
    c.u8()  # magic
    length = c.u8()
    c.u8()
    c.u8()
    c.u8()  # seq
    sysid, compid = c.u8(), c.u8()
    msgid = c.u32()
    c.u16()
    n = c.u32()
    c.align(8)
    payload = b''.join(struct.pack('<Q', c.u64()) for _ in range(n))
    return msgid, sysid, compid, payload[:length]


def decode_log(data):
    """rcl_interfaces/Log -> (level, name, msg)."""
    c = Cdr(data)
    c.i32()
    c.u32()
    level = c.u8()
    return level, c.string(), c.string()


def read_bag(bag_dir, wanted):
    """{topic: [(recv epoch s, raw cdr)]} for the wanted topics, from every .db3 in bag_dir."""
    out = collections.defaultdict(list)
    types = {}
    files = sorted(glob.glob(os.path.join(bag_dir, '*.db3')))
    if not files:
        raise SystemExit(f'No .db3 files in {bag_dir}')
    for path in files:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        topics = {tid: (name, typ) for tid, name, typ in con.execute('select id, name, type from topics')}
        for tid, (name, typ) in topics.items():
            types[name] = typ
            if name not in wanted:
                continue
            for ts, data in con.execute('select timestamp, data from messages where topic_id=? order by timestamp',
                                        (tid,)):
                out[name].append((ts / 1e9, bytes(data)))
        con.close()
    return out, types


# ---------------------------------------------------------------- ULog

def load_ulog(path):
    from pyulog import ULog
    names = ['rtcm_corrections', 'sensor_gnss', 'timesync_status', 'logger_status', 'vehicle_status']
    return ULog(path, names)


def ulog_datasets(ulog, name):
    return [d for d in ulog.data_list if d.name == name]


def ulog_rtcm_instances(ulog):
    """{multi_id: [(px4 ts s, device_id, payload)]}"""
    out = {}
    for d in ulog_datasets(ulog, 'rtcm_corrections'):
        n = len(d.data['timestamp'])
        if not n:
            continue
        mat = np.stack([d.data[f'data[{i}]'] for i in range(300)], axis=1).astype(np.uint8)
        lens = d.data['len'].astype(int)
        out[d.multi_id] = [(d.data['timestamp'][i] / 1e6, int(d.data['device_id'][i]),
                            mat[i, :lens[i]].tobytes()) for i in range(n)]
    return out


class ClockMap:
    """PX4 time (s since boot) <-> companion wall clock (epoch s), from timesync_status.

    PX4's timesync publishes estimated_offset = PX4 time - companion time, so
    companion = px4 - offset (checked against the data in check_sign()).
    """

    def __init__(self, ulog, source_protocol):
        self.t, self.off = [], []
        for d in ulog_datasets(ulog, 'timesync_status'):
            sp = d.data.get('source_protocol')
            if sp is None:
                continue
            sel = sp == source_protocol
            self.t += list(d.data['timestamp'][sel] / 1e6)
            self.off += list(d.data['estimated_offset'][sel] / 1e6)
        order = np.argsort(self.t)
        self.t = np.array(self.t)[order]
        self.off = np.array(self.off)[order]
        self.sign = 1.0
        self.valid = len(self.t) > 0 and np.any(self.off != 0)

    def offset_at(self, px4_t):
        return np.interp(px4_t, self.t, self.off)

    def to_wall(self, px4_t):
        return np.asarray(px4_t) - self.sign * self.offset_at(px4_t)

    def to_px4(self, wall_t):
        # offset changes slowly: one fixed-point step is accurate to microseconds
        guess = np.asarray(wall_t) + self.sign * np.median(self.off)
        return np.asarray(wall_t) + self.sign * self.offset_at(guess)


# ---------------------------------------------------------------- comparison

def compare(a_items, b_items, window=None):
    """Align two ordered sequences of (t, key, info); count matched / missing / extra.

    Items whose time lies within EDGE_S of the window's ends and that do not
    match are reported as edge effects, not losses.
    """
    a_keys = [k for _, k, _ in a_items]
    b_keys = [k for _, k, _ in b_items]
    sm = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    lo, hi = window if window else (-np.inf, np.inf)
    res = {'a_count': len(a_items), 'b_count': len(b_items), 'matched': 0, 'missing': 0, 'extra': 0,
           'edge_missing': 0, 'edge_extra': 0, 'missing_examples': [], 'extra_examples': [], 'pairs': []}

    def edge(t):
        return t < lo + EDGE_S or t > hi - EDGE_S

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            res['matched'] += i2 - i1
            res['pairs'] += [(a_items[i][0], b_items[j][0]) for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        for i in range(i1, i2):
            if edge(a_items[i][0]):
                res['edge_missing'] += 1
            else:
                res['missing'] += 1
                if len(res['missing_examples']) < 20:
                    res['missing_examples'].append({'t': a_items[i][0], **a_items[i][2]})
        for j in range(j1, j2):
            if edge(b_items[j][0]):
                res['edge_extra'] += 1
            else:
                res['extra'] += 1
                if len(res['extra_examples']) < 20:
                    res['extra_examples'].append({'t': b_items[j][0], **b_items[j][2]})
    return res


def frame_items(frames):
    return [(t, hashlib.blake2b(f, digest_size=12).digest(), {'type': frame_type(f), 'size': len(f)})
            for t, f in frames]


def chunk_items(chunks):
    return [(t, hashlib.blake2b(c, digest_size=12).digest(), {'size': len(c)}) for t, c in chunks]


def pct(values, q):
    return float(np.percentile(values, q)) if len(values) else None


# ---------------------------------------------------------------- analysis

def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%H:%M:%S.%f')[:-3] + 'Z'


def load_events(run):
    events = []
    path = os.path.join(run, 'meta', 'events.txt')
    if os.path.exists(path):
        for line in open(path):
            stamp, _, text = line.strip().partition(' ')
            try:
                t = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')[:26] + '+00:00'
                                                    if stamp.endswith('Z') else stamp).timestamp()
            except ValueError:
                continue
            events.append((t, text))
    return events


def load_tap(tap_dir):
    """[(epoch s, bytes)] from caster_tap.py (tap.bin) or replay_caster.py (sent.bin) output."""
    for bin_name, index_name in (('tap.bin', 'tap_index.csv'), ('sent.bin', 'sent_index.csv')):
        bin_path = os.path.join(tap_dir, bin_name)
        if os.path.exists(bin_path):
            data = open(bin_path, 'rb').read()
            with open(os.path.join(tap_dir, index_name)) as f:
                rows = list(csv.reader(f))[1:]
            return bin_name, [(int(r[0]) / 1e9, data[int(r[1]):int(r[1]) + int(r[2])]) for r in rows]
    raise SystemExit(f'No tap.bin or sent.bin in {tap_dir}')


def analyze(run, ulog_path, tap_dir, device_id, criteria):
    meta = json.load(open(os.path.join(run, 'meta', 'run.json')))
    mode, ns, tgt = meta['mode'], meta['namespace'], meta.get('tgt_system', '')
    s = {'run': os.path.basename(os.path.normpath(run)), 'label': meta.get('label'), 'mode': mode,
         'namespace': ns, 'start_utc': meta.get('start_utc'), 'stop_utc': meta.get('stop_utc'),
         'warnings': [], 'hops': {}, 'comparisons': {}}
    warn = s['warnings'].append

    if mode == 'xrce':
        t_frames, t_link = f'/{ns}/rtcm_bridge/rtcm', f'/{ns}/fmu/in/rtcm_corrections'
    else:
        t_frames, t_link = f'/{ns}/mavros/gps_rtk/send_rtcm', f'/uas{tgt}/mavlink_sink'
    bag, bag_types = read_bag(os.path.join(run, 'bag'), {t_frames, t_link, '/rosout'})
    if not bag[t_frames]:
        warn(f'No messages on {t_frames}: no corrections reached the bridge during the run.')

    # hop 1: whole frames from ntrip_client
    hop1_chunks = [(t, decode_rtcm(d)) for t, d in bag[t_frames]]
    hop1 = frames_from_chunks(hop1_chunks)
    multi_frame_msgs = sum(1 for _, c in hop1_chunks if c and len(frames_from_chunks([(0, c)]).frames) != 1)

    # hop 2: what went onto the FCU link
    hop2_chunks = []
    if mode == 'xrce':
        devs = collections.Counter()
        for t, d in bag[t_link]:
            _, dev, _, _, payload = decode_rtcm_data(d)
            devs[dev] += 1
            hop2_chunks.append((t, payload))
        if devs and set(devs) != {device_id}:
            warn(f'Chunks on {t_link} carry device_ids {dict(devs)}, expected only {device_id}.')
    else:
        for t, d in bag[t_link]:
            msgid, _, _, payload = decode_mavlink(d)
            if msgid == MAVLINK_GPS_RTCM_DATA:
                payload = payload.ljust(182, b'\0')
                hop2_chunks.append((t, payload[2:2 + payload[1]]))
    hop2 = frames_from_chunks(hop2_chunks)

    bag_times = [t for t, _ in hop1_chunks] + [t for t, _ in hop2_chunks]
    window = (min(bag_times), max(bag_times)) if bag_times else (meta['start_epoch_ns'] / 1e9,
                                                                 meta.get('stop_epoch_ns', 0) / 1e9)

    def hop_summary(framer, chunks):
        sizes = [len(f) for _, f in framer.frames]
        types = collections.Counter(frame_type(f) for _, f in framer.frames)
        return {'chunks': len(chunks), 'bytes': framer.bytes_in, 'frames': len(framer.frames),
                'crc_failures': framer.crc_failures, 'garbage_bytes': framer.garbage_bytes,
                'largest_frame': max(sizes) if sizes else 0,
                'largest_chunk': max((len(c) for _, c in chunks), default=0),
                'frame_types': {str(k): v for k, v in sorted(types.items())}}

    s['hops']['1_ntrip_client'] = hop_summary(hop1, hop1_chunks)
    s['hops']['1_ntrip_client']['messages_not_one_frame'] = multi_frame_msgs
    s['hops']['2_fcu_link'] = hop_summary(hop2, hop2_chunks)
    s['comparisons']['1_to_2_frames'] = compare(frame_items(hop1.frames), frame_items(hop2.frames), window)
    s['frames_over_720'] = sum(1 for _, f in hop1.frames if len(f) > 720)
    s['frames_over_300'] = sum(1 for _, f in hop1.frames if len(f) > 300)
    if mode == 'mavros' and s['frames_over_720']:
        warn(f'{s["frames_over_720"]} frames over 720 bytes: MAVROS drops these by design.')

    # hop 0: the caster, from an independent connection
    if tap_dir:
        kind, tap_chunks = load_tap(tap_dir)
        lo, hi = window
        tap_chunks = [(t, c) for t, c in tap_chunks if lo - EDGE_S <= t <= hi + EDGE_S]
        hop0 = frames_from_chunks(tap_chunks)
        s['hops']['0_caster'] = hop_summary(hop0, tap_chunks)
        s['hops']['0_caster']['source'] = kind
        s['comparisons']['0_to_1_frames'] = compare(frame_items(hop0.frames), frame_items(hop1.frames),
                                                    (lo - EDGE_S, hi + EDGE_S))
        clock_csv = os.path.join(tap_dir, 'tap_clock.csv')
        if os.path.exists(clock_csv):
            offs = [abs(float(r['offset_ms'])) + float(r['delay_ms']) / 2
                    for r in csv.DictReader(open(clock_csv)) if r['offset_ms']]
            s['tap_clock_worst_ms'] = max(offs) if offs else None
            if offs and max(offs) > 50:
                warn(f'Tap PC clock was up to {max(offs):.0f} ms off the time server: '
                     'hop 0 alignment is unreliable.')
    else:
        warn('No caster tap: hop 0 (what the base sent) is not verified for this run.')

    # bridge counters from /rosout
    bridge = collections.Counter()
    ntrip_log = []
    for t, d in bag['/rosout']:
        level, name, msg = decode_log(d)
        m = re.match(r'RTCM to PX4: (\d+) frames, (\d+) bytes, (\d+) chunks; (\d+) frames split, largest (\d+) '
                     r'bytes, peak (\d+) chunks in 1 s', msg)
        if m and 'rtcm_xrce_bridge' in name:
            for k, v in zip(('frames', 'bytes', 'chunks', 'split', 'largest', 'peak'), map(int, m.groups())):
                bridge[k] = max(bridge[k], v) if k in ('largest', 'peak') else bridge[k] + v
        elif 'ntrip' in name and level >= 30:
            ntrip_log.append(f'{iso(t)} {msg}')
    if bridge:
        s['bridge_log'] = dict(bridge)
    s['ntrip_client_warnings'] = ntrip_log[:50]

    # ---------------- PX4 side
    if not ulog_path:
        found = glob.glob(os.path.join(run, '*.ulg'))
        if len(found) == 1:
            ulog_path = found[0]
        elif len(found) > 1:
            raise SystemExit(f'Several .ulg in {run}: pass --ulog')
    s['ulog'] = os.path.basename(ulog_path) if ulog_path else None
    fix = None
    if not ulog_path:
        warn('No PX4 log: hops 3-5 (inside PX4, to the receiver, the fix) are not verified for this run.')
    else:
        ulog = load_ulog(ulog_path)
        info = ulog.msg_info_dict
        s['px4'] = {'ver_sw': info.get('ver_sw'), 'ver_sw_branch': info.get('ver_sw_branch'),
                    'sys_name': info.get('sys_name'), 'ver_hw': info.get('ver_hw'),
                    'params': {k: v for k, v in ulog.initial_parameters.items()
                               if k.startswith(('SDLOG_', 'UXRCE_DDS_', 'UAVCAN_PUB_RTCM', 'UAVCAN_ENABLE',
                                                'SER_TEL2_BAUD', 'GPS_'))},
                    'log_dropouts': len(ulog.dropouts),
                    'log_dropout_ms': int(sum(d.duration for d in ulog.dropouts))}
        build = os.path.join(run, 'meta', 'px4_msgs_build_info.txt')
        if os.path.exists(build):
            ref = dict(ln.strip().split('=', 1) for ln in open(build) if '=' in ln).get('PX4_FIRMWARE_REF', '')
            sw = info.get('ver_sw') or ''
            s['px4']['matches_px4_msgs_build'] = bool(ref and sw and (ref.startswith(sw) or sw.startswith(ref)))
            if not s['px4']['matches_px4_msgs_build']:
                warn(f'PX4 build {sw[:12]} is not the commit px4_msgs was generated from ({ref[:12]}).')
        if ulog.dropouts:
            warn(f'PX4 log has {len(ulog.dropouts)} write dropouts ({s["px4"]["log_dropout_ms"]} ms): '
                 'logged data is incomplete there.')

        clock = ClockMap(ulog, DDS_TIMESYNC if mode == 'xrce' else MAVLINK_TIMESYNC)
        insts = ulog_rtcm_instances(ulog)
        if not insts:
            warn('PX4 log has no rtcm_corrections: logger_topics.txt was not active '
                 '(px4_console.py install-logger-topics, then reboot).')
        s['px4']['rtcm_instances'] = {str(k): {'chunks': len(v), 'device_ids': sorted({d for _, d, _ in v}),
                                               'bytes': sum(len(p) for _, _, p in v)} for k, v in insts.items()}
        if mode == 'xrce':
            sel = [k for k, v in insts.items() if any(d == device_id for _, d, _ in v)]
        else:
            sel = sorted(insts, key=lambda k: -len(insts[k]))[:1]
        hop3_raw = insts[sel[0]] if sel else []
        s['px4']['instance_used'] = sel[0] if sel else None

        if not clock.valid:
            warn('No timesync_status for this link in the PX4 log: PX4 and bag times cannot be aligned; '
                 'latency and time-windowed checks are skipped.')
        hop3_px4_t = np.array([t for t, _, _ in hop3_raw])
        if clock.valid and len(hop3_px4_t):
            # Sanity: corrections must reach PX4 after leaving the companion, within a second.
            # Pick the offset sign that satisfies it, and say so if neither does.
            hop2_t = np.array([t for t, _ in hop2_chunks]) if hop2_chunks else np.array([t for t, _ in hop1_chunks])
            for sign in (1.0, -1.0):
                clock.sign = sign
                wall = clock.to_wall(hop3_px4_t)
                idx = np.searchsorted(hop2_t, wall) - 1
                ok = idx >= 0
                lag = wall[ok] - hop2_t[idx[ok]]
                if len(lag) and 0 <= np.median(lag) < 1.0:
                    break
            else:
                clock.sign = 1.0
                warn('Clock alignment between PX4 and the companion is implausible; latency is not reported.')
                clock.valid = False
            s['clock_offset_sign'] = clock.sign
        hop3_wall = clock.to_wall(hop3_px4_t) if clock.valid and len(hop3_px4_t) else hop3_px4_t
        hop3_chunks = [(float(t), p) for t, (_, _, p) in zip(hop3_wall, hop3_raw)]
        if clock.valid:
            lo, hi = window
            hop3_in = [(t, p) for t, p in hop3_chunks if lo - 1.0 <= t <= hi + 1.0]
        else:
            hop3_in = hop3_chunks
        hop3 = frames_from_chunks(hop3_in)
        s['hops']['3_px4_uorb'] = hop_summary(hop3, hop3_in)

        s['comparisons']['1_to_3_frames'] = compare(frame_items(hop1.frames), frame_items(hop3.frames),
                                                    window if clock.valid else None)
        if mode == 'xrce':
            c = compare(chunk_items(hop2_chunks), chunk_items(hop3_in), window if clock.valid else None)
            s['comparisons']['2_to_3_chunks'] = c
            if clock.valid and c['pairs']:
                lat = np.array([(b - a) * 1e3 for a, b in c['pairs']])
                s['latency_ms'] = {'n': int(len(lat)), 'p50': pct(lat, 50), 'p95': pct(lat, 95),
                                   'p99': pct(lat, 99), 'max': float(lat.max()), 'min': float(lat.min())}
                s['_latency'] = lat

        # Logger gaps: chunks the logger itself missed (not real losses)
        gaps = []
        for d in ulog_datasets(ulog, 'logger_status'):
            if 'message_gaps' in d.data and len(d.data['message_gaps']):
                g = d.data['message_gaps'].astype(np.int64)
                gaps.append(int(g.max() - g.min()))
        s['px4']['logger_message_gaps'] = sum(gaps) if gaps else None
        if gaps and sum(gaps):
            warn(f'PX4 logger missed {sum(gaps)} full-rate messages: a missing chunk at hop 3 may be a '
                 'logging gap rather than a real loss.')

        msgs = [(m.timestamp / 1e6, m.log_level_str(), m.message) for m in ulog.logged_messages]
        lost = [m for m in msgs if 'rtcm_corrections lost' in m[2]]
        s['px4']['rtcm_lost_warnings'] = len(lost)
        s['px4']['messages'] = [f'{t:.1f}s {lvl} {txt}' for t, lvl, txt in msgs
                                if re.search(r'rtcm|gnss|gps|uxrce|dds|uavcan|logger', txt, re.I)][:80]

        fix = fix_analysis(ulog, hop3_raw, clock, load_events(run), window)
        s['fix'] = fix['summary']
        s['_fix'] = fix

    s['verdicts'] = verdicts(s, criteria)
    return s


def fix_analysis(ulog, hop3_raw, clock, events, window):
    """Fix type per receiver sample; time to RTK fixed; drops; behavior in correction gaps."""
    res = {'summary': {'receivers': {}}, 'series': {}, 'events': []}
    corr_t = np.array([t for t, _, _ in hop3_raw])
    # Correction outages: gaps in what reached PX4
    gaps = []
    if len(corr_t) > 1:
        d = np.diff(corr_t)
        for i in np.nonzero(d > CORRECTION_GAP_S)[0]:
            gaps.append((corr_t[i], corr_t[i + 1]))
    starts = ([corr_t[0]] if len(corr_t) else []) + [b for _, b in gaps]
    res['summary']['correction_outages'] = [{'from_s': a, 'to_s': b, 'duration_s': b - a} for a, b in gaps]

    armed = []
    for d in ulog_datasets(ulog, 'vehicle_status'):
        st = d.data['arming_state']
        t = d.data['timestamp'] / 1e6
        on = None
        for ti, a in zip(t, st):
            if a == ARMING_STATE_ARMED and on is None:
                on = ti
            elif a != ARMING_STATE_ARMED and on is not None:
                armed.append((on, ti))
                on = None
        if on is not None:
            armed.append((on, t[-1]))
    res['summary']['armed_periods_s'] = armed

    def in_gap(t):
        return any(a <= t <= b for a, b in gaps) or (len(corr_t) and t < corr_t[0])

    for d in ulog_datasets(ulog, 'sensor_gnss'):
        t = d.data['timestamp'] / 1e6
        ft = d.data['fix_type'].astype(int)
        if not len(t):
            continue
        if clock.valid:
            wall = clock.to_wall(t)
            lo, hi = window
            sel = (wall >= lo - 5) & (wall <= hi + 5)
            t, ft = t[sel], ft[sel]
            sub = {k: d.data[k][sel] for k in ('eph', 'epv', 'satellites_used') if k in d.data}
        else:
            sub = {k: d.data[k] for k in ('eph', 'epv', 'satellites_used') if k in d.data}
        if not len(t):
            continue
        dt = np.diff(t)
        r = {'samples': int(len(t)), 'rate_hz': float(1 / np.median(dt)) if len(dt) else None,
             'max_sample_gap_s': float(dt.max()) if len(dt) else None,
             'fix_type_counts': {str(k): int(v) for k, v in zip(*np.unique(ft, return_counts=True))}}
        # Time to RTK fixed after each correction start (first, and after each outage)
        ttff = []
        for c0 in starts:
            before = ft[t <= c0]
            if len(before) and before[-1] >= FIX_RTK_FIXED:
                ttff.append({'corrections_from_s': float(c0), 'ttff_s': None, 'note': 'already RTK fixed'})
                continue
            after = np.nonzero((t >= c0) & (ft >= FIX_RTK_FIXED))[0]
            fl = np.nonzero((t >= c0) & (ft >= FIX_RTK_FLOAT))[0]
            ttff.append({'corrections_from_s': float(c0),
                         'ttff_s': float(t[after[0]] - c0) if len(after) else None,
                         'time_to_float_s': float(t[fl[0]] - c0) if len(fl) else None})
        r['time_to_rtk_fixed'] = ttff
        first6 = np.nonzero(ft >= FIX_RTK_FIXED)[0]
        if len(first6):
            k = first6[0]
            tail_t, tail = t[k:], ft[k:]
            r['fixed_fraction_after_first_fix'] = float(np.mean(tail >= FIX_RTK_FIXED))
            flowing = np.array([not in_gap(x) for x in tail_t])
            r['fixed_fraction_with_corrections_flowing'] = (float(np.mean(tail[flowing] >= FIX_RTK_FIXED))
                                                            if flowing.any() else None)
            # Drops below RTK fixed after the first fix, split by cause
            drops = []
            i = 0
            while i < len(tail):
                if tail[i] < FIX_RTK_FIXED:
                    j = i
                    while j < len(tail) and tail[j] < FIX_RTK_FIXED:
                        j += 1
                    end = tail_t[j] if j < len(tail) else tail_t[-1]
                    drops.append({'from_s': float(tail_t[i]), 'duration_s': float(end - tail_t[i]),
                                  'samples': int(j - i), 'lowest_fix': int(tail[i:j].min()),
                                  'during_correction_outage': bool(in_gap(tail_t[i]))})
                    i = j
                else:
                    i += 1
            r['drops_below_fixed'] = drops
            r['unexpected_drops'] = sum(1 for x in drops if not x['during_correction_outage'])
            if 'eph' in sub:
                fixed = tail >= FIX_RTK_FIXED
                r['eph_m_when_fixed'] = {'p50': pct(sub['eph'][k:][fixed], 50),
                                         'p95': pct(sub['eph'][k:][fixed], 95)}
        else:
            r['fixed_fraction_after_first_fix'] = 0.0
            r['unexpected_drops'] = None
        if 'satellites_used' in sub and len(sub['satellites_used']):
            r['satellites_used'] = {'min': int(sub['satellites_used'].min()),
                                    'p50': pct(sub['satellites_used'], 50)}
        res['summary']['receivers'][str(d.multi_id)] = r
        res['series'][d.multi_id] = (t, ft)
    for te, text in events:
        if clock.valid:
            res['events'].append((float(clock.to_px4(te)), text))
    res['corr_t'] = corr_t
    res['corr_bytes'] = [len(p) for _, _, p in hop3_raw]
    return res


def verdicts(s, c):
    """PASS / FAIL / INCOMPLETE against the pre-set criteria (criteria.json)."""
    out = []

    def add(name, ok, value, limit, incomplete=False, why=''):
        out.append({'check': name, 'result': 'INCOMPLETE' if incomplete else ('PASS' if ok else 'FAIL'),
                    'value': value, 'limit': limit, 'note': why})

    comp = s['comparisons']
    for key, label in (('0_to_1_frames', 'caster -> ntrip_client frames lost'),
                       ('1_to_2_frames', 'ntrip_client -> FCU link frames lost'),
                       ('2_to_3_chunks', 'DDS -> PX4 chunks lost'),
                       ('1_to_3_frames', 'end to end (ntrip_client -> PX4) frames lost')):
        if key in comp:
            v = comp[key]['missing']
            gaps = (s.get('px4') or {}).get('logger_message_gaps')
            incomplete = key.endswith('3_chunks') or key.endswith('3_frames')
            add(label, v <= c['max_lost'], v, c['max_lost'],
                incomplete=bool(incomplete and v and gaps),
                why='PX4 logger missed messages in this run' if incomplete and v and gaps else '')
        elif key == '0_to_1_frames':
            add(label, False, None, c['max_lost'], incomplete=True, why='no caster tap')
    for hop, h in s['hops'].items():
        if h['crc_failures'] or (h['garbage_bytes'] and hop != '0_caster'):
            add(f'hop {hop}: corrupted data', False, h['crc_failures'] + h['garbage_bytes'], 0)
    if 'px4' in s:
        add('PX4 "rtcm_corrections lost" warnings', s['px4']['rtcm_lost_warnings'] <= c['max_lost'],
            s['px4']['rtcm_lost_warnings'], c['max_lost'])
        if s['px4'].get('logger_message_gaps') is not None:
            add('PX4 logger gaps (evidence completeness)', s['px4']['logger_message_gaps'] == 0,
                s['px4']['logger_message_gaps'], 0)
    if 'latency_ms' in s:
        add('DDS hop latency p99 (ms)', s['latency_ms']['p99'] <= c['latency_p99_ms_max'],
            round(s['latency_ms']['p99'], 1), c['latency_p99_ms_max'])
    fix = s.get('fix') or {}
    for rid, r in (fix.get('receivers') or {}).items():
        firsts = [x for x in r['time_to_rtk_fixed'] if x.get('note') != 'already RTK fixed']
        if firsts:
            v = firsts[0]['ttff_s']
            add(f'receiver {rid}: time to RTK fixed (s)', v is not None and v <= c['ttff_s_max'],
                None if v is None else round(v, 1), c['ttff_s_max'])
        f = r.get('fixed_fraction_with_corrections_flowing')
        if f is not None:
            add(f'receiver {rid}: RTK fixed share with corrections flowing', f >= c['fixed_fraction_min'],
                round(f, 4), c['fixed_fraction_min'])
        if r.get('unexpected_drops') is not None:
            add(f'receiver {rid}: drops below RTK fixed with corrections flowing',
                r['unexpected_drops'] <= c['max_unexpected_drops'], r['unexpected_drops'],
                c['max_unexpected_drops'])
    if not s.get('ulog'):
        add('PX4 side verified', False, None, None, incomplete=True, why='no PX4 log')
    return out


# ---------------------------------------------------------------- report

def charts(s, out_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    surface, ink, ink2, grid = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
    series = ['#2a78d6', '#eb6834']
    plt.rcParams.update({'figure.facecolor': surface, 'axes.facecolor': surface, 'axes.edgecolor': grid,
                         'axes.labelcolor': ink2, 'xtick.color': ink2, 'ytick.color': ink2, 'text.color': ink,
                         'axes.grid': True, 'grid.color': grid, 'grid.linewidth': 0.8, 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False, 'legend.frameon': False})
    files = []
    fix = s.get('_fix')
    if fix and fix['series']:
        t0 = min(t[0] for t, _ in fix['series'].values())
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True,
                                       gridspec_kw={'height_ratios': [3, 2]})
        for i, (mid, (t, ft)) in enumerate(sorted(fix['series'].items())):
            ax1.step(t - t0, ft, where='post', lw=2, color=series[i % 2], label=f'sensor_gnss {mid}')
        ax1.set_yticks([0, 3, 4, 5, 6])
        ax1.set_yticklabels(['none', '3D', 'DGPS', 'RTK float', 'RTK fixed'])
        ax1.set_ylim(-0.3, 6.6)
        ax1.set_title('Receiver fix type, every sample', loc='left', color=ink, fontsize=11)
        if len(fix['series']) > 1:
            ax1.legend(loc='lower right')
        if len(fix['corr_t']):
            ct = np.asarray(fix['corr_t'])
            sec = np.floor(ct - t0).astype(int)
            bps = np.bincount(sec - sec.min(), weights=fix['corr_bytes'])
            ax2.bar(np.arange(len(bps)) + sec.min(), bps, width=0.8, color=series[0])
        ax2.set_title('Corrections reaching PX4 (bytes per second)', loc='left', color=ink, fontsize=11)
        ax2.set_xlabel('seconds since the first receiver sample in the run')
        for o in fix['summary']['correction_outages']:
            for ax in (ax1, ax2):
                ax.axvspan(o['from_s'] - t0, o['to_s'] - t0, color=grid, alpha=0.6, lw=0)
        for te, text in fix['events']:
            for ax in (ax1, ax2):
                ax.axvline(te - t0, color=ink2, lw=1, ls=':')
            ax1.annotate(text, (te - t0, 6.45), fontsize=8, color=ink2, rotation=90, va='top', ha='right')
        fig.tight_layout()
        path = os.path.join(out_dir, 'fix_timeline.png')
        fig.savefig(path, dpi=130)
        plt.close(fig)
        files.append(('Fix type and corrections over the run (grey: correction outages)', path))
    if s.get('_latency') is not None and len(s['_latency']):
        lat = s['_latency']
        fig, ax = plt.subplots(figsize=(10, 3.2))
        hi = max(np.percentile(lat, 99.5), 1)
        ax.hist(np.clip(lat, None, hi), bins=60, color=series[0], edgecolor=surface, linewidth=1)
        for q, name in ((50, 'p50'), (99, 'p99')):
            v = np.percentile(lat, q)
            ax.axvline(v, color=ink2, lw=1, ls='--')
            ax.annotate(f'{name} {v:.1f} ms', (v, ax.get_ylim()[1] * 0.92), fontsize=9, color=ink2,
                        xytext=(4, 0), textcoords='offset points')
        ax.set_title('Chunk latency, companion -> PX4 (DDS hop)', loc='left', color=ink, fontsize=11)
        ax.set_xlabel('ms')
        ax.set_ylabel('chunks')
        fig.tight_layout()
        path = os.path.join(out_dir, 'latency.png')
        fig.savefig(path, dpi=130)
        plt.close(fig)
        files.append(('Latency of each chunk from the companion to PX4', path))
    return files


def markdown(s, chart_files):
    L = []
    w = L.append
    w(f'## RTK corrections test run `{s["run"]}` ({s["mode"].upper()})\n')
    w(f'- Label: {s["label"]}; vehicle namespace `{s["namespace"]}`; {s["start_utc"]} to {s["stop_utc"]}')
    px4 = s.get('px4') or {}
    if px4:
        w(f'- PX4 `{px4.get("ver_sw")}` ({px4.get("ver_sw_branch")}) on {px4.get("ver_hw")}; '
          f'matches px4_msgs build: {px4.get("matches_px4_msgs_build", "unknown")}')
    w('')
    w('### Verdicts\n')
    w('| Check | Result | Value | Limit | Note |\n|---|---|---|---|---|')
    for v in s['verdicts']:
        w(f'| {v["check"]} | **{v["result"]}** | {v["value"]} | {v["limit"]} | {v["note"]} |')
    if s['warnings']:
        w('\n### Warnings\n')
        for x in s['warnings']:
            w(f'- {x}')
    w('\n### Stream at each hop\n')
    w('| Hop | Chunks | Bytes | RTCM frames | CRC failures | Garbage bytes | Largest frame | Largest chunk |')
    w('|---|---|---|---|---|---|---|---|')
    for hop, h in s['hops'].items():
        w(f'| {hop} | {h["chunks"]} | {h["bytes"]} | {h["frames"]} | {h["crc_failures"]} | '
          f'{h["garbage_bytes"]} | {h["largest_frame"]} | {h["largest_chunk"]} |')
    first = next(iter(s['hops'].values()), None)
    if first:
        w('\nRTCM message types (hop 1): ' + ', '.join(
            f'{k}: {v}' for k, v in s['hops'].get('1_ntrip_client', first)['frame_types'].items()))
        w(f'\nFrames over 300 bytes (split by the bridge): {s["frames_over_300"]}; '
          f'over 720 bytes (dropped by MAVROS): {s["frames_over_720"]}')
    w('\n### Comparisons\n')
    w('| From -> to | Items from | Items to | Matched | Lost | Extra | Edge (ignored) |\n|---|---|---|---|---|---|---|')
    for k, c in s['comparisons'].items():
        w(f'| {k} | {c["a_count"]} | {c["b_count"]} | {c["matched"]} | {c["missing"]} | {c["extra"]} | '
          f'{c["edge_missing"]} / {c["edge_extra"]} |')
    for k, c in s['comparisons'].items():
        if c['missing_examples']:
            w(f'\nLost in {k} (first {len(c["missing_examples"])}): ' +
              '; '.join(f'{iso(x["t"])} {x}' for x in c['missing_examples'][:10]))
    if 'latency_ms' in s:
        lm = s['latency_ms']
        w(f'\n### Latency, companion -> PX4\n\n{lm["n"]} chunks: p50 {lm["p50"]:.1f} ms, p95 {lm["p95"]:.1f} ms, '
          f'p99 {lm["p99"]:.1f} ms, max {lm["max"]:.1f} ms (PX4 times mapped to the companion clock with '
          'timesync_status)')
    fix = s.get('fix')
    if fix:
        w('\n### Receiver\n')
        for rid, r in fix['receivers'].items():
            w(f'**sensor_gnss {rid}**: {r["samples"]} samples at {r["rate_hz"] or 0:.1f} Hz '
              f'(largest gap {r["max_sample_gap_s"] or 0:.2f} s); fix types {r["fix_type_counts"]}')
            for x in r['time_to_rtk_fixed']:
                w(f'- corrections from {x["corrections_from_s"]:.1f} s: '
                  + (x['note'] if x.get('note') else
                     f'RTK float after {x["time_to_float_s"]}, RTK fixed after {x["ttff_s"]} s'))
            if 'fixed_fraction_after_first_fix' in r:
                w(f'- RTK fixed share after first fix: {r["fixed_fraction_after_first_fix"]:.4f}; '
                  f'with corrections flowing: {r.get("fixed_fraction_with_corrections_flowing")}')
            drops = r.get('drops_below_fixed') or []
            w(f'- drops below RTK fixed: {len(drops)} ({r.get("unexpected_drops")} with corrections flowing)')
            for x in drops[:20]:
                w(f'  - at {x["from_s"]:.1f} s for {x["duration_s"]:.2f} s ({x["samples"]} samples), lowest '
                  f'{x["lowest_fix"]}{" during a correction outage" if x["during_correction_outage"] else ""}')
        if fix['correction_outages']:
            w('\nCorrection outages (no chunk reached PX4 for > %.0f s): ' % CORRECTION_GAP_S + '; '.join(
                f'{o["from_s"]:.1f} s for {o["duration_s"]:.1f} s' for o in fix['correction_outages']))
    if px4:
        w('\n### PX4\n')
        w(f'- rtcm_corrections instances: {px4.get("rtcm_instances")}; used: {px4.get("instance_used")}')
        w(f'- "rtcm_corrections lost" warnings: {px4.get("rtcm_lost_warnings")}; logger message gaps: '
          f'{px4.get("logger_message_gaps")}; log write dropouts: {px4.get("log_dropouts")}')
        w(f'- parameters: `{json.dumps(px4.get("params"))}`')
        if px4.get('messages'):
            w('\n<details><summary>PX4 messages (GNSS, DDS, logger)</summary>\n\n```')
            L.extend(px4['messages'])
            w('```\n</details>')
    if s.get('bridge_log'):
        w(f'\nBridge counters (from its log): {s["bridge_log"]}')
    if s.get('ntrip_client_warnings'):
        w('\nntrip_client warnings: ' + '; '.join(s['ntrip_client_warnings'][:10]))
    for title, path in chart_files:
        w(f'\n**{title}**\n\n![{title}]({os.path.basename(path)})')
    return '\n'.join(L) + '\n'


def html_report(md, chart_files, title):
    body = []
    in_table = False
    for line in md.split('\n'):
        if line.startswith('|') and set(line.replace('|', '').strip()) <= set('-'):
            continue
        if line.startswith('|'):
            cells = [html.escape(c.strip()).replace('**', '') for c in line.strip('|').split('|')]
            tag = 'th' if not in_table else 'td'
            if not in_table:
                body.append('<table>')
                in_table = True
            body.append('<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>')
            continue
        if in_table:
            body.append('</table>')
            in_table = False
        m = re.match(r'!\[(.*)\]\((.*)\)', line)
        if m:
            path = next((p for _, p in chart_files if os.path.basename(p) == m.group(2)), None)
            if path:
                b64 = base64.b64encode(open(path, 'rb').read()).decode()
                body.append(f'<img alt="{html.escape(m.group(1))}" src="data:image/png;base64,{b64}">')
            continue
        if line.startswith('## '):
            body.append(f'<h1>{html.escape(line[3:])}</h1>')
        elif line.startswith('### '):
            body.append(f'<h2>{html.escape(line[4:])}</h2>')
        elif line.strip():
            body.append(f'<p>{html.escape(line).replace("`", "")}</p>')
    if in_table:
        body.append('</table>')
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#3a3936}}}}
:root[data-theme="dark"]{{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#3a3936}}
body{{background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px}}
table{{border-collapse:collapse;margin:8px 0;display:block;overflow-x:auto}}th,td{{border-bottom:1px solid var(--line);padding:4px 10px;text-align:left}}
th{{color:var(--ink2);font-weight:600}}p{{margin:4px 0}}img{{max-width:100%;background:#fcfcfb;border-radius:4px}}h2{{margin-top:24px}}
</style></head><body>{"".join(body)}</body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run')
    p.add_argument('--ulog')
    p.add_argument('--tap', help='caster_tap.py or replay_caster.py output directory')
    p.add_argument('--device-id', type=int, default=1, help="rtcm_xrce_bridge's device_id")
    p.add_argument('--criteria', default=os.path.join(HERE, 'criteria.json'))
    a = p.parse_args()
    criteria = json.load(open(a.criteria))
    s = analyze(a.run, a.ulog, a.tap, a.device_id, criteria)
    out = os.path.join(a.run, 'analysis')
    os.makedirs(out, exist_ok=True)
    files = charts(s, out)
    md = markdown(s, files)
    open(os.path.join(out, 'report.md'), 'w').write(md)
    open(os.path.join(out, 'report.html'), 'w').write(html_report(md, files, f'RTK run {s["run"]}'))
    for c in s['comparisons'].values():
        c.pop('pairs', None)
    clean = {k: v for k, v in s.items() if not k.startswith('_')}
    json.dump(clean, open(os.path.join(out, 'summary.json'), 'w'), indent=2, default=float)
    fails = [v for v in s['verdicts'] if v['result'] != 'PASS']
    print(md)
    print(f'Wrote {out}/report.md, report.html, summary.json')
    return 1 if any(v['result'] == 'FAIL' for v in fails) else 0


if __name__ == '__main__':
    sys.exit(main())
