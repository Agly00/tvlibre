#!/bin/bash
# TV Libre sur la télé : installation sur le Raspberry (à lancer en root, Raspberry Pi OS Bookworm 64 bits).
# Prototype : rien n'est lancé tant que le service n'est pas activé à la main (dernière ligne).
set -e
DIR=/opt/tvlibre-tv
apt-get update
apt-get install -y libmpv2 python3-venv python3-pip v4l-utils cec-utils fonts-inter || apt-get install -y libmpv2 python3-venv v4l-utils cec-utils
mkdir -p $DIR /etc/tvlibre
cp -r tvlibre_tv.py lineup.py lineup.json qml $DIR/
python3 -m venv $DIR/venv
$DIR/venv/bin/pip install --upgrade pip
$DIR/venv/bin/pip install PySide6 python-mpv
# Compte IPTV : rempli à la main ou envoyé plus tard par l'app iPhone (lisible par root seulement)
if [ ! -f /etc/tvlibre/tv.json ]; then
  echo '{"server": "http://serveur:port", "username": "", "password": ""}' > /etc/tvlibre/tv.json
  chmod 600 /etc/tvlibre/tv.json
fi
cat > /etc/systemd/system/tvlibre-tv.service <<UNIT
[Unit]
Description=TV Libre sur la télé (HDMI)
After=network-online.target tvlibre-relay.service

[Service]
Environment=QT_QPA_PLATFORM=eglfs
Environment=QT_QPA_EGLFS_HIDECURSOR=1
Environment=QT_QPA_EGLFS_KMS_ATOMIC=1
# La télécommande de la télé (HDMI-CEC) : le Raspberry se déclare lecteur, la télé lui transmet les touches
ExecStartPre=-/usr/bin/cec-ctl -d /dev/cec0 --playback -S
ExecStart=$DIR/venv/bin/python $DIR/tvlibre_tv.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
echo "Installé. Essai : systemctl start tvlibre-tv   (activer au démarrage : systemctl enable tvlibre-tv)"
