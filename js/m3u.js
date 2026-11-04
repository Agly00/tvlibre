// Adaptateur M3U standard : texte M3U → chaînes du modèle commun.
import { MAIN } from './builtin.js';

const UNPLAYABLE_HOSTS = ['youtube.com', 'youtu.be', 'dailymotion.com', 'github.com', 'twitch.tv'];

export function normalize(s) {
  return (s || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

/** Clé de chaîne sans suffixe de qualité (« TF1 HD » = « TF1 ») */
export function channelKey(s) {
  let k = normalize(s);
  for (const suffix of ['fhd', 'uhd', 'hd', 'sd', '4k']) {
    if (k.endsWith(suffix) && k.length > suffix.length + 1) {
      k = k.slice(0, -suffix.length);
      break;
    }
  }
  return k;
}

export function cleanName(raw) {
  return (raw || '')
    .replace(/\([^)]*\)/g, '')
    .replace(/\[[^\]]*\]/g, '')
    .replace(/[Ⓐ-ⓩ]/g, '') // Ⓖ Ⓨ…
    .replace(/\s+/g, ' ')
    .trim();
}

/** Contenu d'un flux HLS (et non d'une liste de chaînes) */
export function isHLSMedia(text) {
  return text.includes('#EXT-X-');
}

export function isPlayable(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (!/^https?:$/.test(u.protocol)) return false;
  const host = u.hostname.toLowerCase();
  if (UNPLAYABLE_HOSTS.some((h) => host === h || host.endsWith('.' + h))) return false;
  const p = u.pathname.toLowerCase();
  return !(p.endsWith('.html') || p.endsWith('/direct') || p.includes('le-live'));
}

export function isBareIP(url) {
  try {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function mainRank(keys, country) {
  const table = MAIN[country] || [];
  for (let i = 0; i < table.length; i++) {
    const aliases = table[i].split('|');
    if (keys.some((k) => aliases.includes(k))) return i;
  }
  return null;
}

function detectCountry(tvgId, group) {
  const base = (tvgId || '').split('@')[0];
  if (base.includes('.')) {
    const suffix = base.split('.').pop().toLowerCase();
    if (suffix === 'fr') return 'fr';
    if (suffix === 'ch') return 'ch';
  }
  const g = normalize(group);
  if (/suisse|switzerland|schweiz|svizzera/.test(g)) return 'ch';
  if (g.includes('france')) return 'fr';
  return null;
}

const ATTR_RE = /([A-Za-z0-9-]+)="([^"]*)"/g;
const HEIGHT_RE = /\((\d{3,4})[pi]\)/;

/**
 * @param {string} text  contenu M3U
 * @param {object} opt   { defaultCountry, groups, autoCountry, origin, fromUser, sourceId }
 * @returns {Array} chaînes { id, name, logo, country, group, tvgId, mainRank, streams[] }
 */
export function parseM3U(text, opt) {
  const byKey = new Map();
  let info = null;
  let ua = null;
  let ref = null;

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith('#EXTINF')) { info = line; ua = null; ref = null; continue; }
    if (line.startsWith('#EXTVLCOPT:http-user-agent=')) { ua = line.slice(27); continue; }
    if (line.startsWith('#EXTVLCOPT:http-referrer=')) { ref = line.slice(25); continue; }
    if (line.startsWith('#')) continue;

    const header = info || '';
    info = null;
    if (!isPlayable(line)) { ua = ref = null; continue; }

    const attrs = {};
    for (const m of header.matchAll(ATTR_RE)) attrs[m[1].toLowerCase()] = m[2];
    const tvgId = attrs['tvg-id'] || '';

    let country;
    if (opt.groups) {
      country = opt.groups[attrs['group-title']];
      if (!country) { ua = ref = null; continue; }
    } else if (opt.autoCountry) {
      country = detectCountry(tvgId, attrs['group-title']) || opt.defaultCountry || 'other';
    } else {
      country = opt.defaultCountry || 'other';
    }

    // Nom affiché : après la dernière guillemet, puis après la première virgule
    let tail = header;
    const q = header.lastIndexOf('"');
    if (q >= 0) tail = header.slice(q + 1);
    const c = tail.indexOf(',');
    if (c >= 0) tail = tail.slice(c + 1);
    const hm = tail.match(HEIGHT_RE);
    let name = cleanName(tail) || cleanName(attrs['tvg-name']);
    if (!name) {
      try { name = new URL(line).hostname; } catch { name = 'Flux'; }
    }

    const idStem = tvgId.split('@')[0].split('.')[0];
    const keys = [channelKey(idStem), channelKey(name), normalize(name)].filter(Boolean);
    const rank = mainRank(keys, country);
    const key = `${country}/${rank !== null ? 'main' + rank : keys[0] || line}`;

    const stream = {
      url: line,
      ua: ua || null,
      ref: ref || null,
      geo: /geo-blocked/i.test(header) || header.includes('Ⓖ'),
      origin: opt.origin,
      sourceId: opt.sourceId,
      fromUser: !!opt.fromUser,
      height: hm ? parseInt(hm[1], 10) : null,
    };
    ua = ref = null;

    const existing = byKey.get(key);
    if (existing) {
      if (!existing.streams.some((s) => s.url === stream.url)) existing.streams.push(stream);
      if (!existing.logo && attrs['tvg-logo']) existing.logo = attrs['tvg-logo'];
    } else {
      byKey.set(key, {
        id: key,
        name,
        logo: attrs['tvg-logo'] || null,
        country,
        group: attrs['group-title'] || '',
        tvgId,
        mainRank: rank,
        streams: [stream],
      });
    }
  }
  return [...byKey.values()];
}

/** Lien direct vers un flux HLS : une seule chaîne */
export function singleStreamChannel(url, name, opt) {
  let title = name;
  if (!title) {
    try {
      const u = new URL(url);
      const stem = u.pathname.split('/').pop().replace(/\.[^.]+$/, '');
      title = u.hostname + (['', 'index', 'playlist', 'master'].includes(stem) ? '' : ' · ' + stem);
    } catch {
      title = 'Flux';
    }
  }
  return {
    id: `other/${channelKey(title)}`,
    name: title,
    logo: null,
    country: 'other',
    group: '',
    tvgId: '',
    mainRank: null,
    streams: [{ url, ua: null, ref: null, geo: false, origin: opt.origin, sourceId: opt.sourceId,
      fromUser: !!opt.fromUser, height: null }],
  };
}
