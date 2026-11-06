#!/bin/bash
# TV Libre : installation du relais au premier démarrage (lancé une fois par cloud-init)
BOOT=/boot/firmware/tvlibre
LOG=/var/log/tvlibre-setup.log
STATUS=$BOOT/etat-installation.txt
exec >>"$LOG" 2>&1
say() { echo "$(date '+%F %T') $*"; echo "$(date '+%F %T') $*" >> "$STATUS"; }
USER_NAME=$(getent passwd 1000 | cut -d: -f1)
export DEBIAN_FRONTEND=noninteractive
say "Début de l'installation"

# 1. Réseau
for i in $(seq 1 90); do curl -fsS -m 5 -o /dev/null https://deb.debian.org && break; sleep 5; done
say "Réseau disponible"

# 2. Paquets
apt-get update
apt-get install -y ffmpeg python3 curl ca-certificates gnupg avahi-daemon
apt-get install -y rpi-connect-lite || true
say "ffmpeg installé : $(ffmpeg -version | head -1)"

# 3. NordVPN (dépôt officiel, repli sur le script d'installation officiel)
if ! command -v nordvpn >/dev/null; then
  curl -fsSL https://repo.nordvpn.com/gpg/nordvpn_public.asc | gpg --dearmor -o /usr/share/keyrings/nordvpn.gpg
  echo "deb [signed-by=/usr/share/keyrings/nordvpn.gpg] https://repo.nordvpn.com/deb/nordvpn/debian stable main" \
    > /etc/apt/sources.list.d/nordvpn.list
  apt-get update && apt-get install -y nordvpn || \
    (curl -fsSL https://downloads.nordcdn.com/apps/linux/install.sh -o /tmp/nordinstall.sh && yes | sh /tmp/nordinstall.sh)
fi
[ -n "$USER_NAME" ] && usermod -aG nordvpn "$USER_NAME"
systemctl enable --now nordvpnd
say "NordVPN installé : $(nordvpn --version 2>/dev/null | head -1)"

# 4. Relais TV Libre
mkdir -p /opt/tvlibre
cp "$BOOT/relay/tvlibre_relay.py" /opt/tvlibre/
cp "$BOOT/vpn-setup.sh" "$BOOT/update.sh" /opt/tvlibre/
chmod +x /opt/tvlibre/*.sh
cat > /etc/systemd/system/tvlibre-relay.service <<UNIT
[Unit]
Description=TV Libre relais
After=network-online.target nordvpnd.service
Wants=network-online.target

[Service]
Environment=HOME=/root
ExecStart=/usr/bin/python3 /opt/tvlibre/tvlibre_relay.py
Restart=always
RestartSec=3
Environment=TVLIBRE_VPN_COUNTRY=Switzerland
Environment=PYTHONUNBUFFERED=1
RuntimeDirectory=tvlibre

[Install]
WantedBy=multi-user.target
UNIT

# Réglage du VPN à chaque démarrage tant que le jeton n'a pas été utilisé
cat > /etc/systemd/system/tvlibre-vpn-setup.service <<UNIT
[Unit]
Description=TV Libre réglage NordVPN
After=network-online.target nordvpnd.service
Wants=network-online.target
ConditionPathExists=!/opt/tvlibre/vpn-ok

[Service]
Environment=HOME=/root
Type=oneshot
ExecStart=/opt/tvlibre/vpn-setup.sh

[Install]
WantedBy=multi-user.target
UNIT

# Mise à jour du relais depuis GitHub (au démarrage puis toutes les 6 h)
cat > /etc/systemd/system/tvlibre-update.service <<UNIT
[Unit]
Description=TV Libre mise à jour du relais
After=network-online.target

[Service]
Environment=HOME=/root
Type=oneshot
ExecStart=/opt/tvlibre/update.sh
UNIT
cat > /etc/systemd/system/tvlibre-update.timer <<UNIT
[Unit]
Description=TV Libre mise à jour du relais

[Timer]
OnBootSec=3min
OnUnitActiveSec=6h

[Install]
WantedBy=timers.target
UNIT

# 5. Chien de garde matériel : redémarrage si le système se fige
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<CONF
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
CONF

systemctl daemon-reload
systemctl enable --now tvlibre-relay.service tvlibre-update.timer tvlibre-vpn-setup.service
say "Relais installé et démarré (port 8090)"

# 6. VPN
/opt/tvlibre/vpn-setup.sh
say "Installation terminée. Adresse : $(hostname -I | awk '{print $1}')"
