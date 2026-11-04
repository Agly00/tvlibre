// Catalogue (« cerveau ») : sources, fusion des flux par chaîne, préférences, filtres.
// Aucun code d'interface ici : ce module est transférable tel quel.
import { BUILTIN_SOURCES, COUNTRIES } from './builtin.js';
import { parseM3U, singleStreamChannel, isHLSMedia, isBareIP, normalize } from './m3u.js';
import { getPref, setPref, idbGet, idbSet, idbDelete } from './store.js';
import { fetchText } from './net.js';

// ---------- Sources ----------

function loadConfigs() {
  const saved = getPref('sources', []);
  const out = [];
  for (const b of BUILTIN_SOURCES) {
    const s = saved.find((x) => x.id === b.id);
    out.push({ ...b, enabled: s ? s.enabled !== false : true });
  }
  for (const s of saved) if (!s.builtin) out.push(s);
  return out;
}

let configs = loadConfigs();

function saveConfigs() {
  setPref('sources', configs.map(({ text, ...rest }) => (rest.builtin ? { id: rest.id, enabled: rest.enabled, builtin: true } : rest)));
}

export function sources() {
  return configs;
}

export function setSourceEnabled(id, on) {
  const c = configs.find((x) => x.id === id);
  if (c) { c.enabled = on; saveConfigs(); }
}

