#!/bin/bash
# Récupère la dernière version du relais sur GitHub ; installe seulement si elle se compile.
# Reportée tant qu'une lecture ou un enregistrement est en cours (le relais relance lui-même plus tard).
URL=https://raw.githubusercontent.com/Agly00/tvlibre/main/relay/tvlibre_relay.py
if [ "$1" != "--force" ] && [ "$(curl -s -m 3 http://127.0.0.1:8090/idle)" = "busy" ]; then
  echo "$(date '+%F %T') mise à jour reportée (lecture en cours)" >> /var/log/tvlibre-update.log
  exit 0
fi
TMP=$(mktemp)
if curl -fsSL -m 60 "$URL" -o "$TMP" && python3 -m py_compile "$TMP" 2>/dev/null; then
  if ! cmp -s "$TMP" /opt/tvlibre/tvlibre_relay.py; then
    cp "$TMP" /opt/tvlibre/tvlibre_relay.py
    echo "$(date '+%F %T') relais mis à jour" >> /var/log/tvlibre-update.log
    systemctl restart tvlibre-relay.service
  fi
fi
rm -f "$TMP"
