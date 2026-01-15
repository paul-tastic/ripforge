#!/bin/bash
# RipForge udev setup script
# Run with sudo to install disc detection rules

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RIPFORGE_DIR="$(dirname "$SCRIPT_DIR")"

echo "Setting up RipForge disc detection..."

# Check for MakeMKV
if ! command -v makemkvcon &> /dev/null; then
    echo "ERROR: MakeMKV not found. Install it first:"
    echo "  sudo add-apt-repository ppa:heyarje/makemkv-beta"
    echo "  sudo apt-get update"
    echo "  sudo apt-get install makemkv-bin makemkv-oss"
    exit 1
fi
echo "  MakeMKV found: $(which makemkvcon)"

# Make disc-inserted script executable
chmod +x "$SCRIPT_DIR/disc-inserted.sh"
echo "  Made disc-inserted.sh executable"

# Update udev rules with correct path
sed "s|/home/paul/ripforge|$RIPFORGE_DIR|g" "$SCRIPT_DIR/99-ripforge.rules" > /tmp/99-ripforge.rules
sudo cp /tmp/99-ripforge.rules /etc/udev/rules.d/
echo "  Installed udev rules to /etc/udev/rules.d/"

# Reload udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "  Reloaded udev rules"

# Create log directory
mkdir -p "$RIPFORGE_DIR/logs"
echo "  Created log directory"

# Check user is in cdrom group
if ! groups | grep -q cdrom; then
    echo ""
    echo "WARNING: Current user is not in the cdrom group."
    echo "  Run: sudo usermod -aG cdrom \$USER"
    echo "  Then log out and back in."
fi

echo ""
echo "Done! Disc detection is now active."
echo "When you insert a disc, RipForge will automatically start ripping."
echo ""
echo "To test, insert a disc and check:"
echo "  tail -f $RIPFORGE_DIR/logs/disc-detect.log"
