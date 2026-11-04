// Interface : liste des chaînes, réglages, lecteur. Toute la logique métier vit dans catalog/health/guide/player.
import * as C from './catalog.js';
import * as H from './health.js';
import * as G from './guide.js';
import { Player } from './player.js';
import { refreshExit, exitLabel, exitLocation, onExitChange } from './location.js';
import { getPref, setPref } from './store.js';
import { relayBase, playbackURL } from './net.js';

const VERSION = '1.0.11';
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hhmm = (t) => new Date(t).toLocaleTimeString('fr-CH', { hour: '2-digit', minute: '2-digit' });
const rel = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' });
function relTime(t) {
  const m = Math.round((t - Date.now()) / 60e3);
  return Math.abs(m) < 60 ? rel.format(m, 'minute') : Math.abs(m) < 48 * 60 ? rel.format(Math.round(m / 60), 'hour') : rel.format(Math.round(m / 1440), 'day');
}
const tvIcon = '<svg><use href="#i-tv"/></svg>';
const logoHTML = (ch) => ch.logo ? `<img src="${esc(ch.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : tvIcon;

let search = '';
let loading = true;

// Logos introuvables : remplacés par l'icône générique
document.addEventListener('error', (e) => {
  if (e.target.tagName === 'IMG' && e.target.closest('.logo')) e.target.outerHTML = tvIcon;
}, true);

function toast(msg, ms = 2600, onTap) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  t.onclick = onTap || null;
  clearTimeout(toast.timer);
  if (ms) toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}

// =====================================================================
// Écran principal
// =====================================================================

function subLine(ch) {
  const parts = [];
  const { now } = G.nowNext(ch.id);
  if (now) parts.push(`${hhmm(now.start)} ${esc(now.title)}`);
  else {
    if (ch.streams.every((s) => s.geo)) parts.push('<span class="geo">Géobloquée</span>');
    const n = C.usableStreams(ch).length;
    parts.push(n > 1 ? `${n} flux` : '1 flux');
  }
  if (H.hasPreferred(ch.id)) parts.push('<span class="ok">✓</span>');
  return parts.join(' · ');
}

function rowHTML(ch, listName, i) {
  const fav = C.isFavorite(ch.id);
  return `<div class="row" data-list="${listName}" data-i="${i}" data-id="${esc(ch.id)}">
    <span class="logo">${logoHTML(ch)}</span>
    <span class="txt"><div class="name">${esc(ch.name)}</div><div class="sub">${subLine(ch)}</div></span>
    <button class="star ${fav ? 'on' : ''}" data-star aria-label="Favori"><svg><use href="#i-star${fav ? '-fill' : ''}"/></svg></button>
  </div>`;
}

const lists = {}; // nom de section → liste de chaînes (contexte de zapping)

function renderHome() {
  const el = $('#list');
  $('#exit-badge').textContent = exitLocation().country ? `Sortie ${exitLocation().country}` : '';
  if (loading && !C.allChannels().length) { el.innerHTML = '<div class="spinner"></div>'; return; }

  let html = '';
  const favs = C.favoriteChannels(search);
  lists.fav = favs;
  if (favs.length) html += `<div class="section-title">Favoris</div><div class="group">${favs.map((c, i) => rowHTML(c, 'fav', i)).join('')}</div>`;
  for (const country of C.COUNTRIES) {
    const l = C.visible(country.id, search);
    lists[country.id] = l;
    if (l.length) html += `<div class="section-title">${country.label} · ${l.length}</div><div class="group">${l.map((c, i) => rowHTML(c, country.id, i)).join('')}</div>`;
  }
  if (!html) html = `<div class="empty">${search ? 'Aucune chaîne trouvée.' : 'Aucune chaîne. Vérifie tes listes dans les réglages.'}</div>`;
  const errs = C.loadErrors();
  if (errs.length) html += `<div class="section-title">Chargement</div><div class="errors">${errs.map(esc).join('<br>')}</div>`;
  el.innerHTML = html;
}

// Clic, favori, appui long
(() => {
  const list = $('#list');
  let pressTimer = null;
  let longPressed = false;
  let start = null;

  list.addEventListener('click', (e) => {
    const row = e.target.closest('.row');
    if (!row) return;
    if (longPressed) { longPressed = false; return; }
    const ch = lists[row.dataset.list]?.[+row.dataset.i];
    if (!ch) return;
    if (e.target.closest('[data-star]')) { C.toggleFavorite(ch.id); renderHome(); return; }
    openPlayer(lists[row.dataset.list], +row.dataset.i);
  });
  list.addEventListener('contextmenu', (e) => {
    const row = e.target.closest('.row');
    if (!row) return;
    e.preventDefault();
    channelActions(lists[row.dataset.list]?.[+row.dataset.i]);
  });
  list.addEventListener('touchstart', (e) => {
    const row = e.target.closest('.row');
    if (!row || e.target.closest('[data-star]')) return;
    start = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    longPressed = false;
    pressTimer = setTimeout(() => {
      longPressed = true;
      navigator.vibrate?.(10);
      channelActions(lists[row.dataset.list]?.[+row.dataset.i]);
    }, 500);
  }, { passive: true });
  list.addEventListener('touchmove', (e) => {
    if (!start) return;
    const t = e.touches[0];
    if (Math.hypot(t.clientX - start.x, t.clientY - start.y) > 10) clearTimeout(pressTimer);
  }, { passive: true });
  list.addEventListener('touchend', () => { clearTimeout(pressTimer); start = null; });
})();

let searchTimer;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { search = e.target.value; renderHome(); }, 150);
});

// =====================================================================
// Feuilles
// =====================================================================

let onSheetClose = null;

/** Remplace un conteneur par une copie vide (évite d'empiler les écouteurs) */
function fresh(el) {
  const n = el.cloneNode(false);
  el.replaceWith(n);
  return n;
}

function openSheet(title, html, bind, onClose) {
  $('#sheet-title').textContent = title;
  fresh($('#sheet-body')).innerHTML = html;
  $('#sheet').hidden = false;
  onSheetClose = onClose || null;
  bind?.($('#sheet-body'));
}

function closeSheet() {
  $('#sheet').hidden = true;
  const f = onSheetClose;
  onSheetClose = null;
  f?.();
}

$('#sheet-close').addEventListener('click', closeSheet);
$('#sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') closeSheet(); });

function sw(id, on) {
  return `<label class="switch"><input type="checkbox" id="${id}" ${on ? 'checked' : ''}><span></span></label>`;
}

// ---------- Actions sur une chaîne (appui long) ----------

function channelActions(ch) {
  if (!ch) return;
  const fav = C.isFavorite(ch.id);
  const hasProg = G.programmes(ch.id).length > 0;
  openSheet(ch.name, `
    <div class="card" style="margin-top:14px">
      <button class="item action" data-a="fav">${fav ? 'Retirer des favoris' : 'Ajouter aux favoris'}</button>
      ${hasProg ? '<button class="item action" data-a="guide">Programme</button>' : ''}
      <button class="item danger" data-a="hide">Masquer la chaîne</button>
      ${ch.group ? `<button class="item danger" data-a="hidegroup">Masquer le groupe « ${esc(ch.group)} »</button>` : ''}
    </div>
    <p class="note">Les éléments masqués se rétablissent dans Réglages › Masquées.</p>`, (body) => {
    body.addEventListener('click', (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (!a) return;
      if (a === 'fav') C.toggleFavorite(ch.id);
      if (a === 'hide') C.hideChannel(ch.id, true);
      if (a === 'hidegroup') C.hideGroup(C.groupKey(ch), true);
      if (a === 'guide') { openSheet(`Programme · ${ch.name}`, guideHTML(ch), scrollToNow); return; }
      closeSheet();
      renderHome();
    });
  });
}

function guideHTML(ch) {
  const list = G.programmes(ch.id);
  if (!list.length) return '<p class="note">Pas de programme pour cette chaîne.</p>';
  const t = Date.now();
  return `<div class="card" style="margin-top:12px">${list.map((p) => `
    <div class="prog ${p.start <= t && p.stop > t ? 'now' : ''}">
      <div class="t">${hhmm(p.start)} – ${hhmm(p.stop)}</div>
      <b>${esc(p.title)}</b>${p.sub ? ` <small class="t">· ${esc(p.sub)}</small>` : ''}
      ${p.desc ? `<p>${esc(p.desc)}</p>` : ''}
    </div>`).join('')}</div>`;
}

function scrollToNow(body) {
  body.querySelector('.prog.now')?.scrollIntoView({ block: 'center' });
}

// ---------- Réglages ----------

let catalogDirty = false;

function settingsHTML() {
  const stats = C.sourceStats();
  const hidden = C.hiddenItems();
  const q = H.quarantined();
  const gs = G.guideStatus();
  const rot = getPref('rotation', 'rot90');
  return `
  <div class="sh-title">Réseau</div>
  <div class="card">
    <div class="item"><span class="txt">${esc(exitLabel())}<small>Pays vu par les serveurs (Wi-Fi, données, VPN)</small></span><button class="text-btn" data-a="exit">Revérifier</button></div>
    <div class="item"><span class="txt"><input class="field" id="relay" placeholder="Relais (facultatif) : https://…" value="${esc(getPref('relay', ''))}" inputmode="url" autocapitalize="off"><small>Serveur relais pour flux http ou géobloqués (à venir)</small></span></div>
  </div>

  <div class="sh-title">Affichage</div>
  <div class="card">
    <div class="item"><span class="txt">Toutes les chaînes<small>Sinon : principales, favoris et tes listes</small></span>${sw('showAll', C.pref('showAll'))}</div>
    <div class="item"><span class="txt">Masquer les flux sur IP nue<small>Rediffusions non officielles</small></span>${sw('hideBareIP', C.pref('hideBareIP'))}</div>
    <div class="item"><span class="txt">Paysage simulé<small>Côté de la Dynamic Island quand tu tournes le téléphone</small></span>
      <span class="seg"><button data-rot="rot90" class="${rot === 'rot90' ? 'on' : ''}">Gauche</button><button data-rot="rotm90" class="${rot === 'rotm90' ? 'on' : ''}">Droite</button></span></div>
  </div>

  <div class="sh-title">Listes de chaînes</div>
  <div class="card">
    ${C.sources().map((s) => `
      <div class="item"><span class="txt">${esc(s.name)}<small>${s.builtin ? 'intégrée' : 'ajoutée'}${stats[s.id] && s.enabled ? ` · ${stats[s.id].channels} chaînes, ${stats[s.id].streams} flux` : ''}${s.kind === 'stored' ? ' · texte collé' : ''}</small></span>
        ${s.builtin ? '' : `<button class="text-btn" style="color:var(--red)" data-del="${esc(s.id)}">Suppr.</button>`}
        ${sw('src-' + s.id, s.enabled)}</div>`).join('')}
  </div>

  <div class="sh-title">Ajouter une liste</div>
  <div class="card">
    <div class="item"><input class="field" id="new-name" placeholder="Nom (facultatif)"></div>
    <div class="item"><input class="field" id="new-url" placeholder="https://…/liste.m3u ou flux.m3u8" inputmode="url" autocapitalize="off" autocorrect="off"></div>
    <div class="item"><button class="text-btn" data-a="paste-url">Coller le lien</button><span class="grow"></span><button class="text-btn" data-a="add-url">Ajouter</button></div>
    <button class="item action" data-a="paste-text">Coller une liste M3U (texte)…</button>
  </div>
  <p class="note">Les flux sont rattachés à leur chaîne (une seule entrée par chaîne) et rangés en France, Suisse ou Autres.</p>

  <div class="sh-title">Guide des programmes (XMLTV)</div>
  <div class="card">
    ${G.guideURLs().map((u) => `<div class="item"><span class="txt mono">${esc(u)}</span><button class="text-btn" style="color:var(--red)" data-delguide="${esc(u)}">Suppr.</button></div>`).join('')}
    <div class="item"><input class="field" id="new-guide" placeholder="https://…/guide.xml ou .xml.gz" inputmode="url" autocapitalize="off" autocorrect="off"><button class="text-btn" data-a="add-guide">Ajouter</button></div>
    <div class="item"><span class="txt">${gs.loading ? 'Mise à jour…' : gs.count ? `${gs.count} chaîne${gs.count > 1 ? 's' : ''} avec programme · ${hhmm(gs.at)}` : 'Aucun guide'}${gs.error ? `<small style="color:var(--red)">${esc(gs.error)}</small>` : ''}</span><button class="text-btn" data-a="guide-refresh">Mettre à jour</button></div>
  </div>

  <div class="sh-title">Masquées</div>
  <div class="card">
    ${hidden.groups.map((g) => `<div class="item"><span class="txt">Groupe : ${esc(g.label)}</span><button class="text-btn" data-unhidegroup="${esc(g.key)}">Rétablir</button></div>`).join('')}
    ${hidden.channels.map((c) => `<div class="item"><span class="txt">${esc(c.name)}</span><button class="text-btn" data-unhide="${esc(c.id)}">Rétablir</button></div>`).join('')}
    ${hidden.groups.length + hidden.channels.length === 0 ? '<div class="item"><span class="txt" style="color:var(--muted)">Rien de masqué. Appui long sur une chaîne pour la masquer.</span></div>' : ''}
  </div>

  <div class="sh-title">Quarantaine · ${q.length}</div>
  <div class="card">
    ${q.slice(0, 60).map((e) => `<div class="item"><span class="txt">${esc(e.channel || '?')}<small>${esc(e.origin || '')} · ${esc(H.REASONS[e.reason] || 'échec')}${e.detail ? ' (' + esc(e.detail) + ')' : ''} · nouvel essai ${relTime(e.nextRetry)}</small></span><button class="text-btn" data-react="${esc(e.url)}">Réactiver</button></div>`).join('')}
    ${q.length ? '<button class="item action" data-a="react-all">Tout réactiver</button>' : '<div class="item"><span class="txt" style="color:var(--muted)">Aucun flux en quarantaine.</span></div>'}
  </div>
  <p class="note">Un flux en échec est remis à l'essai après 30 min, 3 h, 24 h puis 72 h. Un flux géobloqué n'est jamais mis en quarantaine à cause de ta localisation.</p>

  <div class="card" style="margin-top:18px">
    <button class="item action" data-a="reload">Recharger les listes</button>
    <button class="item action" data-a="force-update">Forcer la mise à jour de l'app</button>
  </div>
  <p class="note">TV Libre ${VERSION}</p>`;
}

function openSettings() {
  catalogDirty = false;
  openSheet('Réglages', settingsHTML(), bindSettings, async () => {
    if (catalogDirty) await reloadCatalog();
    else renderHome();
  });
}

function refreshSettings() {
  const body = $('#sheet-body');
  const y = body.scrollTop;
  body.innerHTML = settingsHTML();
  body.scrollTop = y;
}

function bindSettings(body) {
  body.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'showAll' || t.id === 'hideBareIP') { C.setPrefValue(t.id, t.checked); renderHome(); }
    if (t.id === 'relay') setPref('relay', t.value.trim());
    if (t.id.startsWith('src-')) { C.setSourceEnabled(t.id.slice(4), t.checked); catalogDirty = true; }
  });
  body.addEventListener('click', async (e) => {
    const el = e.target.closest('button');
    if (!el) return;
    const d = el.dataset;
    if (d.rot) { setPref('rotation', d.rot); refreshSettings(); return; }
    if (d.del) { await C.deleteSource(d.del); catalogDirty = true; refreshSettings(); return; }
    if (d.unhide) { C.hideChannel(d.unhide, false); refreshSettings(); return; }
    if (d.unhidegroup) { C.hideGroup(d.unhidegroup, false); refreshSettings(); return; }
    if (d.react) { H.reactivate(d.react); refreshSettings(); return; }
    if (d.delguide) { G.setGuideURLs(G.guideURLs().filter((u) => u !== d.delguide)); refreshSettings(); return; }
    switch (d.a) {
      case 'exit': await refreshExit(true); refreshSettings(); renderHome(); break;
      case 'paste-url':
        try { $('#new-url', body).value = (await navigator.clipboard.readText()).trim(); } catch { toast('Presse-papiers inaccessible : colle le lien à la main.'); }
        break;
      case 'add-url':
        if (C.addRemoteSource($('#new-url', body).value, $('#new-name', body).value)) { catalogDirty = true; refreshSettings(); toast('Liste ajoutée : chargement à la fermeture des réglages.'); }
        else toast('Lien invalide ou déjà présent.');
        break;
      case 'paste-text': openPasteSheet(); break;
      case 'add-guide': {
        const u = $('#new-guide', body).value.trim();
        if (!/^https?:\/\//.test(u)) { toast('Lien invalide.'); break; }
        G.setGuideURLs([...new Set([...G.guideURLs(), u])]);
        refreshSettings();
        refreshGuide(true);
        break;
      }
      case 'guide-refresh': refreshGuide(true); break;
      case 'react-all': H.reactivateAll(); refreshSettings(); break;
      case 'reload': closeSheet(); await reloadCatalog(); break;
      case 'force-update': {
        // Vide la copie locale de l'app (pas les réglages ni les favoris) et recharge depuis GitHub
        try {
          for (const r of (await navigator.serviceWorker?.getRegistrations?.()) || []) await r.unregister();
          for (const k of (await caches?.keys?.()) || []) await caches.delete(k);
        } catch { /* ignoré */ }
        location.reload();
        break;
      }
    }
  });
}

function openPasteSheet() {
  openSheet('Coller une liste M3U', `
    <div class="card" style="margin-top:14px">
      <div class="item"><input class="field" id="paste-name" placeholder="Nom de la liste"></div>
      <div class="item"><textarea class="field" id="paste-text" placeholder="#EXTM3U&#10;#EXTINF:-1 …&#10;https://…" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea></div>
      <div class="item"><button class="text-btn" data-a="clip">Coller le presse-papiers</button><span class="grow"></span><button class="text-btn" data-a="save">Ajouter</button></div>
    </div>
    <p class="note">Utile quand le site de la liste refuse d'être lu depuis une page web.</p>`, (body) => {
    body.addEventListener('click', async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'clip') {
        try { $('#paste-text', body).value = await navigator.clipboard.readText(); } catch { toast('Presse-papiers inaccessible.'); }
      }
      if (a === 'save') {
        const ok = await C.addTextSource($('#paste-text', body).value, $('#paste-name', body).value);
        if (!ok) { toast('Le texte ne contient aucun lien.'); return; }
        closeSheet();
        await reloadCatalog();
        toast('Liste ajoutée.');
      }
    });
  });
}

