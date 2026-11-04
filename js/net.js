// Porte réseau unique : choisit le transport d'un flux (direct ou via le relais optionnel).
// Contrat du relais (à construire plus tard, par ex. sur le Raspberry) :
//   GET {relais}/hls?url=<adresse encodée>
//   → renvoie la playlist en https, avec en-têtes CORS, adresses internes réécrites vers le relais,
//     sortie réseau par une IP française.
import { getPref } from './store.js';

export function relayBase() {
  const r = (getPref('relay', '') || '').trim().replace(/\/+$/, '');
  return /^https:\/\//.test(r) ? r : '';
}

/**
 * Adresse à donner au lecteur.
 * @param {object} stream
 * @param {{ needsCountry: string|null, exitCountry: string|null }} ctx
 */
export function playbackURL(stream, ctx = {}) {
  const relay = relayBase();
  if (!relay) return stream.url;
  const insecure = location.protocol === 'https:' && stream.url.startsWith('http:');
  const geoMismatch = ctx.needsCountry === 'FR' && ctx.exitCountry && ctx.exitCountry !== 'FR';
  if (insecure || geoMismatch || stream.ua || stream.ref) {
    return `${relay}/hls?url=${encodeURIComponent(stream.url)}`;
  }
  return stream.url;
}

export function viaRelay(url) {
  const relay = relayBase();
  return !!relay && url.startsWith(relay + '/');
}

/** Téléchargement texte avec délai, depuis une page web (soumis au CORS) */
export async function fetchText(url, timeout = 20000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ctrl.signal, cache: 'no-cache' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    if (/\.gz($|\?)/.test(url) && 'DecompressionStream' in window) {
      const ds = r.body.pipeThrough(new DecompressionStream('gzip'));
      return await new Response(ds).text();
    }
    return await r.text();
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('délai dépassé');
    if (e instanceof TypeError) throw new Error('accès refusé par le serveur (CORS) ou hors ligne');
    throw e;
  } finally {
    clearTimeout(t);
  }
}
