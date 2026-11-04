// Santé des flux : quarantaine avec remise à l'essai, mémoire du dernier flux fonctionnel
// par chaîne et par pays de sortie.
import { getPref, setPref } from './store.js';

const BACKOFF = [30 * 60e3, 3 * 3600e3, 24 * 3600e3, 72 * 3600e3]; // 30 min, 3 h, 24 h, 72 h

export const REASONS = {
  network: 'Connexion impossible',
  timeout: 'Délai dépassé',
  audioOnly: 'Son sans image',
  format: 'Format non lisible',
  refused: 'Refusé (souvent géoblocage)',
  other: 'Erreur de lecture',
};

let entries = getPref('health', {});   // url → { status, failures, lastSuccess, lastFailure, reason, detail, nextRetry, height, channel, origin }
let preferred = getPref('preferred', {}); // "idChaîne|PAYS" ou "idChaîne" → url

function save() {
  setPref('health', entries);
  setPref('preferred', preferred);
}

export function health(url) {
  return entries[url];
}

export function recordSuccess(stream, channel, height, exitCountry) {
  const h = entries[stream.url] || {};
  entries[stream.url] = {
    ...h,
    status: 'ok',
    failures: 0,
    reason: null,
    detail: null,
    nextRetry: null,
    lastSuccess: Date.now(),
    height: height || h.height || null,
    channel: channel.name,
    origin: stream.origin,
  };
  preferred[channel.id] = stream.url;
  if (exitCountry) preferred[`${channel.id}|${exitCountry}`] = stream.url;
  save();
}

export function recordFailure(stream, channel, reason, detail) {
  const h = entries[stream.url] || { failures: 0 };
  const failures = (h.failures || 0) + 1;
  const delay = reason === 'format' ? BACKOFF[BACKOFF.length - 1] : BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)];
  entries[stream.url] = {
    ...h,
    status: 'quarantined',
    failures,
    reason,
    detail: detail || null,
    lastFailure: Date.now(),
    nextRetry: Date.now() + delay,
    channel: channel.name,
    origin: stream.origin,
  };
  for (const k of Object.keys(preferred)) {
    if ((k === channel.id || k.startsWith(channel.id + '|')) && preferred[k] === stream.url) delete preferred[k];
  }
  save();
}

export function isDue(url) {
  const h = entries[url];
  if (!h || h.status === 'ok') return true;
  return (h.nextRetry || 0) <= Date.now();
}

export function preferredStream(channelId, exitCountry) {
  return (exitCountry && preferred[`${channelId}|${exitCountry}`]) || preferred[channelId] || null;
}

export function hasPreferred(channelId) {
  return Object.keys(preferred).some((k) => k === channelId || k.startsWith(channelId + '|'));
}

export function reactivate(url) {
  const h = entries[url];
  if (!h) return;
  entries[url] = { ...h, status: 'ok', failures: 0, reason: null, detail: null, nextRetry: null };
  save();
}

export function reactivateAll() {
  for (const url of Object.keys(entries)) if (entries[url].status !== 'ok') reactivate(url);
}

export function quarantined() {
  return Object.entries(entries)
    .filter(([, h]) => h.status !== 'ok')
    .map(([url, h]) => ({ url, ...h }))
    .sort((a, b) => (a.channel || '').localeCompare(b.channel || ''));
}

export function knownHeight(stream) {
  return entries[stream.url]?.height || stream.height || null;
}