$('#btn-settings').addEventListener('click', openSettings);

// =====================================================================
// Chargement
// =====================================================================

async function reloadCatalog() {
  loading = true;
  renderHome();
  await C.loadAll();
  loading = false;
  renderHome();
  refreshGuide(false);
}

async function refreshGuide(force) {
  const p = G.loadGuide(C.allChannels(), { force });
  if (!$('#sheet').hidden && $('#sheet-title').textContent === 'Réglages') refreshSettings();
  await p;
  renderHome();
  if (!$('#sheet').hidden && $('#sheet-title').textContent === 'Réglages') refreshSettings();
  if (ctx) renderPlayer();
}

// =====================================================================
// Lecteur
// =====================================================================

const video = $('#video');
const player = new Player(video);
const P = $('#player');
const rotor = $('#rotor');
const stage = $('#stage');
const controls = $('#controls');
const panel = $('#panel');
let ctx = null;            // { list, index }
let rotation = 'none';     // none | rot90 | rotm90
let hideTimer = null;
let panelTimer = null;

// Taille réelle de l'écran pour le paysage simulé : on mesure la zone du lecteur (plein écran),
// window.innerHeight étant plus court que l'écran dans une web app installée.
function setViewportVars() {
  const r = P.hidden ? null : P.getBoundingClientRect();
  const w = Math.max(r?.width || 0, window.innerWidth, document.documentElement.clientWidth);
  const h = Math.max(r?.height || 0, window.innerHeight, document.documentElement.clientHeight);
  document.documentElement.style.setProperty('--vw', `${w}px`);
  document.documentElement.style.setProperty('--vh', `${h}px`);
}
setViewportVars();
window.addEventListener('resize', () => {
  setViewportVars();
  // Rotation réelle (verrou désactivé) : on abandonne la rotation simulée
  if (rotation !== 'none' && window.innerWidth > window.innerHeight) setRotation('none');
  applyLayout();
});

