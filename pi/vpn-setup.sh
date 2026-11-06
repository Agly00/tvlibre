#!/bin/bash
# Connexion NordVPN avec le jeton déposé sur la carte SD (fichier effacé après usage)
BOOT=/boot/firmware/tvlibre
STATUS=$BOOT/etat-installation.txt
say() { echo "$(date '+%F %T') $*"; echo "$(date '+%F %T') $*" >> "$STATUS"; }
[ -f /opt/tvlibre/vpn-ok ] && exit 0
TOKEN=$(grep -v '^#' "$BOOT/nordvpn-token.txt" 2>/dev/null | tr -d ' \r\n\t')
if [ -z "$TOKEN" ]; then
  say "VPN : pas de jeton dans nordvpn-token.txt, VPN non configuré (le relais fonctionne sans)"
  exit 0
fi
for i in $(seq 1 30); do nordvpn status >/dev/null 2>&1 && break; sleep 2; done
yes n | nordvpn set analytics off >/dev/null 2>&1
if ! nordvpn login --token "$TOKEN"; then
  say "VPN : jeton refusé, vérifie nordvpn-token.txt"
  exit 1
fi
SUBNET=$(python3 -c "import ipaddress,subprocess;a=subprocess.run(['ip','-4','-o','addr','show','dev','eth0'],capture_output=True,text=True).stdout.split()[3];print(ipaddress.ip_interface(a).network)" 2>/dev/null)
nordvpn set technology nordlynx
nordvpn set lan-discovery on
[ -n "$SUBNET" ] && nordvpn whitelist add subnet "$SUBNET"
nordvpn whitelist add port 8090
nordvpn whitelist add port 22
nordvpn whitelist add port 5353 protocol UDP   # nom tvlibre.local (mDNS) joignable avec le VPN
nordvpn set autoconnect on Switzerland
nordvpn connect Switzerland
if nordvpn status | grep -qiE "status: *connected"; then
  touch /opt/tvlibre/vpn-ok
  # Le jeton ne reste pas sur la carte
  printf '# Jeton utilisé puis effacé le %s\n' "$(date '+%F %T')" > "$BOOT/nordvpn-token.txt"
  say "VPN connecté : $(nordvpn status | grep -i -E 'server|country' | tr '\n' ' ')"
else
  say "VPN : connexion impossible pour l'instant, nouvel essai au prochain démarrage"
fi
