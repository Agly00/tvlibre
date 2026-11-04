// Lecteur web : seul module propre à la plateforme. Il reçoit une chaîne, choisit le flux,
// bascule en cas d'échec, et renvoie des événements de santé au catalogue.
import * as H from './health.js';
import { usableStreams, COUNTRIES } from './catalog.js';
import { playbackURL, viaRelay } from './net.js';
import { exitLocation, onExitChange } from './location.js';

const START_TIMEOUT = 20; // secondes pour obtenir une image

export class Player {
  constructor(video) {
    this.video = video;
    this.hls = null;
    this.channel = null;
    this.queue = [];
    this.current = null;
    this.tried = new Set();
    this.locked = false;
    this.ignoreQuarantine = false;
    this.succeeded = false;
    this.error = null;
    this.wireless = false;
    this.listeners = new Set();
    this.watchdog = null;
    this.token = 0;

    video.setAttribute('playsinline', '');
    video.setAttribute('x-webkit-airplay', 'allow');
    video.setAttribute('autopictureinpicture', '');
    video.preload = 'auto';

    video.addEventListener('error', () => this.onMediaError());
    video.addEventListener('playing', () => this.emit());
    video.addEventListener('pause', () => this.emit());
    video.addEventListener('loadedmetadata', () => this.emit());
    video.addEventListener('webkitcurrentplaybacktargetiswirelesschanged', () => {
      this.wireless = !!video.webkitCurrentPlaybackTargetIsWireless;
      this.emit();
    });

    onExitChange(() => this.exitChanged());
    this.setupMediaSession();
  }

  // ---------- Événements vers l'interface ----------

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit() { this.listeners.forEach((fn) => fn(this)); }

  // ---------- Localisation ----------

  get exitCountry() { return exitLocation().country; }

  /** Pays exigé par un flux géobloqué (null = flux libre) */
  zone(s) {
    return s.geo ? COUNTRIES.find((c) => c.id === this.channel?.country)?.iso || null : null;
  }

  compatible(s) {
    const z = this.zone(s);
    const e = this.exitCountry;
    if (!z || !e || z === e) return true;
    return viaRelay(playbackURL(s, { needsCountry: z, exitCountry: e }));
  }

  exitChanged() {
    if (!this.channel || this.succeeded) return;
    if (this.current && this.compatible(this.current) && (this.locked || this.current.geo)) {
      this.play(this.current);
      return;
    }
    this.tried.clear();
    const s = this.startStream();
    if (s) this.play(s);
  }

  // ---------- Choix du flux ----------

  open(channel) {
    this.channel = channel;
    this.queue = usableStreams(channel);      // ordre fixe (ordre des listes)
    this.tried = new Set();
    this.locked = false;
    this.ignoreQuarantine = false;
    this.error = null;
    const s = this.startStream();
    if (s) {
      this.play(s);
    } else {
      this.stopMedia();
      this.current = null;
      const e = this.exitCountry;
      if (e && this.queue.length && this.queue.every((x) => !this.compatible(x))) {
        const iso = COUNTRIES.find((c) => c.id === channel.country)?.iso || '';
        this.error = `Cette chaîne n'a que des flux géobloqués ${iso} (sortie actuelle : ${e}). Active un VPN ${iso} ou choisis un flux dans le menu.`;
      } else {
        this.error = 'Toutes les sources de cette chaîne sont en quarantaine.';
      }
    }
    this.updateMediaSession();
    this.emit();
  }

  startStream() {
    const prefURL = H.preferredStream(this.channel.id, this.exitCountry);
    const pref = prefURL && this.queue.find((s) => s.url === prefURL);
    if (pref && this.compatible(pref) && H.isDue(pref.url)) return pref;
    return this.nextAuto(null);
  }

  nextAuto(after) {
    const n = this.queue.length;
    if (!n) return null;
    const start = after ? this.queue.findIndex((s) => s.url === after.url) + 1 : 0;
    for (let k = 0; k < n; k++) {
      const c = this.queue[(start + k) % n];
      if (this.tried.has(c.url)) continue;
      if (!this.ignoreQuarantine && !H.isDue(c.url)) continue;
      if (!this.ignoreQuarantine && !this.compatible(c)) continue;
      return c;
    }
    return null;
  }