const realLandscape = () => window.innerWidth > window.innerHeight;
const isLandscapeView = () => rotation !== 'none' || realLandscape();

function applyLayout() {
  rotor.classList.toggle('rot90', rotation === 'rot90');
  rotor.classList.toggle('rotm90', rotation === 'rotm90');
  rotor.classList.toggle('landscape', isLandscapeView());
  // Téléphone déjà en vrai paysage (rotation déverrouillée) : rien à simuler
  const rb = $('[data-act=rotate]');
  if (rb) rb.hidden = realLandscape() && rotation === 'none';
  requestAnimationFrame(updateBand);
}

/** Largeur des bandes noires de chaque côté de l'image (paysage), pour y caler les commandes */
// Paysage simulé : l'heure et le flou d'iOS occupent ~100 pt côté Dynamic Island.
// On décale l'image pour laisser cette marge de ce côté, en prenant sur la bande opposée.
const ISLAND_CLEAR = 100;

function updateBand() {
  let left = 0, right = 0, vl = 0, vr = 0;
  if (isLandscapeView() && !stage.classList.contains('fill')) {
    const W = stage.clientWidth;          // dimensions avant rotation : repère de l'image
    const H = stage.clientHeight;
    const ar = video.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
    const free = Math.max(0, W - Math.min(W, H * ar)); // noir total disponible
    left = right = free / 2;
    if (rotation !== 'none' && free > 0) {
      const islandSide = Math.min(free, Math.max(free / 2, ISLAND_CLEAR));
      const shift = Math.round(2 * islandSide - free);           // décalage de la zone vidéo
      if (rotation === 'rot90') { vl = shift; left = islandSide; right = free - islandSide; }
      else { vr = shift; right = islandSide; left = free - islandSide; }
    }
  }
  rotor.style.setProperty('--bandL', `${Math.round(left)}px`);
  rotor.style.setProperty('--bandR', `${Math.round(right)}px`);
  rotor.style.setProperty('--islandL', rotation === 'rot90' ? `${ISLAND_CLEAR}px` : '0px');
  stage.style.setProperty('--vl', `${vl}px`);
  stage.style.setProperty('--vr', `${vr}px`);
}
video.addEventListener('loadedmetadata', updateBand);

