# TV Libre sur la télé (prototype)

Le Raspberry branché en HDMI devient le boîtier télé : il décode lui-même la vidéo, sans Apple TV ni AirPlay.

- **Interface** : Qt Quick (QML), dessinée en 1920×1080, même organisation que l'app iPhone :
  Direct (groupes puis chaînes avec le programme en cours), Films et Séries (rangées d'affiches, « En cours »),
  Enregistrés.
- **Vidéo** : mpv (libmpv) dessine dans l'interface, décodage matériel du Raspberry, son en HDMI.
  Le DTS est décodé par mpv, sans conversion côté relais.
- **Télécommande** :
  - la télé en HDMI‑CEC : flèches, OK, retour ;
  - un clavier ;
  - l'iPhone, par un petit serveur de commandes (`GET http://<pi>:8091/key?k=up|down|left|right|ok|back|play|menu`).
- **Grille** : `lineup.json`, partagé avec l'app iPhone. Ordre : TNT, autres chaînes françaises par thème, Suisse
  romande, Royaume‑Uni, États‑Unis. Les flux viennent du serveur Xtream ; une liste Netplus ne sert que de repli.
- **Programme TV** : chaînes en lignes, heures en colonnes. Gauche et droite passent d'une émission à l'autre,
  en arrière (replay) comme en avant. OK lance la chaîne, le replay ou l'enregistrement ; lecture‑pause enregistre.
- **Compte** : l'iPhone l'envoie au Raspberry (Réglages, Raspberry, « Envoyer le compte à la télé »).
- **Direct** : haut et bas pour zapper, gauche et droite pour changer de groupe, OK pour afficher ou masquer le
  bandeau, retour pour le menu (la chaîne continue derrière).
- **Films** : gauche recule de 10 s, droite avance de 30 s, OK met en pause. La reprise de lecture est gardée sur
  le Raspberry.
- **Relais** : il reste en place (VPN, enregistrements). L'interface le prévient (`/busy`) pendant les lectures
  pour que ses téléchargements de fond se mettent en pause.

## Essayer sans Raspberry

```
pip install PySide6 python-mpv
python3 tvlibre_tv.py --demo                       # catalogue fictif, flèches et Entrée au clavier
python3 tvlibre_tv.py --demo --shot a.png --page films --keys down,right,ok
```

## Installer sur le Raspberry (plus tard)

`sudo ./install.sh` installe dans `/opt/tvlibre-tv` sans rien lancer. Il faut ensuite :

1. remplir `/etc/tvlibre/tv.json` (lisible par root seulement) ;
2. lancer `systemctl start tvlibre-tv`.

## À vérifier sur place

- **Touches de la télécommande de la télé (SimpLink, LG 2012)** : `cec-ctl -d /dev/cec0 --playback -S`, puis
  `ir-keytable -t` pour voir les touches reçues.
- **Décodage matériel** : le journal du service doit indiquer « Using hardware decoding », et la charge CPU dans `top` rester basse.
- **Son par la télé** : le Dolby Digital passe ; le DTS est décodé par le Raspberry.
