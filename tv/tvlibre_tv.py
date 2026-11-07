#!/usr/bin/env python3
"""TV Libre pour la télé : interface plein écran sur le Raspberry branché en HDMI.

Prototype. L'interface (QML) reprend l'organisation de l'app iPhone : Direct, Films, Séries, Enregistrés.
La vidéo est décodée par le Raspberry lui-même (mpv, décodage matériel), le son part en HDMI.
Pilotage : télécommande de la télé (HDMI-CEC), clavier, ou iPhone (petit serveur de commandes sur le port 8091).

Lancement :
  python3 tvlibre_tv.py                 # avec /etc/tvlibre/tv.json (serveur, identifiant, mot de passe)
  python3 tvlibre_tv.py --demo          # catalogue fictif, sans compte
  python3 tvlibre_tv.py --demo --shot sortie.png --page films   # capture d'écran (sans écran, sans vidéo)
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

import lineup

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("TVLIBRE_TV_CONFIG", "/etc/tvlibre/tv.json")
DATA = os.path.expanduser(os.environ.get("TVLIBRE_TV_DATA", "~/.local/share/tvlibre-tv"))
RELAY = "http://127.0.0.1:8090"
UA = "Mozilla/5.0 (TV Libre)"


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


# ---------------------------------------------------------------- compte Xtream

class Xtream:
    def __init__(self, server, user, password):
        self.server = server.rstrip("/")
        self.user, self.password = user, password

    def api(self, action=None, **kw):
        q = {"username": self.user, "password": self.password}
        if action:
            q["action"] = action
        q.update({k: str(v) for k, v in kw.items()})
        return fetch_json("%s/player_api.php?%s" % (self.server, urllib.parse.urlencode(q)))

    def live_url(self, sid):
        return "%s/live/%s/%s/%s.ts" % (self.server, self.user, self.password, sid)

    def movie_url(self, sid, ext):
        return "%s/movie/%s/%s/%s.%s" % (self.server, self.user, self.password, sid, ext or "mp4")

    def episode_url(self, sid, ext):
        return "%s/series/%s/%s/%s.%s" % (self.server, self.user, self.password, sid, ext or "mp4")

    tz = None          # fuseau du serveur : les adresses de replay sont exprimées dans son heure

    def timeshift_url(self, sid, start, stop):
        """Replay d'une émission (heure du serveur), 5 min de marge"""
        try:
            from zoneinfo import ZoneInfo
            import datetime
            t = datetime.datetime.fromtimestamp(start, ZoneInfo(self.tz)) if self.tz else datetime.datetime.fromtimestamp(start)
        except Exception:
            import datetime
            t = datetime.datetime.fromtimestamp(start)
        minutes = int((stop - start) / 60) + 5
        return "%s/timeshift/%s/%s/%d/%s/%s.ts" % (self.server, self.user, self.password, minutes, t.strftime("%Y-%m-%d:%H-%M"), sid)


# ---------------------------------------------------------------- reprise de lecture

class WatchStore:
    def __init__(self):
        self.path = os.path.join(DATA, "watch.json")
        try:
            self.items = json.load(open(self.path))
        except Exception:
            self.items = {}
        self.lock = threading.Lock()

    def get(self, key):
        return self.items.get(key)

    def save(self, key, entry):
        with self.lock:
            self.items[key] = entry
            os.makedirs(DATA, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.items, f)
            os.replace(tmp, self.path)

    def in_progress(self, kind):
        out = [v for v in self.items.values() if v.get("kind") == kind and 60 < v.get("pos", 0) < v.get("dur", 1) * 0.95]
        return sorted(out, key=lambda v: -v.get("t", 0))


WATCH = WatchStore()


# ---------------------------------------------------------------- catalogue