// ---------- Sous-titres (pistes fournies par le flux : WebVTT ou sous-titrage intégré) ----------

function subtitleTracks() {
  return [...video.textTracks].filter((t) => t.kind === 'subtitles' || t.kind === 'captions');
}
function trackLabel(t) {
  const lang = t.language ? new Intl.DisplayNames(['fr'], { type: 'language' }).of(t.language.split('-')[0]) : '';
  const base = t.label || lang || 'Piste';
  return base + (t.kind === 'captions' ? ' (malentendants)' : '');
}
/** Applique le choix mémorisé : null = désactivés, sinon langue préférée */
function applySubtitlePref() {
  const want = getPref('subs', null);
  const tracks = subtitleTracks();
  let chosen = null;
  if (want) chosen = tracks.find((t) => (t.language || '').startsWith(want)) || tracks.find((t) => t.kind === 'captions') || tracks[0];
  for (const t of tracks) t.mode = t === chosen ? 'showing' : 'disabled';
  if (hasLegacyCC() && !tracks.length) video.webkitClosedCaptionsVisible = !!want;
  updateSubsButton();
}
// Sous-titrage télé intégré au flux (CEA-608) : Safari le signale à part de la liste des pistes
const hasLegacyCC = () => !!video.webkitHasClosedCaptions;
const legacyCCOn = () => !!video.webkitClosedCaptionsVisible;

