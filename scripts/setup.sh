#!/bin/bash
# RipForge Setup Script

set -e

echo "=== RipForge Setup ==="
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Please run without sudo. The script will prompt for sudo when needed."
    exit 1
fi

# Install MakeMKV
echo "Installing MakeMKV..."
if ! command -v makemkvcon &> /dev/null; then
    sudo add-apt-repository -y ppa:heyarje/makemkv-beta
    sudo apt update
    sudo apt install -y makemkv-bin makemkv-oss
else
    echo "  MakeMKV already installed"
fi

# Add user to cdrom group
echo "Adding $USER to cdrom group..."
if groups $USER | grep -q cdrom; then
    echo "  Already in cdrom group"
else
    sudo usermod -aG cdrom $USER
    echo "  Added to cdrom group (logout/login required)"
fi

# Create virtual environment
echo "Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt
echo "  Dependencies installed"

# Create config directory
mkdir -p config

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To run RipForge:"
echo "  source venv/bin/activate"
echo "  python run.py"
echo ""
echo "To install as a service:"
echo "  sudo cp ripforge.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now ripforge"
echo ""