class Catalog(QObject):
    changed = Signal()
    epgReady = Signal(str)
    status = Signal(str)

    def __init__(self, xt, demo=False):
        super().__init__()
        self.xt, self.demo = xt, demo
        self._groups, self._channels, self._flat = [], {}, []
        self._movie_rows, self._series_rows = [], []
        self._epg = {}
        self._guides = {}
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._loading = True
        threading.Thread(target=self._load, daemon=True).start()

    # -- chargement (en fond) --
    def _load(self):
        try:
            if self.demo:
                self._load_demo()
            else:
                self._load_xtream()
        except Exception as e:
            self.status.emit("Catalogue injoignable : %s" % str(e)[:80])
        self._loading = False
        self.changed.emit()
        self.epgReady.emit("*")

    def _load_xtream(self):
        try:
            self.xt.tz = ((self.xt.api() or {}).get("server_info") or {}).get("timezone")
        except Exception:
            pass
        cats = {str(c["category_id"]): c.get("category_name") or "" for c in (self.xt.api("get_live_categories") or [])}
        streams = self.xt.api("get_live_streams") or []
        flat = []
        for st in streams:
            name = st.get("name") or ""
            flat.append({"name": name, "country": lineup.country_of(cats.get(str(st.get("category_id")), ""), name),
                         "xtream": True, "id": str(st.get("stream_id")), "logo": st.get("stream_icon") or "",
                         "archive_days": int(st.get("tv_archive_duration") or 0) if str(st.get("tv_archive")) == "1" else 0})
        self._set_lineup(lineup.build(flat))
        self.changed.emit()
        vcats = {str(c["category_id"]): c.get("category_name") for c in (self.xt.api("get_vod_categories") or [])}
        vods = self.xt.api("get_vod_streams") or []
        rows = {}
        for v in vods:
            rows.setdefault(str(v.get("category_id")), []).append({
                "id": str(v.get("stream_id")), "name": v.get("name") or "", "poster": v.get("stream_icon") or "",
                "ext": v.get("container_extension") or "mp4", "kind": "movie", "added": int(v.get("added") or 0)})
        recent = sorted(sum(rows.values(), []), key=lambda m: -m["added"])[:40]
        self._movie_rows = [{"title": "Ajoutés récemment", "items": recent}] + [
            {"title": vcats.get(k, "Films"), "items": v[:40]} for k, v in list(rows.items())[:12]]
        scats = {str(c["category_id"]): c.get("category_name") for c in (self.xt.api("get_series_categories") or [])}
        series = self.xt.api("get_series") or []
        srows = {}
        for s in series:
            srows.setdefault(str(s.get("category_id")), []).append({
                "id": str(s.get("series_id")), "name": s.get("name") or "", "poster": s.get("cover") or "", "kind": "series"})
        self._series_rows = [{"title": scats.get(k, "Séries"), "items": v[:40]} for k, v in list(srows.items())[:12]]

    def _load_demo(self):
        rnd = random.Random(4)
        fake = []
        n = 0
        for b in lineup.DEF["main"] + lineup.DEF["after_themes"]:
            cc = {"suisse": "ch", "uk": "uk", "us": "us"}.get(b["id"], "fr")
            for e in b["channels"]:
                n += 1
                fake.append({"name": e["name"], "country": cc, "xtream": True, "id": "d%d" % n, "logo": "", "archive_days": 7})
        for nm in ["Canal+ Sport", "beIN Sports 1", "Eurosport 1", "Planète+", "Ushuaïa TV", "Disney Channel", "Piwi+",
                   "France 24", "Euronews", "TCM Cinéma", "OCS Max", "MTV Hits", "Téva", "AB1", "KTO"]:
            n += 1
            fake.append({"name": nm, "country": "fr", "xtream": True, "id": "d%d" % n, "logo": "", "archive_days": 7})
        self._set_lineup(lineup.build(fake), rnd)
        words = ["Le dernier", "Nuit", "Retour", "Horizon", "Les ombres", "Rivière", "Grand nord", "L'été", "Silence",
                 "Le passage", "Cœur", "Frontière", "Lumière", "Le phare", "Vertige"]
        def film(i, k):
            return {"id": "%s%d" % (k, i), "name": ("%s %s" % (rnd.choice(words), rnd.choice(["noir", "perdu", "", "d'avril", "rouge"]))).strip(),
                    "poster": "", "ext": "mkv", "kind": k, "hue": rnd.random(), "progress": 0}
        self._movie_rows = [{"title": t, "items": [film(i + 10 * j, "movie") for i in range(14)]}
                            for j, t in enumerate(["Ajoutés récemment", "Action", "Comédie", "Drame", "Documentaires"])]
        self._movie_rows[0]["items"][0]["progress"] = 0.42
        self._movie_rows[0]["items"][2]["progress"] = 0.8
        self._series_rows = [{"title": t, "items": [film(i + 10 * j, "series") for i in range(14)]}
                             for j, t in enumerate(["Nouveautés", "Policier", "Comédie"])]
        for chans in self._channels.values():
            for c in chans:
                self._epg[c["id"]] = {"now": rnd.choice(["Journal", "Magazine", "Film : " + rnd.choice(words), "Série", "Match en direct", "Documentaire"]),
                                      "next": rnd.choice(["Météo", "Jeu", "Talk-show", "Film", "Reportage"]),
                                      "start": "20:30", "end": "22:10", "progress": rnd.random()}

    def _set_lineup(self, sections, rnd=None):
        """Grille TV Libre → groupes et chaînes numérotées comme une télé (1, 2, 3…)"""
        self._groups, self._channels, self._flat = [], {}, []
        num = 0
        for gi, (title, chans) in enumerate(sections):
            lst = []
            for c in chans:
                num += 1
                st = [{"id": x["id"], "archive_days": x.get("archive_days", 0)} for x in c["streams"] if x.get("id")]
                if not st:
                    continue
                ch = {"id": st[0]["id"], "streams": st, "name": c["name"], "logo": c.get("logo", ""), "num": num,
                      "kind": "live", "group": title, "archive": max(x["archive_days"] for x in st),
                      "hue": rnd.random() if rnd else 0.6}
                lst.append(ch)
                self._flat.append(ch)
            self._channels[str(gi)] = lst
            self._groups.append({"id": str(gi), "name": title, "count": len(lst)})
        self.changed.emit()

    # -- accès depuis l'interface --
    @Property(bool, notify=changed)
    def loading(self):
        return self._loading

    @Property("QVariantList", notify=changed)
    def groups(self):
        return self._groups

    @Slot(int, result="QVariantList")
    def channels(self, gi):
        if 0 <= gi < len(self._groups):
            return self._channels.get(self._groups[gi]["id"], [])
        return []

    @Property("QVariantList", notify=changed)
    def movieRows(self):
        cont = [dict(v["item"], progress=v["pos"] / max(1, v["dur"])) for v in WATCH.in_progress("movie") if "item" in v]
        return ([{"title": "En cours", "items": cont}] if cont else []) + self._movie_rows

    @Property("QVariantList", notify=changed)
    def seriesRows(self):
        return self._series_rows

    @Slot(str, result="QVariantMap")
    def epg(self, sid):
        """Programme en cours et suivant (chargé en fond la première fois)"""
        if sid in self._epg or self.demo:
            return self._epg.get(sid, {})
        self._epg[sid] = {}
        if not self.demo and self.xt:
            threading.Thread(target=self._load_epg, args=(sid,), daemon=True).start()
        return {}

    def _load_epg(self, sid):
        import base64
        try:
            d = self.xt.api("get_short_epg", stream_id=sid, limit=2)
            lst = d.get("epg_listings", []) if isinstance(d, dict) else []
            def title(e):
                try:
                    return base64.b64decode(e.get("title", "")).decode("utf-8", "replace")
                except Exception:
                    return e.get("title", "")
            now = lst[0] if lst else {}
            out = {"now": title(now) if now else "", "next": title(lst[1]) if len(lst) > 1 else ""}
            if now.get("start_timestamp"):
                a, b = int(now["start_timestamp"]), int(now["stop_timestamp"])
                out.update(start=time.strftime("%H:%M", time.localtime(a)), end=time.strftime("%H:%M", time.localtime(b)),
                           progress=max(0.0, min(1.0, (time.time() - a) / max(1, b - a))))
            self._epg[sid] = out
            self.epgReady.emit(sid)
        except Exception:
            pass

    @Property("QVariantList", notify=changed)
    def allChannels(self):
        return self._flat

    guideReady = Signal(str)

    @Slot(str, result="QVariantList")
    def guide(self, sid):
        """Programmes d'une chaîne (passés et à venir), demandés au serveur la première fois"""
        g = self._guides.get(sid)
        if g is not None:
            return g
        self._guides[sid] = []
        if self.demo:
            self._guides[sid] = self._fake_guide(sid)
            return self._guides[sid]
        self._pool.submit(self._load_guide, sid)
        return []

    def _load_guide(self, sid):
        import base64
        try:
            d = self.xt.api("get_simple_data_table", stream_id=sid) or {}
            now = time.time()
            ch = next((c for c in self._flat if c["id"] == sid), {})
            days = ch.get("archive", 0)
            out = []
            for e in d.get("epg_listings", []) if isinstance(d, dict) else []:
                try:
                    a, b = int(e["start_timestamp"]), int(e["stop_timestamp"])
                except Exception:
                    continue
                def dec(v):
                    try:
                        return base64.b64decode(v or "").decode("utf-8", "replace")
                    except Exception:
                        return v or ""
                arch = days > 0 and b < now and a > now - days * 86400 and str(e.get("has_archive", "1")) != "0"
                out.append({"start": a, "stop": b, "title": dec(e.get("title")), "desc": dec(e.get("description")), "archive": arch})
            out.sort(key=lambda x: x["start"])
            self._guides[sid] = out
            self.guideReady.emit(sid)
        except Exception:
            pass

    def _fake_guide(self, sid):
        rnd = random.Random(sid)
        t = (int(time.time()) // 1800) * 1800 - 6 * 3600
        out = []
        titles = ["Journal", "Magazine", "Film", "Série", "Match en direct", "Documentaire", "Jeu", "Météo", "Talk-show", "Reportage"]
        while t < time.time() + 12 * 3600:
            d = rnd.choice([1800, 1800, 3600, 5400, 2700])
            out.append({"start": t, "stop": t + d, "title": rnd.choice(titles), "desc": "", "archive": t + d < time.time()})
            t += d
        return out

    @Slot(str, float, float, str, result=str)
    def record(self, sid, start, stop, title):
        """Enregistrement par le relais (clé USB) : à venir = à l'heure de diffusion, passé = depuis le replay"""
        if self.demo or not self.xt:
            return "démonstration"
        ch = next((c for c in self._flat if c["id"] == sid), {})
        try:
            if stop > time.time():
                q = {"u": self.xt.live_url(sid), "title": title, "channel": ch.get("name", ""), "start": int(start), "stop": int(stop)}
                path = "/rec/schedule"
            else:
                q = {"u": self.xt.timeshift_url(sid, start, stop), "title": title, "channel": ch.get("name", ""),
                     "start": int(start), "duration": int(stop - start),
                     "expires": int(start + ch.get("archive", 7) * 86400)}
                path = "/rec/add"
            d = fetch_json(RELAY + path + "?" + urllib.parse.urlencode(q), timeout=20)
            return "Enregistrement prévu" if d.get("id") else (d.get("error") or "refusé")
        except Exception as e:
            return "Raspberry : %s" % str(e)[:60]

    @Slot(str, result="QVariantList")
    def episodes(self, series_id):
        """Épisodes d'une série, toutes saisons à la suite"""
        if self.demo:
            return [{"id": "%se%d" % (series_id, i), "name": "Épisode %d" % (i + 1), "season": 1 + i // 8, "episode": 1 + i % 8,
                     "ext": "mkv", "kind": "episode"} for i in range(16)]
        info = self.xt.api("get_series_info", series_id=series_id) or {}
        out = []
        for season, eps in sorted((info.get("episodes") or {}).items(), key=lambda kv: int(kv[0])):
            for e in eps:
                out.append({"id": str(e.get("id")), "name": e.get("title") or "", "season": int(season),
                            "episode": int(e.get("episode_num") or 0), "ext": e.get("container_extension") or "mp4", "kind": "episode"})
        return out


# ---------------------------------------------------------------- lecture (mpv)

class Player(QObject):
    streamFailed = Signal()
    """Une seule lecture à la fois : le compte n'accepte qu'une connexion. Le relais est prévenu (/busy) pour que
    ses téléchargements de fond se mettent en pause pendant qu'on regarde."""
    changed = Signal()
    tick = Signal()

    def __init__(self, xt, demo=False):
        super().__init__()
        self.xt, self.demo = xt, demo
        self.mpv = None
        self._item = None
        self._pos = self._dur = 0.0
        self._paused = False
        self._playing = False
        if not demo:
            import mpv
            self.mpv = mpv.MPV(
                vo="libmpv",
                hwdec="drm,drm-copy,v4l2m2m-copy,auto-safe",       # circuits vidéo du Raspberry
                cache="yes", demuxer_max_bytes="150MiB", demuxer_readahead_secs="30",
                network_timeout="20", user_agent=UA,
                slang="fr,fre,fra", alang="fr,fre,fra",
                keep_open="no", idle="yes", input_default_bindings=False, osc=False,
                log_handler=lambda lvl, comp, msg: print("[mpv]", comp, msg.strip()) if lvl in ("error", "warn") or "hardware decoding" in msg else None)
            @self.mpv.property_observer("time-pos")
            def _pos(_n, v):
                self._pos = v or 0.0
            @self.mpv.property_observer("duration")
            def _dur(_n, v):
                self._dur = v or 0.0
            @self.mpv.property_observer("pause")
            def _pause(_n, v):
                self._paused = bool(v)

            @self.mpv.event_callback("end-file")
            def _end(ev):
                try:
                    reason = ev.data.reason if hasattr(ev, "data") else ev["event"]["reason"]
                except Exception:
                    reason = None
                if str(reason).lower().endswith("error") or reason == 4:
                    self.streamFailed.emit()          # (fil de mpv : relayé au fil de l'interface)
        self.streamFailed.connect(self._next_stream)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(1000)
        self._last_busy = 0.0

    def _on_tick(self):
        if not self._playing:
            return
        if self.demo:
            self._pos += 1
            self._dur = self._dur or 5400
        self.tick.emit()
        now = time.time()
        if now - self._last_busy > 60:
            self._last_busy = now
            threading.Thread(target=self._busy, daemon=True).start()
        it = self._item
        if it and it.get("kind") in ("movie", "episode") and int(self._pos) % 10 == 0 and self._dur > 0:
            WATCH.save(it["kind"] + ":" + it["id"], {"kind": it["kind"], "item": it, "pos": self._pos, "dur": self._dur, "t": now})

    def _busy(self):
        try:
            urllib.request.urlopen(RELAY + "/busy", timeout=3).read()
        except Exception:
            pass

    def _url(self, it):
        if self.demo or not self.xt:
            return None
        if it["kind"] == "replay":
            return self.xt.timeshift_url(it["id"], it["start"], it["stop"])
        if it["kind"] == "live":
            return self.xt.live_url(it["id"])
        if it["kind"] == "movie":
            return self.xt.movie_url(it["id"], it.get("ext"))
        return self.xt.episode_url(it["id"], it.get("ext"))

    @Slot("QVariantMap")
    def play(self, it):
        it = dict(it)
        self._item, self._playing, self._paused = it, True, False
        self._pos, self._dur = 0.0, 0.0
        start = 0.0
        if it["kind"] in ("movie", "episode"):
            w = WATCH.get(it["kind"] + ":" + it["id"])
            if w and 60 < w.get("pos", 0) < w.get("dur", 1) * 0.95:
                start = w["pos"] - 5
        url = self._url(it)
        if url and self.mpv:
            self.mpv.play(url) if not start else self.mpv.loadfile(url, start="%.1f" % start)
            self.mpv.pause = False
        elif self.demo:
            self._pos = start
        threading.Thread(target=self._busy, daemon=True).start()
        self.changed.emit()

    @Slot("QVariantMap", float, float, str)
    def playReplay(self, ch, start, stop, title):
        """Revoir une émission passée (replay du serveur)"""
        it = {"kind": "replay", "id": ch["id"], "name": "%s · %s" % (title, ch.get("name", "")), "start": start, "stop": stop,
              "logo": ch.get("logo", ""), "hue": ch.get("hue", 0.6)}
        self.play(it)

    def _next_stream(self):
        """Flux en panne : le suivant de la même chaîne (Xtream d'abord, repli ensuite)"""
        it = self._item or {}
        st = it.get("streams") or []
        tried = it.setdefault("_tried", 1)
        if it.get("kind") == "live" and tried < len(st):
            it["_tried"] = tried + 1
            it["id"] = st[tried]["id"]
            if self.mpv:
                self.mpv.play(self.xt.live_url(it["id"]))

    @Slot()
    def stop(self):
        if self.mpv:
            self.mpv.command("stop")
        self._playing = False
        self.changed.emit()

    @Slot()
    def togglePause(self):
        if self.mpv:
            self.mpv.pause = not self.mpv.pause
        else:
            self._paused = not self._paused
        self.changed.emit()

    @Slot(float)
    def seekBy(self, s):
        if self.mpv:
            self.mpv.seek(s, reference="relative", precision="keyframes")
        else:
            self._pos = max(0, self._pos + s)
        self.tick.emit()

    @Property("QVariantMap", notify=changed)
    def item(self):
        return self._item or {}

    @Property(bool, notify=changed)
    def playing(self):
        return self._playing

    @Property(bool, notify=changed)
    def paused(self):
        return self._paused

    @Property(float, notify=tick)
    def position(self):
        return self._pos

    @Property(float, notify=tick)
    def duration(self):
        return self._dur


# ---------------------------------------------------------------- image vidéo dans l'interface

def register_video_item():
    """Élément QML « Video » : mpv dessine dans l'interface (rendu OpenGL), menus par-dessus la vidéo"""
    import ctypes
    import mpv
    from PySide6.QtGui import QOpenGLContext
    from PySide6.QtOpenGL import QOpenGLFramebufferObject
    from PySide6.QtQml import qmlRegisterType
    from PySide6.QtQuick import QQuickFramebufferObject

    def get_proc_address(_ctx, name):
        ctx = QOpenGLContext.currentContext()
        addr = ctx.getProcAddress(name.decode("utf-8")) if ctx else 0
        return int(addr or 0)

    proc = mpv.MpvGlGetProcAddressFn(get_proc_address)

    class Renderer(QQuickFramebufferObject.Renderer):
        def __init__(self, item):
            super().__init__()
            self.item = item
            self.ctx = None
            self.keep = []

        def createFramebufferObject(self, size):
            # Qt devient propriétaire de l'image ; on garde l'enveloppe Python, sinon elle est libérée aussitôt
            fbo = QOpenGLFramebufferObject(size)
            self.keep.append(fbo)
            return fbo

        def render(self):
            p = self.item.player
            if p is None or p.mpv is None:
                return
            if self.ctx is None:
                self.ctx = mpv.MpvRenderContext(p.mpv, "opengl", opengl_init_params={"get_proc_address": proc})
                self.ctx.update_cb = self.item.frameReady.emit       # appelé hors du fil de l'interface
            fbo = self.framebufferObject()
            self.ctx.render(flip_y=False, opengl_fbo={"w": fbo.width(), "h": fbo.height(), "fbo": fbo.handle()})

    class Video(QQuickFramebufferObject):
        frameReady = Signal()
        player = None

        def __init__(self, parent=None):
            super().__init__(parent)
            self.frameReady.connect(self.update)
            self.setMirrorVertically(True)

        def createRenderer(self):
            self._renderer = Renderer(self)           # gardé : sinon Python le libère sous les pieds de Qt
            return self._renderer

    qmlRegisterType(Video, "TVLibre", 1, 0, "Video")
    return Video


# ---------------------------------------------------------------- télécommande par l'iPhone

class Remote(QObject):
    key = Signal(str)


def serve_remote(remote, port=8091):
    """GET /key?k=up|down|left|right|ok|back|play|menu : les mêmes touches que la télécommande"""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path == "/key" and q.get("k") in ("up", "down", "left", "right", "ok", "back", "play", "menu"):
                remote.key.emit(q["k"])
                body, code = b'{"ok":true}', 200
            else:
                body, code = b'{"error":"inconnu"}', 404
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- démarrage

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="catalogue fictif, sans compte ni vidéo")
    ap.add_argument("--shot", help="capture d'écran PNG puis quitte (avec --demo)")
    ap.add_argument("--page", default="direct", help="page de la capture : direct, films, series, lecture")
    ap.add_argument("--keys", default="", help="touches jouées avant la capture (essai), ex. right,down,ok")
    args = ap.parse_args()

    if args.shot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Rendu dans le fil principal : avec mpv, le rendu en fil séparé pouvait se bloquer (vu en essai)
    os.environ.setdefault("QSG_RENDER_LOOP", "basic")
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
    if not args.demo:
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)   # mpv dessine en OpenGL
    app = QGuiApplication(sys.argv)
    app.setApplicationName("TV Libre")

    xt = None
    if not args.demo:
        cfg = json.load(open(CONFIG))
        xt = Xtream(cfg["server"], cfg["username"], cfg["password"])
    catalog = Catalog(xt, demo=args.demo)
    player = Player(xt, demo=args.demo)
    remote = Remote()
    if not args.shot:
        serve_remote(remote)

    if not args.demo:
        Video = register_video_item()
        Video.player = player

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("catalog", catalog)
    ctx.setContextProperty("player", player)
    ctx.setContextProperty("remote", remote)
    ctx.setContextProperty("demoMode", args.demo)
    ctx.setContextProperty("startPage", args.page)
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "qml", "Main.qml")))
    if not engine.rootObjects():
        sys.exit("interface introuvable ou invalide")

    if args.shot:
        win = engine.rootObjects()[0]
        keys = [k for k in args.keys.split(",") if k]
        def shoot():
            win.grabWindow().save(args.shot)
            app.quit()
        for i, k in enumerate(keys):
            QTimer.singleShot(1500 + 250 * i, lambda k=k: remote.key.emit(k))
        QTimer.singleShot(2500 + 250 * len(keys), shoot)
    code = app.exec()
    sys.stdout.flush()
    os._exit(code)            # sans attendre les demandes au serveur encore en route


if __name__ == "__main__":
    main()