function updateSubsButton() {
  const b = $('#btn-subs');
  if (!b) return;
  const tracks = subtitleTracks();
  b.hidden = tracks.length === 0 && !hasLegacyCC();
  b.classList.toggle('on', tracks.some((t) => t.mode === 'showing') || legacyCCOn());
}
video.textTracks.addEventListener?.('addtrack', () => setTimeout(applySubtitlePref, 50));
video.addEventListener('loadedmetadata', () => setTimeout(applySubtitlePref, 300));
video.addEventListener('playing', updateSubsButton);
video.textTracks.addEventListener?.('removetrack', updateSubsButton);
video.textTracks.addEventListener?.('change', updateSubsButton);

function openSubtitles() {
  const tracks = subtitleTracks();
  const legacy = hasLegacyCC() && !tracks.length;
  const active = tracks.find((t) => t.mode === 'showing') || (legacy && legacyCCOn() ? 'cc' : null);
  openModal('Sous-titres', `
    <div class="card" style="margin-top:12px">
      <button class="item ${!active ? 'current' : ''}" data-ti="-1"><span class="txt">${!active ? '✓ ' : ''}Désactivés</span></button>
      ${tracks.map((t, i) => `<button class="item ${t === active ? 'current' : ''}" data-ti="${i}"><span class="txt">${t === active ? '✓ ' : ''}${esc(trackLabel(t))}</span></button>`).join('')}
      ${legacy ? `<button class="item ${active === 'cc' ? 'current' : ''}" data-ti="cc"><span class="txt">${active === 'cc' ? '✓ ' : ''}Sous-titrage du flux</span></button>` : ''}
    </div>
    <p class="note">Ton choix est retenu pour les autres chaînes quand la même langue existe.</p>`, (body) => {
    body.addEventListener('click', (e) => {
      const ti = e.target.closest('[data-ti]')?.dataset.ti;
      if (ti === undefined) return;
      if (legacy) {
        video.webkitClosedCaptionsVisible = ti === 'cc';
        setPref('subs', ti === 'cc' ? 'fr' : null);
        updateSubsButton();
        closeModal();
        return;
      }
      const t = tracks[+ti];
      setPref('subs', t ? (t.language || 'und').split('-')[0] : null);
      for (const x of tracks) x.mode = x === t ? 'showing' : 'disabled';
      updateSubsButton();
      closeModal();
    });
  });
}
video.addEventListener('resize', updateBand);

function setRotation(r) {
  rotation = r;
  setViewportVars();
  applyLayout();
}

function openPlayer(list, index) {
  ctx = { list, index };
  P.hidden = false;
  document.body.style.overflow = 'hidden';
  stage.classList.remove('fill');
  setRotation('none');
  closePanel();
  closeModal();
  player.open(list[index]);   // dans le geste de l'utilisateur : lecture autorisée
  showControls();
  renderPlayer();
}

function closePlayerCleanup() { hideAirplayCard(); }
function closePlayer() {
  closePlayerCleanup();
  if (navigator.serviceWorker?.controller && window.__pendingReload) { location.reload(); return; }
  player.stop();
  ctx = null;
  P.hidden = true;
  document.body.style.overflow = '';
  setRotation('none');
  if (document.webkitFullscreenElement) document.webkitExitFullscreen?.();
  renderHome();
}

function zap(delta) {
  if (!ctx || ctx.list.length < 2) return;
  selectIndex((ctx.index + delta + ctx.list.length) % ctx.list.length);
}

