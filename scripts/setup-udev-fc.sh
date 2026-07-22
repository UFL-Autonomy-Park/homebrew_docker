#!/bin/bash

# udev rule setup for Flight Controller USB device (FTDI)
# This creates a persistent symlink so the device is always at /dev/ttyFC

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run this script as root or use sudo.${NC}"
  exit 1
fi

echo -e "${GREEN}=== Flight Controller udev Rule Setup ===${NC}"
echo ""

# FTDI device info
VENDOR_ID="0403"
PRODUCT_ID="6001"
SERIAL_NUM="B0040P4E"
SYMLINK_NAME="ttyFC"

UDEV_RULES_FILE="/etc/udev/rules.d/99-flight-controller.rules"

echo "Device Info:"
echo "  Vendor ID:  $VENDOR_ID"
echo "  Product ID: $PRODUCT_ID"
echo "  Serial:     $SERIAL_NUM"
echo "  Symlink:    /dev/$SYMLINK_NAME"
echo ""

# Create the udev rule with serial number
echo -e "${YELLOW}Creating udev rule...${NC}"
cat <<EOF > $UDEV_RULES_FILE
# Flight Controller USB Serial Device (FTDI)
# Using serial number to ensure we match the exact device
SUBSYSTEM=="tty", ATTRS{idVendor}=="$VENDOR_ID", ATTRS{idProduct}=="$PRODUCT_ID", ATTRS{serial}=="$SERIAL_NUM", MODE="0666", SYMLINK+="$SYMLINK_NAME"
EOF

echo -e "${GREEN}udev rule created: $UDEV_RULES_FILE${NC}"
cat $UDEV_RULES_FILE

# Reload udev rules
echo ""
echo -e "${YELLOW}Reloading udev rules...${NC}"
udevadm control --reload-rules
udevadm trigger
udevadm settle

echo ""
echo -e "${GREEN}=== Setup Complete! ===${NC}"
echo ""
echo "Your flight controller will now be available at: /dev/$SYMLINK_NAME"
echo ""
echo -e "${YELLOW}Testing...${NC}"
if [ -e "/dev/$SYMLINK_NAME" ]; then
    echo -e "${GREEN}✓ Symlink /dev/$SYMLINK_NAME exists!${NC}"
    ls -l /dev/$SYMLINK_NAME
else
    echo -e "${YELLOW}Symlink not found yet. Please unplug and replug your device.${NC}"
fi

echo ""
echo "Update your .env file to use:"
echo "  FCU_URL=/dev/$SYMLINK_NAME:921600"
