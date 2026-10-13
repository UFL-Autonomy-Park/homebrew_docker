#!/bin/bash
# Make the Jetson's logs survive reboots and power loss, and capture core dumps.
#
#   sudo bash scripts/setup-persistent-logs.sh && sudo reboot
#
# Host-level settings that a container cannot make for itself:
#   0. A unique machine ID. Jetsons flashed from the same image share one,
#      and the journal is stored under /var/log/journal/<machine-id>.
#   1. journald stores the system journal on disk instead of in RAM. With
#      docker-compose.yml's journald logging driver, the container's output
#      lands there too, next to kernel messages (USB, throttling, segfaults).
#   2. systemd-coredump collects core dumps (including from processes inside
#      the container) instead of apport, which ignores them.
# Safe to re-run.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Run with sudo: sudo $0${NC}"
    exit 1
fi

echo -e "${GREEN}=== Persistent logs and core dumps ===${NC}"

# Machine ID baked into the Jetson image the homebrews were cloned from.
readonly CLONED_IMAGE_MACHINE_ID="5dbfb12414a3456d9014d88183e338b1"
REBOOT_NEEDED=false

echo -e "${YELLOW}[1/3] machine ID: unique per Jetson...${NC}"
if [ "$(cat /etc/machine-id)" = "$CLONED_IMAGE_MACHINE_ID" ]; then
    # systemd-machine-id-setup copies the ID back from /var/lib/dbus/machine-id
    # if that file exists, so both must go before regenerating.
    rm -f /etc/machine-id /var/lib/dbus/machine-id
    systemd-machine-id-setup
    REBOOT_NEEDED=true
    echo "  New machine ID: $(cat /etc/machine-id)"
else
    echo "  Already unique: $(cat /etc/machine-id)"
fi
# Keep D-Bus on the same ID instead of a stale copy.
if [ "$(readlink /var/lib/dbus/machine-id)" != "/etc/machine-id" ]; then
    ln -sf /etc/machine-id /var/lib/dbus/machine-id
fi

echo -e "${YELLOW}[2/3] journald: persistent storage, capped at 2 GB...${NC}"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/50-homebrew-persistent.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=2G
EOF
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
# journald keeps writing to RAM (/run/log/journal) until told to flush; at
# boot systemd-journal-flush.service does this, so do it here too.
journalctl --flush

echo -e "${YELLOW}[3/3] core dumps: systemd-coredump instead of apport...${NC}"
apt-get install -y systemd-coredump
# apport rewrites kernel.core_pattern at boot; disable it so the coredump
# handler stays in place.
if [ -f /etc/default/apport ]; then
    sed -i 's/^enabled=1/enabled=0/' /etc/default/apport
fi
systemctl disable --now apport.service 2>/dev/null || true
sysctl -p /usr/lib/sysctl.d/50-coredump.conf

echo ""
if [ "$REBOOT_NEEDED" = true ]; then
    echo "The machine ID changed. Journals from before the change stay readable with:"
    echo "  journalctl -D /var/log/journal/$CLONED_IMAGE_MACHINE_ID"
    echo ""
fi
echo -e "${RED}Done. Reboot now: sudo reboot${NC}"
echo "Then check (README 3.2):"
echo "  cat /etc/machine-id                    # not $CLONED_IMAGE_MACHINE_ID"
echo "  journalctl --list-boots | tail -2      # grows by one entry per boot"
echo "  cat /proc/sys/kernel/core_pattern      # |/lib/systemd/systemd-coredump ..."