// ---------- Transition en AirPlay ----------
// Safari relance la session AirPlay à chaque changement de flux : on cache la vidéo locale
// et on affiche un carton jusqu'à ce que la télé reprenne.
let airplayCardTimer = null;
// Carton AirPlay : logo de la chaîne + icône de diffusion en bleu, à la place de la vidéo locale.
// Affiché pendant toute la diffusion AirPlay, et pendant la reconnexion au changement de chaîne.
let apZapping = false;
function renderAirplayCard(ch) {
  const card = $('#apcard');
  if (!ch) return;
  card.innerHTML = `<span class="logo">${logoHTML(ch)}</span><svg class="ap-icon"><use href="#i-airplay"/></svg>`;
}
function syncAirplayCard() {
  const on = !!ctx && !player.error && (player.wireless || apZapping);
  if (on) renderAirplayCard(player.channel);
  $('#apcard').hidden = !on;
  stage.classList.toggle('ap-zap', on);
}
function showAirplayCard() {
  apZapping = true;
  syncAirplayCard();
  clearTimeout(airplayCardTimer);
  airplayCardTimer = setTimeout(hideAirplayCard, 12000);
}
function hideAirplayCard() {
  clearTimeout(airplayCardTimer);
  apZapping = false;
  syncAirplayCard();
}
player.on(() => {
  if (apZapping && player.wireless && player.succeeded) { apZapping = false; clearTimeout(airplayCardTimer); }
  syncAirplayCard();
});

function selectIndex(i) {
  if (!ctx || !ctx.list[i]) return;
  ctx.index = i;
  const wasAirPlay = player.wireless;
  player.open(ctx.list[i]);
  if (wasAirPlay) showAirplayCard();
  const z = $('#zap');
  z.textContent = ctx.list[i].name;
  z.hidden = false;
  clearTimeout(zap.timer);
  zap.timer = setTimeout(() => { z.hidden = true; }, 1500);
  renderPlayer();
  if (!panel.hidden) renderPanel();
}
player.setZapHandler(zap);

function playerSub() {
  const ch = player.channel;
  if (!ch) return { text: '', cls: '' };
  const { now } = G.nowNext(ch.id);
  const s = player.current;
  const i = s ? player.queue.indexOf(s) : -1;
  const src = s ? `${player.locked ? '🔒 ' : ''}Flux ${i + 1}/${player.queue.length} · ${s.origin}` : '';
  const exit = exitLocation().country || '?';
  const text = now ? `${hhmm(now.start)} ${now.title} · ${exit}` : `${src} · ${exit}`;
  return { text, cls: relayBase() && s && playbackURL(s, { needsCountry: player.zone(s), exitCountry: exitLocation().country }) !== s.url ? 'vpn' : '' };
}

function renderPlayer() {
  if (!ctx || !player.channel) return;
  const sub = playerSub();
  P.querySelectorAll('.p-name').forEach((el) => { el.textContent = player.channel.name; });
  P.querySelectorAll('.p-sub').forEach((el) => { el.textContent = sub.text; el.className = 'p-sub ' + sub.cls; });
  $('.big-play').classList.toggle('playing', !video.paused);
  $('#btn-airplay').hidden = !player.airplayAvailable;
  updateSubsButton();
  const { now } = G.nowNext(player.channel.id);
  player.updateMediaSession(now ? `${hhmm(now.start)} ${now.title}` : undefined);

  const err = $('#perror');
  if (player.error) {
    const quarantine = /quarantaine/.test(player.error) || !player.current;
    err.innerHTML = `<p>${esc(player.error)}</p>
      <div class="btns">
        ${player.current ? '<button class="btn primary" data-act="retry">Réessayer ce flux</button>' : ''}
        <button class="btn" data-act="${player.locked ? 'auto' : 'retry-all'}">${player.locked ? 'Mode auto' : 'Tout réessayer'}</button>
        ${quarantine ? '<button class="btn primary" data-act="force">Essayer quand même</button>' : ''}
      </div>
      <div class="links"><button data-act="diag">Diagnostic</button><button data-act="dismiss">Masquer</button></div>`;
    err.hidden = false;
    showControls(false);
  } else {
    err.hidden = true;
  }
}

player.on(() => renderPlayer());
onExitChange(() => { renderPlayer(); renderHome(); });
video.addEventListener('play', () => { renderPlayer(); scheduleHide(); });
video.addEventListener('pause', () => { renderPlayer(); showControls(false); });

// ---------- Commandes ----------

function showControls(autoHide = true) {
  controls.classList.remove('off');
  rotor.classList.remove('chrome-off');
  if (autoHide) scheduleHide(); else clearTimeout(hideTimer);
}
function hideControls() {
  controls.classList.add('off');
  rotor.classList.add('chrome-off');   // barre du haut masquée aussi (portrait)
}
function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!video.paused && !player.error && $('#modal').hidden) hideControls();
  }, 4000);
}
function toggleControls() {
  controls.classList.contains('off') ? showControls() : hideControls();
}

// Paysage simulé uniquement (jamais le plein écran natif d'iOS)
function rotateOrFullscreen() {
  if (rotation !== 'none') { setRotation('none'); return; }
  if (realLandscape()) return;
  setRotation(getPref('rotation', 'rot90'));
}

