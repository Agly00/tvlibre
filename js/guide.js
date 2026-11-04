// Guide des programmes : adaptateur XMLTV (.xml ou .xml.gz).
// Ne garde que les chaînes du catalogue et une fenêtre de -6 h à +36 h, stockée localement.
import { getPref, setPref, idbGet, idbSet } from './store.js';
import { fetchText } from './net.js';
import { normalize, channelKey } from './m3u.js';

const REFRESH = 12 * 3600e3;
let byChannel = {}; // id chaîne catalogue → [{ start, stop, title, sub, desc }]
let status = { at: 0, count: 0, error: null, loading: false };

export function guideURLs() {
  return getPref('guides', []);
}
export function setGuideURLs(list) {
  setPref('guides', list);
}
export function guideStatus() {
  return status;
}

const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
function decode(s) {
  return (s || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => {
      if (e[0] === '#') return String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
      return ENT[e] ?? m;
    })
    .trim();
}

// « 20261103205000 +0100 » → horodatage
function parseTime(s) {
  const m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{4})?/.exec(s || '');
  if (!m) return NaN;
  const [, y, mo, d, h, mi, se = '00', tz = '+0000'] = m;
  const off = (tz[0] === '-' ? -1 : 1) * (parseInt(tz.slice(1, 3), 10) * 60 + parseInt(tz.slice(3), 10));
  return Date.UTC(+y, +mo - 1, +d, +h, +mi, +se) - off * 60e3;
}

function tag(block, name) {
  const m = new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`).exec(block);
  return m ? decode(m[1]) : '';
}

/**
 * @param {string} text XMLTV
 * @param {Array} channels chaînes du catalogue
 */
export function parseXMLTV(text, channels) {
  // Index du catalogue : tvg-id exact, souche du tvg-id, nom normalisé
  const byTvg = new Map();
  const byName = new Map();
  for (const ch of channels) {
    if (ch.tvgId) {
      byTvg.set(ch.tvgId.toLowerCase(), ch.id);
      byTvg.set(ch.tvgId.split('@')[0].toLowerCase(), ch.id);
    }
    byName.set(channelKey(ch.name), ch.id);
  }

  // Correspondance id XMLTV → chaîne du catalogue
  const map = new Map();
  for (const m of text.matchAll(/<channel\s+id="([^"]+)"[^>]*>([\s\S]*?)<\/channel>/g)) {
    const xid = m[1];
    let id = byTvg.get(xid.toLowerCase()) || byTvg.get(xid.split('@')[0].toLowerCase());
    if (!id) {
      for (const dn of m[2].matchAll(/<display-name[^>]*>([\s\S]*?)<\/display-name>/g)) {
        id = byName.get(channelKey(decode(dn[1])));
        if (id) break;
      }
    }
    if (!id) id = byName.get(channelKey(xid.split('.')[0]));
    if (id) map.set(xid, id);
  }

  const from = Date.now() - 6 * 3600e3;
  const to = Date.now() + 36 * 3600e3;
  const out = {};
  for (const m of text.matchAll(/<programme\s+([^>]*)>([\s\S]*?)<\/programme>/g)) {
    const attrs = m[1];
    const xid = /channel="([^"]+)"/.exec(attrs)?.[1];
    const id = xid && map.get(xid);
    if (!id) continue;
    const start = parseTime(/start="([^"]+)"/.exec(attrs)?.[1]);
    const stop = parseTime(/stop="([^"]+)"/.exec(attrs)?.[1]);
    if (!(stop > from && start < to)) continue;
    (out[id] ||= []).push({
      start, stop,
      title: tag(m[2], 'title'),
      sub: tag(m[2], 'sub-title'),
      desc: tag(m[2], 'desc'),
    });
  }
  for (const id of Object.keys(out)) out[id].sort((a, b) => a.start - b.start);
  return out;
}

export async function loadGuide(channels, { force = false } = {}) {
  const urls = guideURLs();
  const cached = await idbGet('guide');
  if (cached) {
    byChannel = cached.data || {};
    status = { ...status, at: cached.at, count: Object.keys(byChannel).length };
  }
  if (!urls.length) { byChannel = {}; status = { at: 0, count: 0, error: null, loading: false }; return; }
  if (!force && cached && Date.now() - cached.at < REFRESH && cached.urls === urls.join('|')) return;

  status = { ...status, loading: true, error: null };
  const data = {};
  const errs = [];
  for (const url of urls) {
    try {
      const text = await fetchText(url, 60000);
      const part = parseXMLTV(text, channels);
      for (const [id, list] of Object.entries(part)) data[id] = (data[id] || []).concat(list).sort((a, b) => a.start - b.start);
    } catch (e) {
      errs.push(`${url} : ${e.message}`);
    }
  }
  if (Object.keys(data).length) {
    byChannel = data;
    await idbSet('guide', { at: Date.now(), urls: urls.join('|'), data });
  }
  status = { at: Date.now(), count: Object.keys(byChannel).length, error: errs.join(' ; ') || null, loading: false };
}

export function programmes(channelId) {
  return byChannel[channelId] || [];
}

export function nowNext(channelId) {
  const list = programmes(channelId);
  const t = Date.now();
  const i = list.findIndex((p) => p.start <= t && p.stop > t);
  if (i < 0) return { now: null, next: list.find((p) => p.start > t) || null };
  return { now: list[i], next: list[i + 1] || null };
}

export function hasGuide() {
  return Object.keys(byChannel).length > 0;
}

export { normalize };
