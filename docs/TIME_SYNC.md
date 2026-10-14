# Time Sync

Every computer that runs ROS 2 nodes for the lab (the homebrew Jetsons, ground
station PCs and the discovery server) runs **chrony**. The discovery server
(192.168.1.201) is the lab's time server: it syncs to the internet and serves
time to the lab network. Everything else syncs to it.

Why:

- **Message ages are computed across machines.** MAVROS stamps PX4 data with
  the Jetson's clock; a node on a ground PC compares that stamp with its own
  clock. Any offset between the two clocks goes straight into the computed
  age.
- **One shared reference.** When every machine syncs to the same server, their
  clocks agree with each other even if the internet is slow or down
  (`local stratum 10` below keeps the server answering without internet).
- **No clock jumps after boot.** chrony steps the clock only in its first
  updates after starting (`makestep 1 3` in Ubuntu's default config: steps
  larger than 1 s, only in the first three updates) and slews it gradually
  afterwards. A jump while MAVROS runs makes it reset its time sync.
  After a battery pull a Jetson boots with its clock at 1970 (homebrew05,
  2026-10-14: chrony stepped it forward 56.8 years, 22.6 s after boot), so
  the homebrew container's entrypoint waits until the clock agrees with the
  time server before starting ROS.
- Ubuntu's server documentation recommends chrony for serving NTP;
  `systemd-timesyncd`, the default client, implements SNTP only and cannot
  serve time.

Containers need nothing: a container cannot have its own wall clock (Linux
time namespaces do not virtualize `CLOCK_REALTIME`, see
`man time_namespaces`), so the homebrew container uses the Jetson's clock.

> [!NOTE]
> On Ubuntu, `chrony` and `systemd-timesyncd` conflict: installing one removes
> the other. All config changes below are new files in `/etc/chrony/conf.d/`
> (Ubuntu's `chrony.conf` includes that directory with
> `confdir /etc/chrony/conf.d`); the packaged `chrony.conf` is never edited.

## 1. Discovery server (the time server)

```bash
head -2 /etc/os-release
sudo ufw status
sudo apt-get update && sudo apt-get install -y chrony
grep -n confdir /etc/chrony/chrony.conf
```

`grep` must print `confdir /etc/chrony/conf.d`. If it prints nothing, stop:
the files below would be ignored.

```bash
printf 'allow 192.168.1.0/24\nlocal stratum 10\n' | sudo tee /etc/chrony/conf.d/lan-server.conf
sudo systemctl restart chrony
```

- `allow 192.168.1.0/24`: serve time to the lab subnet.
- `local stratum 10`: keep serving time if the internet is lost. chrony's
  docs say the stratum "should be larger than the maximum expected stratum in
  the network when external NTP servers are accessible"; internet sources are
  stratum 1–3.

Only if `ufw status` said `active`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 123 proto udp
```

Check, about a minute after the restart:

```bash
chronyc sources
chronyc tracking
sudo ss -ulpn | grep ':123 '
```

- `chronyc sources`: one line starts with `^*` (the selected internet source).
- `chronyc tracking`: `Stratum 2` (or 3) and a `Reference ID` naming an
  internet server, `Leap status : Normal`. `Reference ID 7F7F0101` with
  `Stratum 10` means the server is on its local clock: normal for a few
  seconds after a restart, and also what it does when the internet is down.
- `ss`: `chronyd` listening on UDP port 123.

## 2. Homebrew Jetsons

Part of the first-time setup: README step 1.8. The commands are the same as
for a ground PC (section 3).

## 3. Ground PCs (and any other computer running ROS 2 nodes)

```bash
sudo apt-get install -y chrony
printf 'server 192.168.1.201 iburst prefer\n' | sudo tee /etc/chrony/conf.d/lab-time-server.conf
sudo systemctl restart chrony
sleep 30
chronyc sources
```

- `iburst`: a burst of requests at startup, so the first sync takes seconds,
  not minutes.
- `prefer`: use the lab server over the internet pools from Ubuntu's default
  config. The pools stay configured as a fallback if the lab server is
  unreachable (e.g. a ground PC away from the lab network).

The `192.168.1.201` line must start with `^*`. The internet pools are listed
below it with `^-` (usable, not selected) or `^?` (not reachable yet); both are
normal. Then:

```bash
chronyc tracking | grep -E "Reference ID|Stratum|System time"
```

Expect `Reference ID : C0A801C9 (192.168.1.201)` and `Stratum 3` (one more
than the server). `System time` is this machine's current estimated offset
from the server.

Clocks a few tens of milliseconds off when chrony is installed are corrected
gradually, not stepped: on 2026-10-14 homebrew05 started 24 ms behind the
server and was within 0.2 ms about a minute later, with no step in the chrony
log (`journalctl -u chrony`), while its container kept running.

## 4. Undo

**A Jetson or ground PC** (back to Ubuntu's default `systemd-timesyncd`,
which removes chrony):

```bash
sudo rm -f /etc/chrony/conf.d/lab-time-server.conf
sudo apt-get install -y systemd-timesyncd
timedatectl timesync-status
```

`timesync-status` should show a `Server:` line within a minute.

**The discovery server:**

```bash
sudo rm -f /etc/chrony/conf.d/lan-server.conf
sudo ufw status numbered
```

If `ufw status numbered` lists a rule for port 123, delete it by its number
(`sudo ufw delete <number>`). Then:

```bash
sudo apt-get install -y systemd-timesyncd
timedatectl timesync-status
```

Undo the server **last**: clients still pointing at it fall back to the
internet pools (`prefer` keeps the pools as a fallback), but their clocks no
longer share one reference.
