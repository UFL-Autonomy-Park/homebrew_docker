#!/bin/bash
# Make the Jetson's logs survive reboots and power loss, and capture core dumps.
#
#   sudo ./scripts/setup-persistent-logs.sh
#
# Host-level settings that a container cannot make for itself:
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

echo -e "${YELLOW}[1/2] journald: persistent storage, capped at 2 GB...${NC}"
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

echo -e "${YELLOW}[2/2] core dumps: systemd-coredump instead of apport...${NC}"
apt-get install -y systemd-coredump
# apport rewrites kernel.core_pattern at boot; disable it so the coredump
# handler stays in place.
if [ -f /etc/default/apport ]; then
    sed -i 's/^enabled=1/enabled=0/' /etc/default/apport
fi
systemctl disable --now apport.service 2>/dev/null || true
sysctl -p /usr/lib/sysctl.d/50-coredump.conf

echo ""
echo -e "${GREEN}Done. Check:${NC}"
echo "  ls /var/log/journal                    # a machine-id directory exists"
echo "  journalctl --list-boots                # grows by one entry per boot"
echo "  cat /proc/sys/kernel/core_pattern      # |/lib/systemd/systemd-coredump ..."
echo "  coredumpctl list                       # crashes, once there are any"
echo ""
echo "Then recreate the container so the logging changes in docker-compose.yml"
echo "take effect: docker compose up -d"
