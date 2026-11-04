# TV Libre (web app)

Lecteur de chaînes en direct pour iPhone, installé depuis Safari sur l'écran d'accueil.
Aucune expiration, aucun Xcode. Mise à jour : on remplace les fichiers en ligne.

## Mise en ligne sur GitHub Pages (une seule fois)

1. Connecte-toi sur github.com (crée un compte gratuit si besoin).
2. Bouton **+** en haut à droite > **New repository**. Nom : `tvlibre`. Coche **Public**. **Create repository**.
3. Sur la page du dépôt : **uploading an existing file**. Glisse **tout le contenu** du dossier `tvlibre-web`
   (index.html, app.css, sw.js, manifest.webmanifest, et les dossiers `js` et `icons`). **Commit changes**.
4. **Settings** > **Pages** > Source : **Deploy from a branch**, Branch : **main**, dossier **/ (root)** > **Save**.
5. Après une minute, l'adresse s'affiche : `https://<ton-nom>.github.io/tvlibre/`.

## Installation sur l'iPhone

1. Ouvre l'adresse dans **Safari**.
2. Bouton **Partager** > **Sur l'écran d'accueil** > **Ajouter**.
3. Lance TV Libre depuis son icône (et non depuis Safari) : plein écran, données conservées.

## Mettre à jour

Remplace les fichiers modifiés dans le dépôt GitHub. Si `sw.js` a changé de version, l'app affiche
« Nouvelle version disponible » : touche le message pour l'installer.

## Utilisation

- **Liste** : tap pour regarder, étoile pour les favoris, **appui long** pour masquer une chaîne ou son groupe.
- **Lecteur** : swipe vertical pour zapper, swipe depuis le bord gauche ou ☰ pour la liste des chaînes,
  tap pour les commandes, double tap pour ajusté / rempli.
- **Paysage** : bouton de rotation. Rotation verrouillée : paysage simulé (réglage du côté de la Dynamic Island
  dans Réglages). Rotation déverrouillée et téléphone tourné : plein écran natif, intégral.
- **Écran verrouillé** : lecture / pause et ⏮ ⏭ pour changer de chaîne.
- **Flux** (icône réglages du lecteur) : choisir un flux le verrouille ; diagnostic et image dans l'image.
- **Réglages** : listes (intégrées ou ajoutées par lien ou texte collé), guide XMLTV, éléments masqués,
  quarantaine, relais (pour plus tard).

## Architecture

| Fichier | Rôle |
|---|---|
| `js/m3u.js` | Adaptateur M3U standard vers le modèle commun |
| `js/guide.js` | Adaptateur XMLTV (guide des programmes) |
| `js/catalog.js` | Sources, fusion des flux par chaîne, préférences, filtres |
| `js/health.js` | Quarantaine et mémoire du dernier flux par pays de sortie |
| `js/location.js` | Pays de sortie de la connexion |
| `js/net.js` | Porte réseau unique, relais optionnel |
| `js/player.js` | Lecteur web (seul module propre à la plateforme) |
| `js/main.js` | Interface |
| `sw.js` | Cache local de l'app (démarrage sans réseau ni GitHub) |

`js/vendor/hls.light.min.js` (licence Apache 2.0, voir `hls.LICENSE`) ne sert que sur les navigateurs
sans HLS natif ; Safari sur iPhone utilise son lecteur intégré.