export function addRemoteSource(url, name) {
  const u = url.trim();
  if (!/^https?:\/\//.test(u) || configs.some((c) => c.url === u)) return false;
  let host = u;
  try { host = new URL(u).hostname; } catch { /* garde l'URL */ }
  configs.push({ id: 'user.' + Date.now(), name: name?.trim() || host, kind: 'remote', url: u, autoCountry: true, enabled: true });
  saveConfigs();
  return true;
}

export async function addTextSource(text, name) {
  if (!/https?:\/\//.test(text)) return false;
  const id = 'user.' + Date.now();
  await idbSet('list.' + id, text);
  configs.push({ id, name: name?.trim() || 'Liste collée', kind: 'stored', autoCountry: true, enabled: true });
  saveConfigs();
  return true;
}

export async function deleteSource(id) {
  const c = configs.find((x) => x.id === id);
  if (!c || c.builtin) return;
  configs = configs.filter((x) => x.id !== id);
  await idbDelete('list.' + id);
  await idbDelete('cache.' + id);
  saveConfigs();
}

// ---------- Chargement et fusion ----------

let channels = [];
let stats = {};
let errors = [];

async function sourceText(c) {
  if (c.kind === 'text') return { text: c.text };
  if (c.kind === 'stored') return { text: (await idbGet('list.' + c.id)) || '' };
  try {
    const text = await fetchText(c.url);
    idbSet('cache.' + c.id, { text, at: Date.now() });
    return { text, fresh: true };
  } catch (e) {
    // Hors ligne ou serveur indisponible : dernière copie connue
    const cached = await idbGet('cache.' + c.id);
    if (cached?.text) return { text: cached.text, stale: true, error: e.message };
    throw e;
  }
}

function parseSource(c, text) {
  const opt = {
    defaultCountry: c.defaultCountry || null,
    groups: c.groups || null,
    autoCountry: !!c.autoCountry,
    origin: c.name,
    sourceId: c.id,
    fromUser: !c.builtin || !!c.pinned,
  };
  if (c.kind === 'remote' && isHLSMedia(text)) {
    return [singleStreamChannel(c.url, c.name !== safeHost(c.url) ? c.name : null, opt)];
  }
  return parseM3U(text, opt);
}

function safeHost(u) {
  try { return new URL(u).hostname; } catch { return u; }
}

export async function loadAll() {
  const active = configs.filter((c) => c.enabled);
  const results = await Promise.all(active.map(async (c) => {
    try {
      const { text, stale, error } = await sourceText(c);
      const list = parseSource(c, text);
      if (!list.length) throw new Error('aucune chaîne lisible');
      return { c, list, warn: stale ? `copie hors ligne (${error})` : null };
    } catch (e) {
      return { c, list: [], err: e.message };
    }
  }));

  const merged = new Map();
  stats = {};
  errors = [];
  for (const r of results) {
    stats[r.c.id] = { channels: r.list.length, streams: r.list.reduce((n, ch) => n + ch.streams.length, 0) };
    if (r.err) errors.push(`${r.c.name} : ${r.err}`);
    if (r.warn) errors.push(`${r.c.name} : ${r.warn}`);
    for (const ch of r.list) {
      const m = merged.get(ch.id);
      if (!m) { merged.set(ch.id, { ...ch, streams: [...ch.streams] }); continue; }
      for (const s of ch.streams) if (!m.streams.some((x) => x.url === s.url)) m.streams.push(s);
      if (!m.logo && ch.logo) m.logo = ch.logo;
      if (!m.tvgId && ch.tvgId) m.tvgId = ch.tvgId;
      if (!m.group && ch.group) m.group = ch.group;
    }
  }
  channels = [...merged.values()];
  return channels;
}

export function sourceStats() { return stats; }
export function loadErrors() { return errors; }

// ---------- Préférences ----------

const prefs = {
  favorites: new Set(getPref('favorites', [])),
  hiddenChannels: new Set(getPref('hiddenChannels', [])),
  hiddenGroups: new Set(getPref('hiddenGroups', [])),   // "pays|groupe"
  showAll: getPref('showAll', false),
  hideBareIP: getPref('hideBareIP', false),
};

function savePrefs() {
  setPref('favorites', [...prefs.favorites]);
  setPref('hiddenChannels', [...prefs.hiddenChannels]);
  setPref('hiddenGroups', [...prefs.hiddenGroups]);
  setPref('showAll', prefs.showAll);
  setPref('hideBareIP', prefs.hideBareIP);
}

export function pref(name) { return prefs[name]; }
export function setPrefValue(name, v) { prefs[name] = v; savePrefs(); }

export function isFavorite(id) { return prefs.favorites.has(id); }
export function toggleFavorite(id) {
  prefs.favorites.has(id) ? prefs.favorites.delete(id) : prefs.favorites.add(id);
  savePrefs();
}

export const groupKey = (ch) => `${ch.country}|${ch.group || ''}`;

export function hideChannel(id, hidden = true) {
  hidden ? prefs.hiddenChannels.add(id) : prefs.hiddenChannels.delete(id);
  savePrefs();
}
export function hideGroup(key, hidden = true) {
  hidden ? prefs.hiddenGroups.add(key) : prefs.hiddenGroups.delete(key);
  savePrefs();
}
export function isHidden(ch) {
  return prefs.hiddenChannels.has(ch.id) || (ch.group && prefs.hiddenGroups.has(groupKey(ch)));
}

export function hiddenItems() {
  const byId = new Map(channels.map((c) => [c.id, c]));
  return {
    channels: [...prefs.hiddenChannels].map((id) => ({ id, name: byId.get(id)?.name || id.split('/').pop() })),
    groups: [...prefs.hiddenGroups].map((key) => {
      const [country, group] = key.split('|');
      return { key, label: `${COUNTRIES.find((c) => c.id === country)?.label || country} · ${group || 'sans groupe'}` };
    }),
  };
}

// ---------- Vues ----------

/** Flux utilisables d'une chaîne, après filtres globaux */
export function usableStreams(ch) {
  return prefs.hideBareIP ? ch.streams.filter((s) => !isBareIP(s.url)) : ch.streams;
}

function sortChannels(list) {
  return list.sort((a, b) => {
    if (a.mainRank !== null && b.mainRank !== null) return a.mainRank - b.mainRank;
    if (a.mainRank !== null) return -1;
    if (b.mainRank !== null) return 1;
    return a.name.localeCompare(b.name, 'fr');
  });
}

function matches(ch, q) {
  return !q || normalize(ch.name).includes(q);
}

export function visible(country, search) {
  const q = normalize(search);
  return sortChannels(channels.filter((ch) =>
    ch.country === country &&
    !isHidden(ch) &&
    usableStreams(ch).length > 0 &&
    (prefs.showAll || ch.mainRank !== null || ch.streams.some((s) => s.fromUser) || q) &&
    matches(ch, q)));
}

export function favoriteChannels(search) {
  const q = normalize(search);
  return channels.filter((ch) => prefs.favorites.has(ch.id) && !isHidden(ch) && usableStreams(ch).length && matches(ch, q));
}

export function allChannels() { return channels; }
export function channelById(id) { return channels.find((c) => c.id === id); }
export { COUNTRIES };