  choose(s) { this.locked = true; this.play(s); }
  retryCurrent() { this.current ? this.play(this.current) : this.retryAll(); }
  retryAll() { this.open(this.channel); }
  forceTry() {
    this.ignoreQuarantine = true;
    this.locked = false;
    this.tried = new Set();
    const s = this.nextAuto(null);
    if (s) this.play(s);
  }
  unlockAndContinue() {
    this.locked = false;
    const s = this.nextAuto(this.current);
    s ? this.play(s) : this.retryAll();
  }

  // ---------- Lecture ----------

  play(s) {
    const token = ++this.token;
    clearInterval(this.watchdog);
    this.tried.add(s.url);
    this.current = s;
    this.error = null;
    this.succeeded = false;
    this.stopMedia(false);

    const url = playbackURL(s, { needsCountry: this.zone(s), exitCountry: this.exitCountry });
    const v = this.video;
    if (v.canPlayType('application/vnd.apple.mpegurl')) {
      v.src = url;                       // Safari : HLS natif, AirPlay vidéo
      this.tryPlay();
    } else {
      this.playWithHls(url, token);      // autres navigateurs (tests sur ordinateur)
    }
    this.startWatchdog(s, token);
    this.updateMediaSession();
    this.emit();
  }

  tryPlay() {
    const p = this.video.play();
    if (p && p.catch) p.catch(() => this.emit()); // lecture bloquée : bouton ▶︎ affiché
  }

  async playWithHls(url, token) {
    if (!window.Hls) await loadScript('js/vendor/hls.light.min.js');
    if (token !== this.token) return;
    if (!window.Hls?.isSupported()) { this.video.src = url; this.tryPlay(); return; }
    const hls = new window.Hls({ enableWorker: true, lowLatencyMode: false });
    this.hls = hls;
    hls.on(window.Hls.Events.ERROR, (_, d) => {
      if (!d.fatal || token !== this.token) return;
      const code = d.response?.code;
      const reason = code === 401 || code === 403 || code === 451 ? 'refused'
        : d.type === 'networkError' ? 'network' : d.type === 'mediaError' ? 'format' : 'other';
      this.failed(this.current, token, reason, code ? `HTTP ${code} (${d.details})` : d.details);
    });
    hls.loadSource(url);
    hls.attachMedia(this.video);
    hls.on(window.Hls.Events.MANIFEST_PARSED, () => this.tryPlay());
  }

  startWatchdog(s, token) {
    let elapsed = 0;
    this.watchdog = setInterval(() => {
      if (token !== this.token) return clearInterval(this.watchdog);
      elapsed++;
      const v = this.video;
      if (v.readyState >= 2 && (this.wireless || v.videoWidth > 0)) {
        clearInterval(this.watchdog);
        return this.succeed(s, v.videoHeight || null);
      }
      // Le son avance depuis 6 s sans aucune image : son seul
      if (v.readyState >= 2 && v.videoWidth === 0 && v.currentTime >= 6 && !this.wireless) {
        clearInterval(this.watchdog);
        return this.failed(s, token, 'audioOnly');
      }
      // Lecture bloquée par le navigateur (jamais démarrée, faute de geste) : on attend l'appui sur ▶︎
      if (v.paused && v.played.length === 0 && v.readyState >= 2) { elapsed = Math.min(elapsed, START_TIMEOUT - 5); return; }
      if (elapsed >= START_TIMEOUT) {
        clearInterval(this.watchdog);
        this.failed(s, token, v.readyState >= 2 && v.videoWidth === 0 ? 'audioOnly' : 'timeout');
      }
    }, 1000);
  }

  succeed(s, height) {
    if (this.succeeded) return;
    this.succeeded = true;
    H.recordSuccess(s, this.channel, height, this.exitCountry);
    this.emit();
  }

  onMediaError() {
    if (this.hls || !this.current) return; // erreurs gérées par hls.js
    const code = this.video.error?.code;
    const reason = code === 2 ? 'network' : code === 3 ? 'format' : 'other';
    const detail = code === 4 ? 'source refusée ou format non pris en charge' : this.video.error?.message || null;
    this.failed(this.current, this.token, reason, detail);
  }

