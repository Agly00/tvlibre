// Pays de sortie de la connexion (IP vue par les serveurs) : Wi-Fi suisse = CH, VPN France = FR…

const listeners = new Set();
let state = { country: null, org: null, checkedAt: 0, checking: false };
let inflight = null;

export function exitLocation() {
  return state;
}

export function onExitChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function exitLabel() {
  if (!state.country) return state.checking ? 'Sortie : vérification…' : 'Sortie : inconnue';
  return `Sortie : ${state.country}${state.org ? ' · ' + state.org : ''}`;
}

async function getJSON(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 6000);
  try {
    const r = await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

export function refreshExit(force = false) {
  if (!force && Date.now() - state.checkedAt < 60e3 && state.country) return Promise.resolve(state);
  if (inflight) return inflight;
  state = { ...state, checking: true };
  inflight = (async () => {
    let country = null;
    let org = null;
    try {
      const j = await getJSON('https://ipinfo.io/json');
      country = (j.country || '').toUpperCase() || null;
      org = j.org ? j.org.replace(/^AS\d+\s+/, '') : null;
    } catch {
      try {
        const j = await getJSON('https://api.country.is');
        country = (j.country || '').toUpperCase() || null;
      } catch { /* hors ligne */ }
    }
    const changed = country && country !== state.country;
    state = { country: country || state.country, org: country ? org : state.org, checkedAt: Date.now(), checking: false };
    inflight = null;
    if (changed) listeners.forEach((fn) => fn(state));
    return state;
  })();
  return inflight;
}

// Changement de réseau probable : retour en ligne, retour dans l'app, puis toutes les 5 min
window.addEventListener('online', () => setTimeout(() => refreshExit(true), 2000));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshExit(true);
});
setInterval(() => {
  if (document.visibilityState === 'visible') refreshExit(true);
}, 5 * 60e3);