P.addEventListener('click', (e) => {
  // Barre du haut masquée : un toucher la fait réapparaître sans déclencher de bouton
  if (rotor.classList.contains('chrome-off') && e.target.closest('.pbar')) { showControls(); return; }
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (!act) return;
  switch (act) {
    case 'close': closePlayer(); return;
    case 'panel': panel.hidden ? openPanel() : closePanel(); return;
    case 'streams': openStreams(); return;
    case 'play': player.togglePlay(); break;
    case 'airplay': player.showAirPlay(); break;
    case 'rotate': rotateOrFullscreen(); break;
    case 'subs': openSubtitles(); return;
    case 'retry': player.retryCurrent(); break;
    case 'retry-all': player.retryAll(); break;
    case 'auto': player.unlockAndContinue(); break;
    case 'force': player.forceTry(); break;
    case 'diag': openDiagnostic(); return;
    case 'dismiss': player.error = null; renderPlayer(); return;
    case 'modal-close': closeModal(); return;
  }
  showControls();
});

// ---------- Liste des chaînes par-dessus la vidéo ----------

function renderPanel() {
  if (!ctx) return;
  panel.innerHTML = ctx.list.map((ch, i) => {
    const { now } = G.nowNext(ch.id);
    return `<div class="row ${i === ctx.index ? 'current' : ''}" data-pi="${i}">
      <span class="logo">${logoHTML(ch)}</span>
      <span class="txt"><div class="name">${esc(ch.name)}</div>${now ? `<div class="sub">${hhmm(now.start)} ${esc(now.title)}</div>` : ''}</span>
    </div>`;
  }).join('');
  panel.querySelector('.current')?.scrollIntoView({ block: 'center' });
}

function openPanel() {
  panel.hidden = false;
  stage.classList.add('panel-open');
  controls.classList.add('off');
  renderPanel();
  closePanelSoon(8000);
}
function closePanel() {
  panel.hidden = true;
  stage.classList.remove('panel-open');
  clearTimeout(panelTimer);
}
function closePanelSoon(ms) {
  clearTimeout(panelTimer);
  panelTimer = setTimeout(closePanel, ms);
}
panel.addEventListener('click', (e) => {
  const row = e.target.closest('[data-pi]');
  if (!row) return;
  const i = +row.dataset.pi;
  if (i !== ctx.index) selectIndex(i);
  closePanelSoon(1500);
});
panel.addEventListener('scroll', () => closePanelSoon(8000), { passive: true });

// ---------- Fenêtres dans le lecteur (flux, diagnostic, programme) ----------

function openModal(title, html, bind) {
  const m = $('#modal');
  $('.modal-title', m).textContent = title;
  fresh($('.modal-body', m)).innerHTML = html;
  m.hidden = false;
  showControls(false);
  bind?.($('.modal-body', m));
}
function closeModal() {
  $('#modal').hidden = true;
  if (ctx) scheduleHide();
}
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });

function openStreams() {
  const ch = player.channel;
  if (!ch) return;
  const hasProg = G.programmes(ch.id).length > 0;
  openModal(ch.name, `
    ${hasProg ? '<div class="card" style="margin-top:12px"><button class="item action" data-m="guide">Programme</button></div>' : ''}
    <div class="sh-title">Flux · ${player.queue.length}</div>
    <div class="card">${player.queue.map((s, i) => `
      <button class="item ${s === player.current ? 'current' : ''}" data-si="${i}">
        <span class="txt">${s === player.current ? '✓ ' : ''}${esc(player.label(s))}<small class="mono">${esc(s.url)}</small></span>
      </button>`).join('')}</div>
    <div class="card" style="margin-top:12px">
      ${player.locked ? '<button class="item action" data-m="auto">Revenir au choix automatique</button>' : ''}
      ${player.pipAvailable ? '<button class="item action" data-m="pip">Image dans l\'image</button>' : ''}
      <button class="item action" data-m="diag">Diagnostic</button>
    </div>
    <p class="note">Choisir un flux le verrouille : pas de bascule automatique tant que tu ne reviens pas au choix automatique.</p>`, (body) => {
    body.addEventListener('click', (e) => {
      const si = e.target.closest('[data-si]')?.dataset.si;
      if (si !== undefined) { player.choose(player.queue[+si]); closeModal(); return; }
      const m = e.target.closest('[data-m]')?.dataset.m;
      if (m === 'auto') { player.unlockAndContinue(); closeModal(); }
      if (m === 'pip') { closeModal(); player.pip(); }
      if (m === 'diag') openDiagnostic();
      if (m === 'guide') openModal(`Programme · ${ch.name}`, guideHTML(ch), scrollToNow);
    });
  });
}

function diagLines() {
  const v = video;
  const s = player.current;
  const lines = [
    `Chaîne : ${player.channel?.name || '?'}`,
    exitLabel(),
    `Flux : ${s ? s.origin : 'aucun'}${player.locked ? ' (choisi à la main)' : ''}`,
    `État : ${player.succeeded ? 'image affichée ✓' : player.error ? 'échec' : 'validation en cours'}`,
    `Image : ${v.videoWidth ? `${v.videoWidth}×${v.videoHeight}` : 'aucune'}`,
    `Lecture : ${v.paused ? 'en pause' : 'en cours'} · readyState ${v.readyState}${player.wireless ? " · sur l'Apple TV" : ''}`,
  ];
  if (v.error) lines.push(`Erreur média : code ${v.error.code}${v.error.message ? ' · ' + v.error.message : ''}`);
  if (s) {
    lines.push(`Adresse : ${s.url}`);
    const pu = playbackURL(s, { needsCountry: player.zone(s), exitCountry: exitLocation().country });
    if (pu !== s.url) lines.push(`Via relais : ${pu}`);
  }
  return lines;
}

