#!/bin/bash
set -e

# Home OS — Cloudflare Tunnel Setup
# Run after install.sh to enable remote access

echo "============================================"
echo "  Home OS — Cloudflare Tunnel Setup"
echo "============================================"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (sudo bash setup-tunnel.sh)"
    exit 1
fi

# Install cloudflared
if ! command -v cloudflared &>/dev/null; then
    echo "[1/4] Installing cloudflared..."
    ARCH=$(dpkg --print-architecture)
    curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb" -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
else
    echo "[1/4] cloudflared already installed."
fi

# Authenticate
echo ""
echo "[2/4] Authenticating with Cloudflare..."
echo "A browser window will open. Log in and authorize the tunnel."
echo ""
cloudflared tunnel login

# Create tunnel
echo ""
echo "[3/4] Creating tunnel..."
read -p "Enter a name for your tunnel (e.g., home-os): " TUNNEL_NAME
TUNNEL_NAME="${TUNNEL_NAME:-home-os}"

cloudflared tunnel create "$TUNNEL_NAME"
TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')

echo "Tunnel ID: $TUNNEL_ID"

# Configure
read -p "Enter your domain (e.g., home.yourdomain.com): " DOMAIN

cat > /etc/cloudflared/config.yml << EOF
tunnel: $TUNNEL_ID
credentials-file: /root/.cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: $DOMAIN
    service: https://localhost:443
    originRequest:
      noTLSVerify: true
  - service: http_status:404
EOF

# Route DNS
echo ""
echo "[4/4] Routing DNS..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN"

# Install as service
cloudflared service install
systemctl enable cloudflared
systemctl start cloudflared

echo ""
echo "============================================"
echo "  Tunnel configured!"
echo "============================================"
echo ""
echo "  Your Home OS is now accessible at:"
echo "  https://$DOMAIN"
echo ""
echo "  On iPhone:"
echo "  1. Open https://$DOMAIN in Safari"
echo "  2. Tap Share → Add to Home Screen"
echo ""
echo "  Commands:"
echo "    systemctl status cloudflared"
echo "    cloudflared tunnel list"
echo ""