  failed(s, token, reason, detail) {
    if (token !== this.token || !s) return;
    clearInterval(this.watchdog);
    // Flux géobloqué refusé ou sans image : la sortie réseau est en cause, pas le flux
    const geoIssue = s.geo && ['refused', 'audioOnly', 'other'].includes(reason);
    if (!geoIssue) H.recordFailure(s, this.channel, reason, detail);

    if (geoIssue) {
      const z = this.zone(s) || '?';
      const e = this.exitCountry || 'inconnue';
      const hint = this.compatible(s)
        ? `Ton IP de sortie (${e}) est refusée par la chaîne : serveur VPN probablement repéré. Change de serveur puis « Réessayer ce flux ».`
        : `Ce flux exige une IP ${z} (sortie actuelle : ${e}). Active un VPN ${z} : il sera relancé automatiquement.`;
      if (this.locked || !this.nextAuto(s)) { this.error = hint; this.emit(); return; }
    }
    if (this.locked) {
      this.error = `Le flux choisi ne fonctionne pas (${(H.REASONS[reason] || reason).toLowerCase()}${detail ? ', ' + detail : ''}).`;
      this.emit();
      return;
    }
    const next = this.nextAuto(s);
    if (next) return this.play(next);
    this.error = `Aucun flux n'a fonctionné (dernier : ${(H.REASONS[reason] || reason).toLowerCase()}).`;
    this.emit();
  }

  togglePlay() {
    if (this.video.paused) this.tryPlay(); else this.video.pause();
  }

  stopMedia(clearSrc = true) {
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    if (clearSrc) {
      this.video.pause();
      this.video.removeAttribute('src');
      this.video.load();
    }
  }

  stop() {
    this.token++;
    clearInterval(this.watchdog);
    this.stopMedia(true);
    this.channel = null;
    this.current = null;
    this.emit();
  }

  // ---------- AirPlay, image dans l'image ----------

  get airplayAvailable() { return typeof this.video.webkitShowPlaybackTargetPicker === 'function'; }
  showAirPlay() { this.video.webkitShowPlaybackTargetPicker?.(); }

  get pipAvailable() {
    return typeof this.video.webkitSetPresentationMode === 'function' || document.pictureInPictureEnabled;
  }
  pip() {
    const v = this.video;
    if (typeof v.webkitSetPresentationMode === 'function') {
      v.webkitSetPresentationMode(v.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture');
    } else if (document.pictureInPictureElement) {
      document.exitPictureInPicture();
    } else {
      v.requestPictureInPicture?.().catch(() => {});
    }
  }

  // ---------- Écran verrouillé / Centre de contrôle ----------

  setZapHandler(fn) { this.zapHandler = fn; }

  setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    const set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* action non prise en charge */ } };
    set('play', () => this.tryPlay());
    set('pause', () => this.video.pause());
    set('nexttrack', () => this.zapHandler?.(1));
    set('previoustrack', () => this.zapHandler?.(-1));
    set('seekbackward', null);
    set('seekforward', null);
  }

  updateMediaSession(subtitle) {
    if (!('mediaSession' in navigator) || !this.channel) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: this.channel.name,
        artist: subtitle || 'TV Libre · Direct',
        artwork: this.channel.logo ? [{ src: this.channel.logo, sizes: '512x512' }] : [],
      });
    } catch { /* ignoré */ }
  }

  // ---------- Libellés ----------

  label(s) {
    const parts = [s.origin];
    if (!this.compatible(s)) parts.push(`⚠︎ IP ${this.zone(s)}`);
    const h = H.knownHeight(s);
    if (h) parts.push(`${h}p`);
    if (s.url.startsWith('http:')) parts.push('http');
    const hh = H.health(s.url);
    if (hh?.status === 'quarantined') parts.push('quarantaine');
    else if (hh?.lastSuccess) parts.push('✓');
    return parts.join(' · ');
  }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = resolve;
    el.onerror = reject;
    document.head.appendChild(el);
  });
}