function openDiagnostic() {
  const ch = player.channel;
  if (!ch) return;
  const streams = player.queue.map((s) => {
    const h = H.health(s.url);
    const state = !h ? 'non testé' : h.status === 'ok' ? (h.lastSuccess ? `✓ image ${relTime(h.lastSuccess)}${h.height ? ' · ' + h.height + 'p' : ''}` : 'ok')
      : `quarantaine · ${H.REASONS[h.reason] || 'échec'}${h.detail ? ' (' + h.detail + ')' : ''} · nouvel essai ${relTime(h.nextRetry)}`;
    return { s, state };
  });
  openModal('Diagnostic', `
    <div class="card" style="margin-top:12px"><div class="item"><span class="txt mono" id="diag-live">${diagLines().map(esc).join('<br>')}</span></div></div>
    <div class="sh-title">Tous les flux</div>
    <div class="card">${streams.map(({ s, state }, i) => `
      <div class="item"><span class="txt">${esc(s.origin)}${s === player.current ? ' ▶︎' : ''}<small>${esc(state)}</small><small class="mono">${esc(s.url)}</small></span>
        <button class="text-btn" data-di="${i}">Lire</button>
        ${H.health(s.url)?.status === 'quarantined' ? `<button class="text-btn" data-dr="${i}">Réactiver</button>` : ''}</div>`).join('')}</div>
    <div class="card" style="margin-top:12px">
      <button class="item action" data-d="exit">Revérifier la sortie réseau</button>
      <button class="item action" data-d="copy">Copier le rapport</button>
    </div>`, (body) => {
    const live = setInterval(() => {
      const el = $('#diag-live', body);
      if (!el || $('#modal').hidden) return clearInterval(live);
      el.innerHTML = diagLines().map(esc).join('<br>');
    }, 1000);
    body.addEventListener('click', async (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.di !== undefined) { player.choose(player.queue[+b.dataset.di]); closeModal(); }
      if (b.dataset.dr !== undefined) { H.reactivate(player.queue[+b.dataset.dr].url); openDiagnostic(); }
      if (b.dataset.d === 'exit') { await refreshExit(true); openDiagnostic(); }
      if (b.dataset.d === 'copy') {
        const report = diagLines().concat(streams.map(({ s, state }) => `- ${s.origin} | ${state} | ${s.url}`)).join('\n');
        try { await navigator.clipboard.writeText(report); toast('Rapport copié.'); } catch { toast('Copie impossible.'); }
      }
    });
  });
}

// ---------- Gestes : swipe vertical (zapping), bord gauche (liste), tap, double tap ----------

(() => {
  let g = null;
  let lastTap = 0;
  let tapTimer = null;

  // Écart à l'écran → écart dans le repère de l'image (rotation simulée)
  function toContent(dX, dY) {
    if (rotation === 'rot90') return { dx: dY, dy: -dX };
    if (rotation === 'rotm90') return { dx: -dY, dy: dX };
    return { dx: dX, dy: dY };
  }
  function contentX(clientX, clientY) {
    if (rotation === 'rot90') return clientY;
    if (rotation === 'rotm90') return window.innerHeight - clientY;
    return clientX - stage.getBoundingClientRect().left;
  }

  stage.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.panel, .modal, .perror, button')) { g = null; return; }
    g = { x: e.clientX, y: e.clientY, t: Date.now(), cx: contentX(e.clientX, e.clientY) };
  });
  stage.addEventListener('pointerup', (e) => {
    if (!g) return;
    const { dx, dy } = toContent(e.clientX - g.x, e.clientY - g.y);
    const dt = Date.now() - g.t;
    const start = g;
    g = null;

    if (Math.abs(dy) > 50 && Math.abs(dy) > Math.abs(dx) * 1.3) { zap(dy < 0 ? 1 : -1); return; }
    if (start.cx < 70 && dx > 60 && Math.abs(dx) > Math.abs(dy)) { openPanel(); return; }
    if (Math.hypot(dx, dy) > 12 || dt > 400) return;

    const now = Date.now();
    if (now - lastTap < 280) {
      clearTimeout(tapTimer);
      lastTap = 0;
      stage.classList.toggle('fill');   // double tap : ajusté / rempli
      updateBand();
      return;
    }
    lastTap = now;
    tapTimer = setTimeout(() => {
      if (!panel.hidden) closePanel(); else toggleControls();
    }, 280);
  });
  stage.addEventListener('pointercancel', () => { g = null; });
  // Empêche le rebond de la page pendant les gestes
  P.addEventListener('touchmove', (e) => { if (!e.target.closest('.panel, .modal-body')) e.preventDefault(); }, { passive: false });
})();

// =====================================================================
// Démarrage
// =====================================================================

renderHome();
refreshExit(true).then(renderHome);
reloadCatalog();

// Retour dans l'app après longtemps : listes rafraîchies en arrière-plan
let lastLoad = Date.now();
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && Date.now() - lastLoad > 3600e3 && !ctx) {
    lastLoad = Date.now();
    await C.loadAll();
    renderHome();
  }
});

// Fonctionnement hors ligne et mises à jour
if ('serviceWorker' in navigator && location.protocol === 'https:') {
  // Nouvelle version publiée : elle prend la main et l'app se recharge d'elle-même (sauf pendant la lecture)
  const hadController = !!navigator.serviceWorker.controller;
  let reloaded = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController || reloaded) return;
    reloaded = true;
    if (!ctx) location.reload();
    else { window.__pendingReload = true; toast('Nouvelle version installée : elle s\'appliquera à la fermeture du lecteur.'); }
  });
  navigator.serviceWorker.register('sw.js').then((reg) => {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') reg.update().catch(() => {});
    });
  }).catch(() => {});
}
