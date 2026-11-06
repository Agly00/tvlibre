#!/bin/bash
# Récupère la dernière version du relais sur GitHub ; installe seulement si elle se compile
URL=https://raw.githubusercontent.com/Agly00/tvlibre/main/relay/tvlibre_relay.py
TMP=$(mktemp)
if curl -fsSL -m 60 "$URL" -o "$TMP" && python3 -m py_compile "$TMP" 2>/dev/null; then
  if ! cmp -s "$TMP" /opt/tvlibre/tvlibre_relay.py; then
    cp "$TMP" /opt/tvlibre/tvlibre_relay.py
    systemctl restart tvlibre-relay.service
    echo "$(date '+%F %T') relais mis à jour" >> /var/log/tvlibre-update.log
  fi
fi
rm -f "$TMP"
