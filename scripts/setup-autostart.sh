#!/bin/bash

# Setup script for MAVROS Docker auto-start on boot

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the absolute path to the docker-compose directory (parent of scripts dir)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
COMPOSE_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

echo -e "${GREEN}=== MAVROS Docker Auto-Start Setup ===${NC}"
echo "Docker Compose directory: $COMPOSE_DIR"
echo ""

# Check if docker-compose.yml exists
if [ ! -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    echo -e "${RED}Error: docker-compose.yml not found in $COMPOSE_DIR${NC}"
    exit 1
fi

# Detect docker compose command (v1 vs v2)
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
    echo -e "${GREEN}Found: docker-compose (v1)${NC}"
elif docker compose version &> /dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
    echo -e "${GREEN}Found: docker compose (v2)${NC}"
else
    echo -e "${RED}Error: Neither 'docker-compose' nor 'docker compose' command found${NC}"
    exit 1
fi

# Service file content
SERVICE_FILE="/etc/systemd/system/homebrew-docker.service"

echo ""
echo -e "${YELLOW}Creating systemd service file...${NC}"

# Create the service file
sudo tee $SERVICE_FILE > /dev/null <<EOF
[Unit]
Description=Homebrew Docker Compose
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$COMPOSE_DIR
ExecStart=/usr/bin/$DOCKER_COMPOSE_CMD up -d
ExecStop=/usr/bin/$DOCKER_COMPOSE_CMD down
TimeoutStartSec=0
User=$USER

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}Service file created at $SERVICE_FILE${NC}"

# Reload systemd
echo ""
echo -e "${YELLOW}Reloading systemd daemon...${NC}"
sudo systemctl daemon-reload

# Enable the service
echo -e "${YELLOW}Enabling service to start on boot...${NC}"
sudo systemctl enable homebrew-docker.service

echo ""
echo -e "${GREEN}=== Setup Complete! ===${NC}"
echo ""
echo "The service is now enabled and will start on boot."
echo ""
echo "Useful commands:"
echo "  Start now:        sudo systemctl start homebrew-docker.service"
echo "  Stop:             sudo systemctl stop homebrew-docker.service"
echo "  Restart:          sudo systemctl restart homebrew-docker.service"
echo "  Check status:     sudo systemctl status homebrew-docker.service"
echo "  View logs:        sudo journalctl -u homebrew-docker.service -f"
echo "  Disable autoboot: sudo systemctl disable homebrew-docker.service"
echo ""
echo -e "${YELLOW}Would you like to start the service now? (y/n)${NC}"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo -e "${YELLOW}Starting service...${NC}"
    sudo systemctl start homebrew-docker.service
    sleep 2
    sudo systemctl status homebrew-docker.service
fi
