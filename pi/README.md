# Raspberry TV Libre : installation

Copier ces trois fichiers dans `/boot/firmware/tvlibre/` de la carte SD (Raspberry Pi OS Lite 64 bits),
avec un fichier `nordvpn-token.txt` contenant le jeton NordVPN (jamais publié ici),
puis ajouter à la fin du `runcmd` de `user-data` :

    - [ bash, /boot/firmware/tvlibre/setup.sh ]

Au premier démarrage : ffmpeg, NordVPN (Suisse, NordLynx), relais `tvlibre_relay.py` (port 8090),
mises à jour automatiques depuis ce dépôt toutes les 6 h. Le relais installe ensuite lui-même WireGuard
(accès depuis l'extérieur) et prépare la clé USB des enregistrements.

Corrections intégrées : dossier personnel pour NordVPN (`HOME=/root`), nom `tvlibre.local` joignable
avec le VPN (port 5353), taille de paquet du tunnel gérée par le relais (1280).
