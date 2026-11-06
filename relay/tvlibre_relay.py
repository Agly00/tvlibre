#!/usr/bin/env python3
"""TV Libre, relais Raspberry Pi.

Le Raspberry joint le serveur IPTV à travers NordVPN et sert à l'iPhone et à l'Apple TV :
  /status                  santé (température, alimentation, VPN, sessions)
  /api?u=URL               requête au serveur (catalogue, guide), relayée
  /live/open?u=URL         direct : ffmpeg reçoit le flux ts et le découpe en HLS (reconnexion automatique)
  /vod/open?u=URL&ext=mkv  film : mp4 relayé tel quel, mkv réemballé en HLS fMP4, ts découpé
  /s/<id>/<fichier>        fichiers d'une session
  /close?id=<id>           fin de session
  /probe?u=URL             le serveur accepte-t-il les plages d'octets (replay lisible comme un film) ?
  /rec/add, /rec/schedule, /rec/list, /rec/stop?id=, /rec/delete?id=, /rec/file/<id>.ts   enregistrements (clé USB)
  /busy                    l'iPhone lit en direct depuis la 5G : les enregistrements de replay se mettent en pause
  /wg/client               configuration WireGuard de l'iPhone (donnée seulement sur le réseau de la maison)
Une seule lecture à la fois vers le serveur (abonnement limité à une connexion).
Bibliothèque standard Python uniquement, plus ffmpeg.
"""
import json, math, os, re, shutil, socket, struct, subprocess, sys, threading, time, uuid
import urllib.request, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

VERSION = "1.3.2"
PORT = int(os.environ.get("TVLIBRE_PORT", "8090"))
RUN = os.environ.get("TVLIBRE_RUN", "/run/tvlibre")
UA = "VLC/3.0.21 LibVLC/3.0.21"
IDLE_STOP = {"live": 120}   # s sans demande du lecteur : session arrêtée (libère la connexion du compte)
IDLE_STOP_VOD = 6 * 3600     # film en pause : on garde la session (la télécommande peut rester des heures en pause)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- santé

def sh(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def temperature():
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read().strip()) / 1000
    except Exception:
        return None


def throttled():
    out = sh(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", out)
    if not m:
        return {}
    v = int(m.group(1), 16)
    return {
        "raw": hex(v),
        "undervoltage_now": bool(v & 0x1), "throttled_now": bool(v & 0x4), "soft_temp_limit_now": bool(v & 0x8),
        "undervoltage_since_boot": bool(v & 0x10000), "throttled_since_boot": bool(v & 0x40000),
    }


def vpn_status():
    out = sh(["nordvpn", "status"])
    d = {"raw": out[:400]}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip().lower().replace(" ", "_")] = v.strip()
    d["connected"] = d.get("status", "").lower() == "connected"
    return d


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))      # aucun paquet envoyé : choisit l'interface du réseau local
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------- accès au serveur IPTV

def http_get(url, rng=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if rng:
        req.add_header("Range", rng)
    return urllib.request.urlopen(req, timeout=timeout)


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset("")

    def reset(self, mode):
        with self.lock:
            self.mode, self.bytes, self.secs, self.retries, self.errors, self.bitrate = mode, 0, 0.0, 0, 0, 0.0

    def add(self, n, s):
        with self.lock:
            self.bytes += n
            self.secs += s

    def snapshot(self):
        with self.lock:
            mbps = self.bytes * 8 / self.secs / 1e6 if self.secs > 0 else 0
            return {"mode": self.mode, "download_mbps": round(mbps, 2), "file_mbps": round(self.bitrate, 2),
                    "retries": self.retries, "errors": self.errors}


STATS = Stats()


class RemoteFile:
    """Fichier distant : petites lectures par plage, et lecture continue (une connexion ouverte, réserve devant)."""
    KEEP_BEHIND = 16 << 20
    AHEAD = 160 << 20
    WINDOW = 200 << 20

    def __init__(self, url):
        self.url = url
        self.size = 0
        self.cv = threading.Condition()
        self.buf = bytearray()
        self.start = 0
        self.thread = None
        self.gen = 0             # numéro de connexion (une nouvelle annule l'ancienne)
        self.failure = None
        self.done = False
        self.furthest = 0
        self.closed = False

    def read_range(self, off, n):
        for attempt in range(4):
            try:
                t = time.time()
                with http_get(self.url, "bytes=%d-%d" % (off, off + n - 1)) as r:
                    data = r.read(n)
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr and cr.split("/")[-1].isdigit():
                        self.size = int(cr.split("/")[-1])
                    elif r.status == 200 and off == 0:
                        self.size = int(r.headers.get("Content-Length") or 0) or self.size
                STATS.add(len(data), time.time() - t)
                return bytes(data)
            except Exception as e:
                STATS.retries += 1
                if attempt == 3:
                    STATS.errors += 1
                    raise
                time.sleep(attempt + 1)

    def _run(self, gen, off):
        pos = off
        while True:
            with self.cv:
                if self.closed or gen != self.gen:
                    return
            try:
                with http_get(self.url, "bytes=%d-" % pos, timeout=30) as r:
                    if r.status not in (200, 206) or (r.status == 200 and pos > 0):
                        raise IOError("HTTP %d" % r.status)
                    last = time.time()
                    while True:
                        with self.cv:
                            while not self.closed and gen == self.gen and \
                                    self.start + len(self.buf) - self.furthest > self.AHEAD:
                                self.cv.wait(1)        # réserve pleine : pause
                            if self.closed or gen != self.gen:
                                return
                        chunk = r.read(256 * 1024)
                        now = time.time()
                        if not chunk:
                            with self.cv:
                                self.done = True
                                self.cv.notify_all()
                            return
                        STATS.add(len(chunk), now - last)
                        last = now
                        with self.cv:
                            if gen != self.gen:
                                return
                            self.buf += chunk
                            pos += len(chunk)
                            self.cv.notify_all()
            except Exception as e:
                STATS.retries += 1
                log("flux interrompu, reprise :", e)
                time.sleep(2)               # reprise à la position atteinte

    def _restart(self, off):
        self.gen += 1
        self.buf = bytearray()
        self.start = off
        self.failure = None
        self.done = False
        self.furthest = off
        th = threading.Thread(target=self._run, args=(self.gen, off), daemon=True)
        th.start()
        self.thread = th

    def read_stream(self, off, n, can_seek=True, timeout=60):
        end = min(off + n, self.size) if self.size else off + n
        with self.cv:
            buf_end = self.start + len(self.buf)
            reachable = self.thread is not None and self.start <= off <= buf_end + self.WINDOW
            if not reachable:
                if not can_seek:
                    raise IOError("hors de la réserve")
                self._restart(off)
            self.furthest = max(self.furthest, end)
            self.cv.notify_all()
            deadline = time.time() + timeout
            while self.start + len(self.buf) < end and not self.done:
                if self.closed:
                    raise IOError("fermé")
                if off < self.start:
                    if not can_seek:
                        raise IOError("relancé ailleurs")
                    self._restart(off)
                left = deadline - time.time()
                if left <= 0:
                    raise IOError("délai dépassé")
                self.cv.wait(min(1, left))
            data = bytes(self.buf[off - self.start:end - self.start])
            # élagage derrière la lecture
            drop = off - self.KEEP_BEHIND - self.start
            if drop > (8 << 20):
                del self.buf[:drop]
                self.start += drop
            self.cv.notify_all()
            return data

    def close(self):
        with self.cv:
            self.closed = True
            self.gen += 1
            self.cv.notify_all()


# ---------------------------------------------------------------- mkv → HLS fMP4

def vint(b, p, keep):
    if p >= len(b) or b[p] == 0:
        return None, p, 0
    first = b[p]
    n, mask = 1, 0x80
    while not first & mask:
        n += 1
        mask >>= 1
    if p + n > len(b):
        return None, p, 0
    v = first if keep else first & (mask - 1)
    for i in range(1, n):
        v = (v << 8) | b[p + i]
    return v, p + n, n


def elem(b, p):
    i, q, il = vint(b, p, True)
    if i is None or il > 4:
        return None
    s, r, sl = vint(b, q, False)
    if s is None:
        return None
    if s == (1 << (7 * sl)) - 1:
        s = -1
    return i, s, r


def children(b, start, end):
    p = start
    while p < end:
        e = elem(b, p)
        if e is None:
            break
        i, s, d = e
        if s < 0 or d + s > end:
            break
        yield i, d, s
        p = d + s


def uint(b, d, s):
    v = 0
    for x in b[d:d + s]:
        v = (v << 8) | x
    return v


def flt(b, d, s):
    return struct.unpack(">f", b[d:d + 4])[0] if s == 4 else struct.unpack(">d", b[d:d + 8])[0] if s == 8 else 0.0


SEGMENT, SEEKHEAD, INFO, TRACKS, CUES, CLUSTER = 0x18538067, 0x114D9B74, 0x1549A966, 0x1654AE6B, 0x1C53BB6B, 0x1F43B675


class Track:
    def __init__(self):
        self.num = self.type = self.w = self.h = self.ch = self.defdur = 0
        self.codec, self.priv, self.lang, self.name = "", b"", "und", ""
        self.rate, self.default, self.forced, self.strip, self.compressed = 0.0, True, False, b"", False


def parse_tracks(b):
    out = []
    for i, d, s in children(b, 0, len(b)):
        if i != 0xAE:
            continue
        t = Track()
        for j, dd, ss in children(b, d, d + s):
            v = b[dd:dd + ss]
            if j == 0xD7: t.num = uint(b, dd, ss)
            elif j == 0x83: t.type = uint(b, dd, ss)
            elif j == 0x86: t.codec = v.decode("latin1").rstrip("\0")
            elif j == 0x63A2: t.priv = bytes(v)
            elif j == 0x22B59C: t.lang = v.decode("latin1").rstrip("\0")
            elif j == 0x536E: t.name = v.decode("utf8", "replace").rstrip("\0")
            elif j == 0x23E383: t.defdur = uint(b, dd, ss)
            elif j == 0x88: t.default = uint(b, dd, ss) != 0
            elif j == 0x55AA: t.forced = uint(b, dd, ss) != 0
            elif j == 0xE0:
                for k, d3, s3 in children(b, dd, dd + ss):
                    if k == 0xB0: t.w = uint(b, d3, s3)
                    elif k == 0xBA: t.h = uint(b, d3, s3)
            elif j == 0xE1:
                for k, d3, s3 in children(b, dd, dd + ss):
                    if k == 0xB5: t.rate = flt(b, d3, s3)
                    elif k == 0x9F: t.ch = uint(b, d3, s3)
            elif j == 0x6D80:
                for k, d3, s3 in children(b, dd, dd + ss):
                    if k != 0x6240: continue
                    for l, d4, s4 in children(b, d3, d3 + s3):
                        if l != 0x5034: continue
                        algo, sett = 0, b""
                        for m, d5, s5 in children(b, d4, d4 + s4):
                            if m == 0x4254: algo = uint(b, d5, s5)
                            elif m == 0x4255: sett = bytes(b[d5:d5 + s5])
                        if algo == 3: t.strip = sett
                        else: t.compressed = True
        out.append(t)
    return out


def open_mkv(f):
    head = f.read_range(0, 512 * 1024)
    e = elem(head, 0)
    if not e or e[0] != 0x1A45DFA3:
        raise IOError("pas un mkv")
    e2 = elem(head, e[2] + e[1])
    if not e2 or e2[0] != SEGMENT:
        raise IOError("mkv incomplet")
    seg_start = e2[2]
    pos, info, tracks, cues = {}, None, None, None
    p = seg_start
    while p < len(head):
        e = elem(head, p)
        if e is None or e[0] == CLUSTER or e[1] < 0:
            break
        i, s, d = e
        if d + s <= len(head):
            if i == SEEKHEAD:
                for j, dd, ss in children(head, d, d + s):
                    if j != 0x4DBB: continue
                    sid = spos = None
                    for k, d3, s3 in children(head, dd, dd + ss):
                        if k == 0x53AB: sid = uint(head, d3, s3)
                        elif k == 0x53AC: spos = uint(head, d3, s3)
                    if sid is not None and spos is not None:
                        pos.setdefault(sid, spos)
            elif i == INFO: info = head[d:d + s]
            elif i == TRACKS: tracks = head[d:d + s]
            elif i == CUES: cues = head[d:d + s]
        p = d + s

    def fetch(eid):
        if eid not in pos:
            return None
        off = seg_start + pos[eid]
        h = f.read_range(off, 16)
        ee = elem(h, 0)
        if not ee or ee[0] != eid or ee[1] <= 0:
            return None
        return f.read_range(off + ee[2], ee[1])

    info = info or fetch(INFO)
    tracks = tracks or fetch(TRACKS)
    cues = cues or fetch(CUES)
    if not info or not tracks:
        raise IOError("mkv sans en-tête")
    if not cues:
        raise IOError("fichier sans index : impossible de le découper")
    scale, dur = 1000000, 0.0
    for j, d, s in children(info, 0, len(info)):
        if j == 0x2AD7B1: scale = uint(info, d, s) or 1000000
        elif j == 0x4489: dur = flt(info, d, s)
    ix = {"seg_start": seg_start, "scale": scale, "duration": dur * scale / 1e9, "tracks": parse_tracks(tracks)}
    video = next((t for t in ix["tracks"] if t.type == 1), None)
    pts = []
    for j, d, s in children(cues, 0, len(cues)):
        if j != 0xBB: continue
        tm, trs = None, []
        for k, dd, ss in children(cues, d, d + s):
            if k == 0xB3: tm = uint(cues, dd, ss)
            elif k == 0xB7:
                ct = cp = None
                for l, d3, s3 in children(cues, dd, dd + ss):
                    if l == 0xF7: ct = uint(cues, d3, s3)
                    elif l == 0xF1: cp = uint(cues, d3, s3)
                if cp is not None:
                    trs.append((ct, cp))
        if tm is None: continue
        for ct, cp in trs:
            if video is None or ct == video.num:
                pts.append((tm * scale / 1e9, cp))
    pts.sort()
    segs = []
    for t, cp in pts:
        if segs and (cp == segs[-1][1] or t - segs[-1][0] < 10):
            continue
        segs.append((t, cp))
    if not segs:
        raise IOError("index vide")
    if ix["duration"] <= 0:
        ix["duration"] = segs[-1][0] + 10
    ix["segs"] = segs
    return ix


def frames_of_block(b, p, end):
    tr, q, _ = vint(b, p, False)
    tc = struct.unpack(">h", bytes(b[q:q + 2]))[0]
    flags = b[q + 2]
    q += 3
    lacing = (flags >> 1) & 3
    if lacing == 0:
        return tr, tc, flags, [(q, end)]
    n = b[q] + 1
    q += 1
    sizes = []
    if lacing == 1:
        for _ in range(n - 1):
            s = 0
            while True:
                x = b[q]; q += 1; s += x
                if x != 255: break
            sizes.append(s)
    elif lacing == 3:
        s, q, _ = vint(b, q, False)
        sizes.append(s)
        for _ in range(n - 2):
            raw, q2, ln = vint(b, q, False)
            s = s + raw - ((1 << (7 * ln - 1)) - 1)
            q = q2
            sizes.append(s)
    else:
        each = (end - q) // n
        sizes = [each] * (n - 1)
    out = []
    for s in sizes:
        out.append((q, q + s)); q += s
    out.append((q, end))
    return tr, tc, flags, out


def parse_clusters(b, tracks, scale):
    """-> {piste: [(pts_ns, données, clé, durée_ns)]}"""
    res = {}
    p, ctc = 0, 0

    def emit(blk, key, dur=0):
        tr, tc, fl, frs = blk
        t = tracks.get(tr)
        if t is None: return
        pts = (ctc + tc) * scale
        for a, z in frs:
            res.setdefault(tr, []).append((pts, t.strip + bytes(b[a:z]), key, dur * scale))

    while p < len(b):
        e = elem(b, p)
        if e is None: break
        i, s, d = e
        if i == CLUSTER:
            p = d
            continue
        if s < 0 or d + s > len(b): break
        if i == 0xE7:
            ctc = uint(b, d, s)
        elif i == 0xA3:
            blk = frames_of_block(b, d, d + s)
            emit(blk, bool(blk[2] & 0x80))
        elif i == 0xA0:
            br, ref, dur = None, False, 0
            for j, dd, ss in children(b, d, d + s):
                if j == 0xA1: br = (dd, dd + ss)
                elif j == 0xFB: ref = True
                elif j == 0x9B: dur = uint(b, dd, ss)
            if br:
                emit(frames_of_block(b, br[0], br[1]), not ref, dur)
        p = d + s
    return res


def box(t, c): return struct.pack(">I", 8 + len(c)) + t.encode() + c
def full(t, v, f, c): return box(t, struct.pack(">I", (v << 24) | f) + c)


class Bits:
    def __init__(self, b): self.b, self.p = b, 0
    def r(self, n):
        v = 0
        for _ in range(n):
            byte = self.p >> 3
            v = (v << 1) | (((self.b[byte] >> (7 - (self.p & 7))) & 1) if byte < len(self.b) else 0)
            self.p += 1
        return v


class BitW:
    def __init__(self): self.v, self.n = 0, 0
    def w(self, n, x): self.v = (self.v << n) | (x & ((1 << n) - 1)); self.n += n
    def bytes(self):
        pad = (-self.n) % 8
        return (self.v << pad).to_bytes((self.n + pad) // 8, "big")


AC3_RATES = [48000, 44100, 32000]
AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]
MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def desc(tag, c): return bytes([tag, len(c)]) + c


def esds(oti, dsi):
    dc = bytes([oti, 0x15, 0, 0, 0]) + struct.pack(">II", 0, 0) + (desc(5, dsi) if dsi else b"")
    return full("esds", 0, 0, desc(3, struct.pack(">HB", 1, 0) + desc(4, dc) + desc(6, b"\x02")))


def audio_setup(t, first):
    c = t.codec
    if c == "A_AC3":
        br = Bits(first)
        if br.r(16) != 0x0B77: return None
        br.r(16); fscod = br.r(2); fsc = br.r(6); bsid = br.r(5); bsmod = br.r(3); acmod = br.r(3)
        if (acmod & 1) and acmod != 1: br.r(2)
        if acmod & 4: br.r(2)
        if acmod == 2: br.r(2)
        lfe = br.r(1)
        if fscod > 2: return None
        w = BitW(); w.w(2, fscod); w.w(5, bsid); w.w(3, bsmod); w.w(3, acmod); w.w(1, lfe); w.w(5, fsc >> 1); w.w(5, 0)
        rate, fdur, cfg, fourcc, codecs = AC3_RATES[fscod], 1536, box("dac3", w.bytes()), "ac-3", "ac-3"
    elif c == "A_EAC3":
        br = Bits(first)
        if br.r(16) != 0x0B77: return None
        br.r(2); br.r(3); frmsiz = br.r(11); fscod = br.r(2)
        if fscod == 3:
            rate = [24000, 22050, 16000, 0][br.r(2)]; nblk = 6
        else:
            rate = AC3_RATES[fscod]; nblk = [1, 2, 3, 6][br.r(2)]
        if not rate: return None
        acmod = br.r(3); lfe = br.r(1); bsid = br.r(5)
        size = (frmsiz + 1) * 2
        dep = len(first) > size + 4 and first[size] == 0x0B and first[size + 1] == 0x77 and (first[size + 2] >> 6) == 1
        kbps = size * 8 * rate // (nblk * 256) // 1000
        w = BitW(); w.w(13, kbps); w.w(3, 0)
        w.w(2, fscod); w.w(5, bsid); w.w(1, 0); w.w(1, 0); w.w(3, 0); w.w(3, acmod); w.w(1, lfe); w.w(3, 0)
        w.w(4, 1 if dep else 0)
        if dep: w.w(9, 0x2)
        else: w.w(1, 0)
        fdur, cfg, fourcc, codecs = nblk * 256, box("dec3", w.bytes()), "ec-3", "ec-3"
    elif c.startswith("A_AAC"):
        asc = t.priv
        if len(asc) < 2:
            idx = AAC_RATES.index(int(t.rate)) if int(t.rate) in AAC_RATES else 3
            asc = bytes([(2 << 3) | (idx >> 1), ((idx & 1) << 7) | (max(1, t.ch) << 3)])
        idx = ((asc[0] & 7) << 1) | (asc[1] >> 7)
        if idx >= len(AAC_RATES): return None
        rate, fdur, cfg, fourcc, codecs = AAC_RATES[idx], 1024, esds(0x40, asc), "mp4a", "mp4a.40.%d" % (asc[0] >> 3)
    elif c == "A_MPEG/L3":
        rate = int(t.rate) or 48000
        fdur, cfg, fourcc, codecs = (1152 if rate >= 32000 else 576), esds(0x6B, b""), "mp4a", "mp4a.40.34"
    else:
        return None
    entry = b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 8 + struct.pack(">HHHHI", max(2, t.ch or 2), 16, 0, 0, rate << 16) + cfg
    return {"timescale": rate, "fdur": fdur, "entry": box(fourcc, entry), "codecs": codecs}


def video_setup(t):
    if t.codec == "V_MPEG4/ISO/AVC" and len(t.priv) > 4:
        fourcc, cfg = "avc1", box("avcC", t.priv)
        codecs = "avc1.%02x%02x%02x" % (t.priv[1], t.priv[2], t.priv[3])
    elif t.codec == "V_MPEGH/ISO/HEVC" and len(t.priv) > 22:
        fourcc, cfg, codecs = "hvc1", box("hvcC", t.priv), None
    else:
        return None
    e = b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 16 + struct.pack(">HHIIIH", t.w, t.h, 0x480000, 0x480000, 0, 1) \
        + b"\0" * 32 + struct.pack(">Hh", 0x18, -1) + cfg
    return {"timescale": 90000, "entry": box(fourcc, e), "codecs": codecs}


def init_segment(setup, video, w=0, h=0):
    ftyp = box("ftyp", b"iso6" + struct.pack(">I", 0) + b"iso6cmfcmp41")
    mvhd = full("mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0) + struct.pack(">IH", 0x10000, 0x100) + b"\0" * 10 + MATRIX + b"\0" * 24 + struct.pack(">I", 2))
    tkhd = full("tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 0) + b"\0" * 8 + struct.pack(">hhH", 0, 0, 0 if video else 0x100) + b"\0\0" + MATRIX + struct.pack(">II", w << 16, h << 16))
    mdhd = full("mdhd", 0, 0, struct.pack(">IIII", 0, 0, setup["timescale"], 0) + struct.pack(">HH", 0x55C4, 0))
    hdlr = full("hdlr", 0, 0, b"\0" * 4 + (b"vide" if video else b"soun") + b"\0" * 12 + b"TVLibre\0")
    mh = full("vmhd", 0, 1, b"\0" * 8) if video else full("smhd", 0, 0, b"\0" * 4)
    dinf = box("dinf", full("dref", 0, 0, struct.pack(">I", 1) + full("url ", 0, 1, b"")))
    stbl = box("stbl", full("stsd", 0, 0, struct.pack(">I", 1) + setup["entry"]) + full("stts", 0, 0, b"\0" * 4)
               + full("stsc", 0, 0, b"\0" * 4) + full("stsz", 0, 0, b"\0" * 8) + full("stco", 0, 0, b"\0" * 4))
    trak = box("trak", tkhd + box("mdia", mdhd + hdlr + box("minf", mh + dinf + stbl)))
    mvex = box("mvex", full("trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0)))
    return ftyp + box("moov", mvhd + trak + mvex)


def media_segment(seq, base, samples, video):
    n = len(samples)
    if video:
        flags, ver = 0x1 | 0x100 | 0x200 | 0x400 | 0x800, 1
        body = b"".join(struct.pack(">IIIi", d, len(x), 0x02000000 if k else 0x01010000, c) for d, x, k, c in samples)
    else:
        flags, ver = 0x1 | 0x100 | 0x200, 0
        body = b"".join(struct.pack(">II", d, len(x)) for d, x, k, c in samples)

    def build(off):
        trun = full("trun", ver, flags, struct.pack(">Ii", n, off) + body)
        traf = box("traf", full("tfhd", 0, 0x020000, struct.pack(">I", 1)) + full("tfdt", 1, 0, struct.pack(">Q", max(0, base))) + trun)
        return box("moof", full("mfhd", 0, 0, struct.pack(">I", seq)) + traf)

    m = build(0)
    m = build(len(m) + 8)
    data = b"".join(x for _, x, _, _ in samples)
    return m + struct.pack(">I", 8 + len(data)) + b"mdat" + data


LANG = {"fre": "fr", "fra": "fr", "eng": "en", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt",
        "dut": "nl", "nld": "nl", "jpn": "ja", "kor": "ko", "chi": "zh", "zho": "zh", "ara": "ar", "rus": "ru", "tur": "tr"}
NAMES = {"fr": "Français", "en": "Anglais", "de": "Allemand", "es": "Espagnol", "it": "Italien", "pt": "Portugais",
         "nl": "Néerlandais", "ja": "Japonais", "ko": "Coréen", "zh": "Chinois", "ar": "Arabe", "ru": "Russe", "tr": "Turc"}


def lang2(l):
    l = (l or "und").lower()
    return LANG.get(l, l if len(l) == 2 else "und")


def is_fr(l): return lang2(l) == "fr"


class MKVSession:
    TEXT_SUBS = ("S_TEXT/UTF8", "S_TEXT/ASS", "S_TEXT/SSA", "S_TEXT/WEBVTT", "S_ASS", "S_SSA")

    def __init__(self, url):
        self.f = RemoteFile(url)
        self.ix = open_mkv(self.f)
        tr = self.ix["tracks"]
        self.video = next((t for t in tr if t.type == 1), None)
        if not self.video or self.video.compressed:
            raise IOError("pas d'image lisible")
        self.vset = video_setup(self.video)
        if not self.vset:
            raise IOError("image %s non prise en charge" % self.video.codec)
        all_tracks = {t.num: t for t in tr}
        first = parse_clusters(self.seg_bytes(0, stream=False), all_tracks, self.ix["scale"])
        self.audios = []
        refused = []
        for t in tr:
            if t.type != 2: continue
            s = audio_setup(t, first[t.num][0][1]) if (t.num in first and not t.compressed) else None
            if s: self.audios.append((t, s))
            else: refused.append(t.codec)
        if not self.audios:
            raise IOError("son non pris en charge : " + ", ".join(refused))
        self.audios.sort(key=lambda a: (not is_fr(a[0].lang), not a[0].default))
        self.subs = [t for t in tr if t.type == 17 and not t.compressed and t.codec in self.TEXT_SUBS]
        self.subs.sort(key=lambda t: (not is_fr(t.lang), t.forced))
        self.tracks = {self.video.num: self.video}
        for t, _ in self.audios: self.tracks[t.num] = t
        for t in self.subs: self.tracks[t.num] = t
        self.cache, self.order, self.inflight = {0: {k: v for k, v in first.items() if k in self.tracks}}, [0], {}
        self.lock = threading.Lock()
        STATS.bitrate = (self.f.size * 8 / self.ix["duration"] / 1e6) if self.ix["duration"] > 0 else 0

    @property
    def duration(self): return self.ix["duration"]

    def seg_range(self, i):
        ix = self.ix
        a = ix["seg_start"] + ix["segs"][i][1]
        b = ix["seg_start"] + ix["segs"][i + 1][1] if i + 1 < len(ix["segs"]) else max(a + 1, self.f.size)
        return a, b

    def seg_bytes(self, i, stream=True, can_seek=True):
        a, b = self.seg_range(i)
        return self.f.read_stream(a, b - a, can_seek) if stream else self.f.read_range(a, b - a)

    def seg_dur(self, i):
        s = self.ix["segs"]
        t1 = s[i + 1][0] if i + 1 < len(s) else self.ix["duration"]
        return max(0.1, t1 - s[i][0])

    def frames(self, i, prefetch=False):
        with self.lock:
            if i in self.cache:
                return self.cache[i]
            ev = self.inflight.get(i)
            if ev is None:
                ev = threading.Event()
                self.inflight[i] = ev
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(90)
            with self.lock:
                if i in self.cache:
                    return self.cache[i]
            raise IOError("morceau indisponible")
        try:
            r = parse_clusters(self.seg_bytes(i, True, not prefetch), self.tracks, self.ix["scale"])
            with self.lock:
                self.cache[i] = r
                self.order.append(i)
                while len(self.order) > 8:
                    self.cache.pop(self.order.pop(0), None)
            return r
        finally:
            with self.lock:
                self.inflight.pop(i, None)
            ev.set()

    def prefetch(self, i):
        for k in range(1, 5):
            j = i + k
            if j < len(self.ix["segs"]):
                with self.lock:
                    busy = j in self.cache or j in self.inflight
                if not busy:
                    threading.Thread(target=lambda j=j: self._quiet(j), daemon=True).start()
                    return              # un seul à la fois : ils s'enchaînent

    def _quiet(self, j):
        try:
            self.frames(j, prefetch=True)
        except Exception:
            pass

    def master(self):
        m = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
        for k, (t, s) in enumerate(self.audios):
            c = lang2(t.lang)
            name = NAMES.get(c, t.lang) + (" · " + t.name if t.name else "") + " (%s)" % s["codecs"]
            m.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="%s",LANGUAGE="%s",DEFAULT=%s,AUTOSELECT=YES,URI="audio%d.m3u8"'
                     % (name.replace('"', "'"), c, "YES" if k == 0 else "NO", k))
        for k, t in enumerate(self.subs):
            c = lang2(t.lang)
            name = NAMES.get(c, t.lang) + (" (forcés)" if t.forced else (" · " + t.name if t.name else "")) + " %d" % (k + 1)
            m.append('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="%s",LANGUAGE="%s",DEFAULT=NO,AUTOSELECT=%s,FORCED=%s,URI="subs%d.m3u8"'
                     % (name.replace('"', "'"), c, "YES" if t.forced else "NO", "YES" if t.forced else "NO", k))
        bw = int(self.f.size * 8 / self.duration * 1.3) if self.duration > 0 else 8000000
        inf = '#EXT-X-STREAM-INF:BANDWIDTH=%d,RESOLUTION=%dx%d,AUDIO="aud"' % (max(500000, bw), self.video.w, self.video.h)
        if self.subs: inf += ',SUBTITLES="sub"'
        if self.vset["codecs"]:
            inf += ',CODECS="%s"' % ",".join([self.vset["codecs"]] + sorted({s["codecs"] for _, s in self.audios}))
        m += [inf, "video.m3u8"]
        return "\n".join(m) + "\n"

    def playlist(self, prefix, ext, init=None):
        tgt = math.ceil(max(self.seg_dur(i) for i in range(len(self.ix["segs"]))))
        l = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:%d" % tgt, "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0"]
        if init:
            l += ["#EXT-X-INDEPENDENT-SEGMENTS", '#EXT-X-MAP:URI="%s"' % init]
        for i in range(len(self.ix["segs"])):
            l += ["#EXTINF:%.3f," % self.seg_dur(i), "%s%d.%s" % (prefix, i, ext)]
        l.append("#EXT-X-ENDLIST")
        return "\n".join(l) + "\n"

    def video_seg(self, i, fr):
        pts = [p * 9 // 100000 for p, _, _, _ in fr]
        srt = sorted(pts)
        fb = self.video.defdur * 9 // 100000 if self.video.defdur else 0
        if fb <= 0 and len(srt) > 1: fb = srt[len(srt) // 2] - srt[len(srt) // 2 - 1]
        if fb <= 0: fb = 3600
        smp = []
        for k, (p, (_, d, key, _)) in enumerate(zip(pts, fr)):
            dur = srt[k + 1] - srt[k] if k + 1 < len(srt) else fb
            smp.append((max(1, dur), d, key, p - srt[k]))
        base = srt[0] if srt else int(self.ix["segs"][i][0] * 90000)
        return media_segment(i + 1, base, smp, True)

    def audio_seg(self, i, k, fr):
        s = self.audios[k][1]
        start = fr[0][0] if fr else int(self.ix["segs"][i][0] * 1e9)
        base = max(0, start) * s["timescale"] // 1000000000
        return media_segment(i + 1, base, [(s["fdur"], d, True, 0) for _, d, _, _ in fr], False)

    @staticmethod
    def vtt(fr, codec):
        def st(ns):
            ms = max(0, ns // 1000000)
            return "%02d:%02d:%02d.%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
        out = "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000\n\n"
        for p, d, _, dur in fr:
            txt = d.decode("utf8", "replace")
            if "ASS" in codec or "SSA" in codec:
                parts = txt.split(",", 8)
                if len(parts) == 9: txt = parts[8]
                txt = re.sub(r"\{[^}]*\}", "", txt).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            txt = txt.replace("&", "&amp;").replace("\r", "").replace("-->", "→").strip()
            if txt:
                out += "%s --> %s\n%s\n\n" % (st(p), st(p + (dur if dur > 0 else 4000000000)), txt)
        return out

    def get(self, name):
        n = len(self.ix["segs"])
        if name == "master.m3u8": return self.master().encode(), "application/vnd.apple.mpegurl"
        if name == "video.m3u8": return self.playlist("v_", "m4s", "init_v.mp4").encode(), "application/vnd.apple.mpegurl"
        if name == "init_v.mp4": return init_segment(self.vset, True, self.video.w, self.video.h), "video/mp4"
        m = re.fullmatch(r"audio(\d+)\.m3u8", name)
        if m and int(m.group(1)) < len(self.audios):
            k = int(m.group(1))
            return self.playlist("a%d_" % k, "m4s", "init_a%d.mp4" % k).encode(), "application/vnd.apple.mpegurl"
        m = re.fullmatch(r"init_a(\d+)\.mp4", name)
        if m and int(m.group(1)) < len(self.audios):
            return init_segment(self.audios[int(m.group(1))][1], False), "video/mp4"
        m = re.fullmatch(r"subs(\d+)\.m3u8", name)
        if m and int(m.group(1)) < len(self.subs):
            return self.playlist("t%s_" % m.group(1), "vtt").encode(), "application/vnd.apple.mpegurl"
        m = re.fullmatch(r"v_(\d+)\.m4s", name)
        if m and int(m.group(1)) < n:
            i = int(m.group(1))
            fr = self.frames(i)
            self.prefetch(i)
            return self.video_seg(i, fr.get(self.video.num, [])), "video/mp4"
        m = re.fullmatch(r"a(\d+)_(\d+)\.m4s", name)
        if m and int(m.group(1)) < len(self.audios) and int(m.group(2)) < n:
            k, i = int(m.group(1)), int(m.group(2))
            return self.audio_seg(i, k, self.frames(i).get(self.audios[k][0].num, [])), "video/mp4"
        m = re.fullmatch(r"t(\d+)_(\d+)\.vtt", name)
        if m and int(m.group(1)) < len(self.subs) and int(m.group(2)) < n:
            t = self.subs[int(m.group(1))]
            return self.vtt(self.frames(int(m.group(2))).get(t.num, []), t.codec).encode(), "text/vtt"
        raise KeyError(name)

    def close(self): self.f.close()


# ---------------------------------------------------------------- ts (film) → HLS découpé

def ts_sync(b):
    for o in range(188):
        if o + 188 * 5 < len(b) and all(b[o + 188 * k] == 0x47 for k in range(5)):
            return o
    return None


def ts_pid(b, k): return ((b[k + 1] & 0x1F) << 8) | b[k + 2]


def ts_tables(b, s0):
    pat = pmt = None
    pmt_pid = vpid = None
    vtype = 0
    k = s0
    while k + 188 <= len(b) and pmt is None:
        if b[k] == 0x47 and b[k + 1] & 0x40:
            p, q = ts_pid(b, k), k + 5 + b[k + 4]
            if p == 0 and pat is None:
                pat = bytes(b[k:k + 188])
                e = q + 8
                while e + 4 <= k + 184:
                    if (b[e] << 8) | b[e + 1]:
                        pmt_pid = ((b[e + 2] & 0x1F) << 8) | b[e + 3]
                        break
                    e += 4
            elif pmt_pid is not None and p == pmt_pid:
                pmt = bytes(b[k:k + 188])
                ln = ((b[q + 1] & 0x0F) << 8) | b[q + 2]
                end = min(k + 188, q + 3 + ln - 4)
                r = q + 12 + (((b[q + 10] & 0x0F) << 8) | b[q + 11])
                while r + 5 <= end:
                    st, ep = b[r], ((b[r + 1] & 0x1F) << 8) | b[r + 2]
                    if vpid is None and st in (0x1B, 0x24, 0x02):
                        vpid, vtype = ep, st
                    r += 5 + (((b[r + 3] & 0x0F) << 8) | b[r + 4])
        k += 188
    return pat, pmt, vpid, vtype


def ts_pts(b, s0, vpid, last):
    found = None
    k = s0
    while k + 188 <= len(b):
        if b[k] == 0x47 and ts_pid(b, k) == vpid and b[k + 1] & 0x40 and b[k + 3] & 0x10:
            q = k + 4 + ((1 + b[k + 4]) if b[k + 3] & 0x20 else 0)
            if q + 14 <= k + 188 and b[q:q + 3] == b"\0\0\1" and b[q + 7] & 0x80:
                x = q + 9
                v = (((b[x] >> 1) & 7) << 30) | (b[x + 1] << 22) | ((b[x + 2] >> 1) << 15) | (b[x + 3] << 7) | (b[x + 4] >> 1)
                if not last:
                    return v
                found = v
        k += 188
    return found


class TSSession:
    def __init__(self, url):
        self.f = RemoteFile(url)
        head = self.f.read_range(0, 2 << 20)
        s0 = ts_sync(head)
        if s0 is None: raise IOError("fichier ts illisible")
        pat, pmt, vpid, vtype = ts_tables(head, s0)
        if not (pat and pmt and vpid): raise IOError("fichier ts incomplet")
        size = self.f.size
        first = ts_pts(head, s0, vpid, False)
        tl = min(size, 2 << 20)
        tail = self.f.read_range(size - tl, tl)
        t0 = ts_sync(tail)
        lastp = ts_pts(tail, t0, vpid, True) if t0 is not None else None
        if first is None or lastp is None: raise IOError("durée inconnue")
        self.duration = ((lastp - first) % (1 << 33)) / 90000
        if self.duration < 1: raise IOError("durée inconnue")
        self.start = s0
        self.seg = max(188 * 2000, int(size * 10 / self.duration) // 188 * 188)
        self.count = math.ceil((size - s0) / self.seg)
        self.header = pat + pmt
        STATS.bitrate = size * 8 / self.duration / 1e6

    def rng(self, i):
        a = self.start + i * self.seg
        b = min(self.f.size, a + self.seg)
        return a, a + (b - a) // 188 * 188

    def get(self, name):
        if name == "master.m3u8":
            total = max(1, self.f.size - self.start)
            l = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:%d" % (math.ceil(self.duration * self.seg / total) + 1),
                 "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0"]
            for i in range(self.count):
                a, b = self.rng(i)
                l += ["#EXTINF:%.3f," % (self.duration * (b - a) / total), "s%d.ts" % i]
            l.append("#EXT-X-ENDLIST")
            return ("\n".join(l) + "\n").encode(), "application/vnd.apple.mpegurl"
        m = re.fullmatch(r"s(\d+)\.ts", name)
        if m and int(m.group(1)) < self.count:
            a, b = self.rng(int(m.group(1)))
            return self.header + self.f.read_stream(a, b - a), "video/mp2t"
        raise KeyError(name)

    def close(self): self.f.close()


# ---------------------------------------------------------------- mp4 : relayé tel quel (plages d'octets)

class ProxySession:
    def __init__(self, url):
        self.url = url
        self.duration = None

    def close(self): pass


# ---------------------------------------------------------------- direct : ffmpeg

class LiveSession:
    def __init__(self, sid, url):
        self.sid, self.url = sid, url
        self.dir = os.path.join(RUN, sid)
        os.makedirs(self.dir, exist_ok=True)
        self.proc = None
        self.stopped = False
        self.restarts = 0
        self.duration = None
        threading.Thread(target=self._supervise, daemon=True).start()

    def _cmd(self, append):
        flags = "delete_segments+independent_segments+omit_endlist+temp_file" + ("+append_list+discont_start" if append else "")
        return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-user_agent", UA, "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                "-reconnect_delay_max", "4", "-rw_timeout", "15000000",
                "-fflags", "+genpts+discardcorrupt", "-i", self.url,
                "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-sn", "-dn",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "8", "-hls_flags", flags,
                "-hls_segment_filename", os.path.join(self.dir, "seg_%06d.ts"), os.path.join(self.dir, "live.m3u8")]

    def _supervise(self):
        append = False
        while not self.stopped:
            log("direct", self.sid, "démarrage ffmpeg" + (" (reprise)" if append else ""))
            self.proc = subprocess.Popen(self._cmd(append), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            err = self.proc.stderr.read()
            self.proc.wait()
            if self.stopped:
                break
            self.restarts += 1
            STATS.retries += 1
            log("direct", self.sid, "ffmpeg arrêté, relance :", (err or "")[-300:])
            append = os.path.exists(os.path.join(self.dir, "live.m3u8"))
            time.sleep(min(5, self.restarts))

    def ready(self):
        p = os.path.join(self.dir, "live.m3u8")
        return os.path.exists(p) and "#EXTINF" in open(p).read()

    def get(self, name):
        if not re.fullmatch(r"live\.m3u8|seg_\d+\.ts", name):
            raise KeyError(name)
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            raise KeyError(name)
        with open(p, "rb") as fh:
            return fh.read(), ("application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t")

    def close(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- sessions (une seule active)

class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}          # id → (session, dernier accès)

    def open(self, kind, factory):
        sid = uuid.uuid4().hex[:10]
        self.close_all()                       # le compte n'autorise qu'une connexion
        s = factory(sid)
        with self.lock:
            self.items[sid] = [s, time.time(), kind]
        return sid, s

    def get(self, sid):
        with self.lock:
            it = self.items.get(sid)
            if it:
                it[1] = time.time()
                return it[0]
        return None

    def close(self, sid):
        with self.lock:
            it = self.items.pop(sid, None)
        if it:
            it[0].close()

    def close_all(self):
        with self.lock:
            items, self.items = list(self.items.values()), {}
        for it in items:
            it[0].close()

    def reap(self):
        while True:
            time.sleep(10)
            now = time.time()
            with self.lock:
                old = [k for k, v in self.items.items() if now - v[1] > IDLE_STOP.get(v[2], IDLE_STOP_VOD)]
            for k in old:
                log("session inactive fermée", k)
                self.close(k)

    remote_busy_until = 0.0

    def busy_upstream(self):
        """Une lecture utilise la connexion du compte (les enregistrements relus depuis la clé ne comptent pas),
        ici ou sur l'iPhone hors de la maison (signalé par /busy)"""
        if time.time() < self.remote_busy_until:
            return True
        with self.lock:
            return any(v[2] != "local" for v in self.items.values())

    def describe(self):
        with self.lock:
            return [{"id": k, "kind": v[2], "idle_s": int(time.time() - v[1])} for k, v in self.items.items()]


SESSIONS = Sessions()


# ---------------------------------------------------------------- garde : VPN et serveur

class Guard:
    def __init__(self):
        self.upstream_host = None
        self.upstream_ok = None
        self.last_check = 0
        self.vpn = {}

    def loop(self):
        fails = 0
        while True:
            try:
                self.vpn = vpn_status()
                if not self.vpn.get("connected") and shutil.which("nordvpn"):
                    log("VPN déconnecté : reconnexion")
                    sh(["nordvpn", "connect", os.environ.get("TVLIBRE_VPN_COUNTRY", "Switzerland")], timeout=60)
                if self.upstream_host:
                    ok = self.reach(self.upstream_host)
                    self.upstream_ok = ok
                    fails = 0 if ok else fails + 1
                    if fails >= 3 and shutil.which("nordvpn"):
                        log("serveur injoignable 3 fois : autre serveur VPN")
                        sh(["nordvpn", "connect", os.environ.get("TVLIBRE_VPN_COUNTRY", "Switzerland")], timeout=60)
                        fails = 0
                self.last_check = time.time()
            except Exception as e:
                log("garde :", e)
            time.sleep(60)

    @staticmethod
    def reach(host):
        h, _, p = host.partition(":")
        try:
            with socket.create_connection((h, int(p or 80)), timeout=8):
                return True
        except Exception:
            return False


GUARD = Guard()

VPN_MTU = 1280   # la ligne ne laisse pas passer les gros paquets du tunnel à 1420 : le HTTPS se bloquait


def mtu_keeper():
    """Remet la taille de paquet du tunnel à chaque (re)connexion du VPN"""
    path = "/sys/class/net/nordlynx/mtu"
    while True:
        try:
            if os.path.exists(path) and int(open(path).read().strip()) > VPN_MTU:
                sh(["ip", "link", "set", "nordlynx", "mtu", str(VPN_MTU)], timeout=10)
                log("taille de paquet du VPN ramenée à", VPN_MTU)
        except Exception as e:
            log("mtu :", e)
        time.sleep(3)


# ---------------------------------------------------------------- clé USB : enregistrements

MOUNT = "/mnt/tvlibre"
LABEL = "TVLIBRE"


class Storage:
    """Clé USB : formatée une fois (ext4, nom TVLIBRE) puis montée ; sans clé, le relais fonctionne comme avant"""

    def __init__(self):
        self.ok = False
        self.device = None
        self.error = None
        self.formatted = set()

    def usb_part(self):
        try:
            data = json.loads(sh(["lsblk", "-J", "-o", "NAME,TRAN,TYPE,FSTYPE,LABEL,MOUNTPOINT,SIZE"], timeout=10) or "{}")
        except Exception:
            return None
        for d in data.get("blockdevices", []):
            if d.get("tran") != "usb" or d.get("type") != "disk":
                continue
            parts = [c for c in d.get("children", []) if c.get("type") == "part"]
            return parts[0] if parts else d
        return None

    def check(self):
        p = self.usb_part()
        if p is None:
            if self.ok:
                log("clé USB retirée")
            if os.path.ismount(MOUNT):
                sh(["umount", "-l", MOUNT], timeout=30)
            self.ok, self.device, self.error = False, None, None
            self.formatted.clear()          # nouvelle clé : on pourra la formater
            return
        dev = "/dev/" + p["name"]
        disk = re.sub(r"\d+$", "", p["name"])
        try:
            try:
                ro = open("/sys/block/%s/ro" % disk).read().strip() == "1"
            except OSError:
                ro = False
            if ro:
                raise IOError("clé protégée en écriture (verrou sur la clé, ou clé usée) : branche une autre clé")
            if p.get("fstype") != "ext4" or p.get("label") != LABEL:
                if dev in self.formatted:
                    raise IOError("formatage déjà tenté sans succès")
                self.formatted.add(dev)
                log("formatage de la clé", dev)
                if p.get("mountpoint"):
                    sh(["umount", p["mountpoint"]], timeout=30)
                r = subprocess.run(["mkfs.ext4", "-F", "-q", "-L", LABEL, dev], capture_output=True, text=True, timeout=600)
                if r.returncode != 0:
                    raise IOError("formatage impossible : " + r.stderr.strip()[:200])
            os.makedirs(MOUNT, exist_ok=True)
            if not os.path.ismount(MOUNT):
                r = subprocess.run(["mount", "-o", "noatime", dev, MOUNT], capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    raise IOError("montage impossible : " + r.stderr.strip()[:200])
            os.makedirs(os.path.join(MOUNT, "rec"), exist_ok=True)
            self.ok, self.device, self.error = True, dev, None
        except Exception as e:
            self.ok, self.error = False, str(e)
            log("clé USB :", e)

    def free(self):
        if not self.ok:
            return 0
        st = os.statvfs(MOUNT)
        return st.f_bavail * st.f_frsize

    def total(self):
        if not self.ok:
            return 0
        st = os.statvfs(MOUNT)
        return st.f_blocks * st.f_frsize

    def loop(self):
        while True:
            self.check()
            time.sleep(30)


STORAGE = Storage()


class Recorder:
    """Enregistre les replays demandés sur la clé ; se met en pause dès qu'une lecture utilise le compte"""
    MIN_FREE = 150 << 20

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = []
        self.loaded = False
        self.current = None
        self.idle_since = time.time()

    def path(self, name=""):
        return os.path.join(MOUNT, "rec", name)

    def load(self):
        if self.loaded or not STORAGE.ok:
            return
        try:
            with open(self.path("jobs.json")) as f:
                self.jobs = json.load(f)
            for j in self.jobs:
                if j["status"] == "en cours":
                    j["status"] = "en attente"
        except Exception:
            self.jobs = []
        self.loaded = True

    def save(self):
        if not STORAGE.ok:
            return
        tmp = self.path("jobs.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.jobs, f, ensure_ascii=False)
        os.replace(tmp, self.path("jobs.json"))

    def add(self, q):
        if not STORAGE.ok:
            raise IOError("pas de clé USB utilisable")
        with self.lock:
            self.load()
            j = {"id": uuid.uuid4().hex[:10], "url": q["u"], "title": q.get("title", "Enregistrement"),
                 "channel": q.get("channel", ""), "start": float(q.get("start", "0") or 0),
                 "duration": float(q.get("duration", "0") or 0), "status": "en attente", "bytes": 0,
                 "expires": float(q.get("expires", "0") or 0),
                 "created": time.time(), "error": None}
            self.jobs.append(j)
            self.save()
            return j

    def schedule(self, q):
        """Direct à enregistrer à l'heure de diffusion (2 min de marge avant, 5 min après)"""
        if not STORAGE.ok:
            raise IOError("pas de clé USB utilisable")
        start, stop = float(q["start"]), float(q["stop"])
        with self.lock:
            self.load()
            j = {"id": uuid.uuid4().hex[:10], "url": q["u"], "title": q.get("title", "Enregistrement"),
                 "channel": q.get("channel", ""), "start": start, "duration": stop - start, "kind": "live",
                 "rec_from": start - 120, "rec_to": stop + 300,
                 "status": "programmé", "bytes": 0, "created": time.time(), "error": None}
            self.jobs.append(j)
            self.save()
            return j

    def stop(self, jid):
        """Arrêt demandé : le direct garde ce qui est déjà enregistré, un replay en attente est retiré de la file"""
        with self.lock:
            self.load()
            for j in self.jobs:
                if j["id"] != jid:
                    continue
                if j["status"] in ("en cours", "programmé", "en attente", "en pause"):
                    has = os.path.exists(self.path(jid + ".ts")) and os.path.getsize(self.path(jid + ".ts")) > (1 << 20)
                    j["status"] = "terminé" if has else "arrêté"
                    j["error"] = "arrêté avant la fin" if has else None
            if self.current == jid:
                self.current = None
            self.save()

    def recording_now(self):
        with self.lock:
            for j in self.jobs:
                if j["status"] == "en cours":
                    return {"id": j["id"], "title": j["title"], "channel": j.get("channel", ""),
                            "kind": j.get("kind", "replay"), "until": j.get("rec_to")}
        return None

    def live_active(self):
        r = self.recording_now()
        return r if r and r["kind"] == "live" else None

    def delete(self, jid):
        with self.lock:
            self.load()
            self.jobs = [j for j in self.jobs if j["id"] != jid]
            if self.current == jid:
                self.current = None
            try:
                os.remove(self.path(jid + ".ts"))
            except OSError:
                pass
            self.save()

    def listing(self):
        with self.lock:
            self.load()
            out = []
            for j in self.jobs:
                d = {k: v for k, v in j.items() if k != "url"}
                try:
                    d["bytes"] = os.path.getsize(self.path(j["id"] + ".ts"))
                except OSError:
                    pass
                out.append(d)
            return out

    def next_job(self):
        """Replay à enregistrer : celui qui sort du replay le plus tôt passe en premier"""
        with self.lock:
            self.load()
            todo = [j for j in self.jobs if j["status"] in ("en attente", "en pause") and j.get("kind") != "live"]
        todo.sort(key=lambda j: j.get("expires") or float("inf"))
        return todo[0] if todo else None

    def check_expiry(self):
        """Replays qui sortent de la fenêtre du serveur : abandon si c'est trop tard, alerte si c'est proche"""
        now = time.time()
        changed = False
        with self.lock:
            for j in self.jobs:
                if j.get("kind") == "live" or j["status"] not in ("en attente", "en pause") or not j.get("expires"):
                    j.pop("urgent", None)
                    continue
                if now >= j["expires"]:
                    j["status"], j["error"] = "échec", "sorti du replay avant la fin de l'enregistrement"
                    j.pop("urgent", None)
                    changed = True
                    try:
                        os.remove(self.path(j["id"] + ".ts"))
                    except OSError:
                        pass
                else:
                    # Il faut à peu près la durée du programme pour l'enregistrer (débit du serveur), plus 15 min de marge
                    urgent = j["expires"] - now < j.get("duration", 3600) * 1.15 + 900
                    if urgent != bool(j.get("urgent")):
                        j["urgent"] = urgent
                        changed = True
            if changed:
                self.save()

    def urgent(self):
        with self.lock:
            return [{"id": j["id"], "title": j["title"], "expires": j["expires"]} for j in self.jobs if j.get("urgent")]

    def due_live(self):
        now = time.time()
        with self.lock:
            self.load()
            for j in self.jobs:
                if j.get("kind") == "live" and j["status"] in ("programmé", "en cours"):
                    if now >= j["rec_to"]:
                        has = os.path.exists(self.path(j["id"] + ".ts"))
                        j["status"] = "terminé" if has else "échec"
                        j["error"] = None if has else "rien n'a été reçu"
                        self.save()
                    elif now >= j["rec_from"]:
                        return j
        return None

    def set(self, j, **kw):
        with self.lock:
            j.update(kw)
            self.save()

    def loop(self):
        while True:
            time.sleep(5)
            try:
                if not STORAGE.ok:
                    self.loaded = False
                    continue
                self.check_expiry()
                live = self.due_live()
                if live:                                  # le direct à enregistrer passe avant tout
                    SESSIONS.close_all()
                    self.run_live(live)
                    continue
                if SESSIONS.busy_upstream():
                    self.idle_since = time.time()
                    continue
                if time.time() - self.idle_since < 20:      # lecture tout juste arrêtée : on laisse la main
                    continue
                j = self.next_job()
                if j:
                    self.run(j)
            except Exception as e:
                log("enregistrement :", e)

    def run(self, j):
        f = self.path(j["id"] + ".ts")
        have = os.path.getsize(f) if os.path.exists(f) else 0
        if STORAGE.free() < self.MIN_FREE:
            return self.set(j, status="échec", error="clé pleine")
        self.current = j["id"]
        self.set(j, status="en cours", error=None)
        log("enregistrement", j["title"])
        try:
            r = http_get(j["url"], "bytes=%d-" % have if have else None, timeout=30)
            if have and r.status != 206:
                have = 0                                  # pas de reprise possible : on repart du début
            mode = "ab" if have else "wb"
            with open(f, mode) as out:
                t = time.time()
                while True:
                    if self.current != j["id"]:
                        return                            # supprimé pendant l'enregistrement
                    if SESSIONS.busy_upstream():
                        self.set(j, status="en pause")
                        log("enregistrement en pause (lecture en cours)")
                        return
                    if STORAGE.free() < self.MIN_FREE:
                        self.set(j, status="échec", error="clé pleine")
                        return
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    now = time.time()
                    STATS.add(len(chunk), now - t)
                    t = now
            size = os.path.getsize(f)
            self.set(j, status="terminé" if size > (1 << 20) else "échec", bytes=size,
                     error=None if size > (1 << 20) else "le serveur n'a rien envoyé")
            log("enregistrement terminé", j["title"], size)
        except Exception as e:
            self.set(j, status="en attente", error=str(e)[:120])
            log("enregistrement interrompu, nouvel essai plus tard :", e)
            time.sleep(30)
        finally:
            if self.current == j["id"]:
                self.current = None

    def run_live(self, j):
        """Enregistre le direct jusqu'à l'heure de fin ; reconnexion automatique en cas de coupure"""
        f = self.path(j["id"] + ".ts")
        self.current = j["id"]
        self.set(j, status="en cours", error=None)
        log("enregistrement du direct", j["title"])
        try:
            with open(f, "ab") as out:
                while time.time() < j["rec_to"] and self.current == j["id"]:
                    if STORAGE.free() < self.MIN_FREE:
                        self.set(j, status="terminé", error="clé pleine : arrêté avant la fin")
                        return
                    try:
                        r = http_get(j["url"], timeout=20)
                        t = time.time()
                        while time.time() < j["rec_to"] and self.current == j["id"]:
                            chunk = r.read(188 * 1000)
                            if not chunk:
                                break
                            out.write(chunk)
                            now = time.time()
                            STATS.add(len(chunk), now - t)
                            t = now
                        r.close()
                    except Exception as e:
                        log("direct interrompu, reconnexion :", e)
                        time.sleep(3)
            if self.current == j["id"]:
                size = os.path.getsize(f) if os.path.exists(f) else 0
                self.set(j, status="terminé" if size > (1 << 20) else "échec", bytes=size,
                         error=None if size > (1 << 20) else "rien n'a été reçu")
                log("enregistrement du direct terminé", j["title"], size)
        finally:
            if self.current == j["id"]:
                self.current = None

    def summary(self):
        with self.lock:
            return {k: sum(1 for j in self.jobs if j["status"] == k) for k in ("programmé", "en attente", "en cours", "en pause", "terminé", "échec")}


RECORDER = Recorder()


# ---------------------------------------------------------------- WireGuard : l'app joint le Raspberry de partout

WG_DIR = "/etc/tvlibre/wg"
WG_PORT = 51820
WG_NET = "10.66.0.0/24"


class WireGuard:
    """Serveur WireGuard minimal (10.66.0.1) : l'app de l'iPhone s'y connecte hors de la maison.
    Le port est ouvert sur la box par UPnP si elle le permet."""

    def __init__(self):
        self.ok = False
        self.public_ip = None
        self.upnp = False
        self.error = None
        self.note = None

    def read(self, name):
        with open(os.path.join(WG_DIR, name)) as f:
            return f.read().strip()

    def keys(self):
        os.makedirs(WG_DIR, mode=0o700, exist_ok=True)
        for who in ("server", "client"):
            k = os.path.join(WG_DIR, who + ".key")
            if not os.path.exists(k):
                priv = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True).stdout.strip()
                pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True).stdout.strip()
                with open(k, "w") as f:
                    f.write(priv)
                os.chmod(k, 0o600)
                with open(os.path.join(WG_DIR, who + ".pub"), "w") as f:
                    f.write(pub)

    def setup(self):
        if not shutil.which("wg") or not shutil.which("upnpc"):
            log("installation de WireGuard")
            subprocess.run(["apt-get", "install", "-y", "wireguard-tools", "miniupnpc"],
                           capture_output=True, text=True, timeout=900, env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
        if not shutil.which("wg"):
            raise IOError("WireGuard non installé")
        self.keys()
        conf = "[Interface]\nAddress = 10.66.0.1/24\nListenPort = %d\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.66.0.2/32\n" % (
            WG_PORT, self.read("server.key"), self.read("client.pub"))
        path = "/etc/wireguard/tvlibre.conf"
        old = open(path).read() if os.path.exists(path) else ""
        if old != conf:
            os.makedirs("/etc/wireguard", exist_ok=True)
            with open(path, "w") as f:
                f.write(conf)
            os.chmod(path, 0o600)
            sh(["systemctl", "enable", "wg-quick@tvlibre"], timeout=30)
            sh(["systemctl", "restart", "wg-quick@tvlibre"], timeout=60)
        elif "tvlibre" not in sh(["wg", "show", "interfaces"]):
            sh(["systemctl", "restart", "wg-quick@tvlibre"], timeout=60)
        if shutil.which("nordvpn") and not os.path.exists(os.path.join(WG_DIR, "allowlist-ok")):
            sh(["nordvpn", "whitelist", "add", "port", str(WG_PORT), "protocol", "UDP"], timeout=30)
            sh(["nordvpn", "whitelist", "add", "subnet", WG_NET], timeout=30)
            open(os.path.join(WG_DIR, "allowlist-ok"), "w").close()
        self.ok = "tvlibre" in sh(["wg", "show", "interfaces"])

    def map_port(self):
        """Ouverture du port sur la box (UPnP) et adresse publique de la maison"""
        ip = lan_ip()
        if not ip or not shutil.which("upnpc"):
            return
        out = sh(["upnpc", "-e", "TV Libre", "-a", ip, str(WG_PORT), str(WG_PORT), "UDP", "86400"], timeout=30)
        self.upnp = "is redirected to internal" in out
        m = re.search(r"ExternalIPAddress = ([0-9.]+)", out) or re.search(r"ExternalIPAddress = ([0-9.]+)", sh(["upnpc", "-s"], timeout=30))
        if m:
            self.public_ip = m.group(1)
            a = [int(x) for x in self.public_ip.split(".")]
            if a[0] == 10 or (a[0] == 100 and 64 <= a[1] < 128) or (a[0] == 192 and a[1] == 168) or (a[0] == 172 and 16 <= a[1] < 32):
                self.note = "la box n'a pas d'adresse publique (CGNAT) : demande une IPv4 publique à Sunrise"
            else:
                self.note = None if self.upnp else "ouvre le port %d UDP de la box vers %s" % (WG_PORT, ip)
        else:
            self.note = "UPnP indisponible sur la box : ouvre le port %d UDP vers %s et indique l'adresse publique dans l'app" % (WG_PORT, ip)

    def client_config(self):
        return "[Interface]\nPrivateKey = %s\nAddress = 10.66.0.2/32\n\n[Peer]\nPublicKey = %s\nEndpoint = %s:%d\nAllowedIPs = 10.66.0.1/32\nPersistentKeepalive = 25\n" % (
            self.read("client.key"), self.read("server.pub"), self.public_ip or "ADRESSE_PUBLIQUE", WG_PORT)

    def loop(self):
        while True:
            try:
                self.setup()
                self.map_port()
                self.error = None
            except Exception as e:
                self.error = str(e)[:200]
                log("wireguard :", e)
            time.sleep(1800)

    def describe(self):
        return {"ok": self.ok, "public_ip": self.public_ip, "upnp": self.upnp, "port": WG_PORT, "note": self.note, "error": self.error}


WG = WireGuard()


def status():
    return {
        "version": VERSION, "lan_ip": lan_ip(), "port": PORT,
        "temperature_c": temperature(), "throttled": throttled(),
        "vpn": {k: v for k, v in GUARD.vpn.items() if k != "raw"},
        "upstream": {"host": GUARD.upstream_host, "reachable": GUARD.upstream_ok},
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        "uptime_s": int(float(open("/proc/uptime").read().split()[0])) if os.path.exists("/proc/uptime") else None,
        "sessions": SESSIONS.describe(), "stats": STATS.snapshot(),
        "storage": {"ok": STORAGE.ok, "free": STORAGE.free(), "total": STORAGE.total(), "error": STORAGE.error,
                    "recordings": RECORDER.summary() if STORAGE.ok else {}},
        "recording": RECORDER.recording_now() if STORAGE.ok else None,
        "urgent": RECORDER.urgent() if STORAGE.ok else [],
        "wireguard": WG.describe(),
    }


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TVLibreRelay/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None, head=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def js(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/status":
                return self.js(status())
            if u.path == "/api":
                return self.api(q["u"])
            if u.path == "/live/open":
                return self.live_open(q["u"])
            if u.path == "/vod/open":
                return self.vod_open(q["u"], q.get("ext", "mkv").lower(), float(q.get("start", "0") or 0))
            if u.path == "/probe":
                return self.probe(q["u"])
            if u.path == "/rec/add":
                try:
                    return self.js(RECORDER.add(q))
                except Exception as e:
                    return self.js({"error": str(e)}, 409)
            if u.path == "/rec/schedule":
                try:
                    return self.js(RECORDER.schedule(q))
                except Exception as e:
                    return self.js({"error": str(e)}, 409)
            if u.path == "/rec/stop":
                RECORDER.stop(q.get("id", ""))
                return self.js({"ok": True})
            if u.path == "/wg/client":
                # Donnée seulement aux appareils du réseau de la maison
                ip = self.client_address[0]
                if not (ip.startswith("192.168.") or ip.startswith("172.") or (ip.startswith("10.") and not ip.startswith("10.66."))):
                    return self.js({"error": "seulement depuis le réseau de la maison"}, 403)
                if not WG.ok:
                    return self.js({"error": WG.error or "WireGuard pas encore prêt"}, 503)
                return self.js({"config": WG.client_config(), **WG.describe()})
            if u.path == "/busy":
                Sessions.remote_busy_until = time.time() + 90
                return self.js({"ok": True})
            if u.path == "/rec/list":
                return self.js({"storage": status()["storage"], "items": RECORDER.listing()})
            if u.path == "/rec/delete":
                RECORDER.delete(q.get("id", ""))
                return self.js({"ok": True})
            m = re.fullmatch(r"/rec/file/([0-9a-f]{10})\.ts", u.path)
            if m:
                return self.local_file(RECORDER.path(m.group(1) + ".ts"), head)
            if u.path == "/close":
                SESSIONS.close(q.get("id", ""))
                return self.js({"ok": True})
            m = re.fullmatch(r"/s/([0-9a-f]{10})/(.+)", u.path)
            if m:
                return self.serve(m.group(1), m.group(2), head)
            self.send(404, b"introuvable")
        except KeyError as e:
            self.send(404, ("introuvable : %s" % e).encode())
        except Exception as e:
            log("erreur", u.path, e)
            self.js({"error": str(e)}, 502)

    def probe(self, url):
        """Replay : le serveur donne-t-il la taille et accepte-t-il les plages ? (lecture façon film)"""
        try:
            with http_get(url, "bytes=0-1", timeout=20) as r:
                cr = r.headers.get("Content-Range", "")
                total = int(cr.split("/")[-1]) if r.status == 206 and "/" in cr and cr.split("/")[-1].isdigit() else 0
                return self.js({"range": total > 0, "size": total, "status": r.status})
        except Exception as e:
            return self.js({"range": False, "error": str(e)[:120]})

    def local_file(self, path, head):
        """Fichier enregistré sur la clé, avec plages d'octets"""
        if not os.path.exists(path):
            return self.send(404, b"introuvable")
        size = os.path.getsize(path)
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
        a, b = 0, size - 1
        if m:
            a = int(m.group(1))
            b = min(size - 1, int(m.group(2)) if m.group(2) else size - 1)
            if a >= size:
                return self.send(416, b"", "text/plain", {"Content-Range": "bytes */%d" % size}, head)
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(b - a + 1))
        self.send_header("Accept-Ranges", "bytes")
        if m:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, size))
        self.end_headers()
        if head:
            return
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(left, 256 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def note_upstream(self, url):
        p = urllib.parse.urlparse(url)
        if p.hostname not in ("127.0.0.1", "localhost"):
            GUARD.upstream_host = p.netloc

    def api(self, url):
        self.note_upstream(url)
        last = None
        for attempt in range(2):
            try:
                with http_get(url, timeout=25) as r:
                    body = r.read()
                    return self.send(r.status, body, r.headers.get("Content-Type", "application/json"))
            except urllib.error.HTTPError as e:
                return self.send(e.code, e.read() or b"", "text/plain")
            except Exception as e:
                last = e
        raise last

    def recording_conflict(self):
        r = RECORDER.live_active()
        if r:
            self.js({"error": "Enregistrement en cours de « %s » : arrête-le pour regarder autre chose." % r["title"]}, 423)
            return True
        return False

    def live_open(self, url):
        if self.recording_conflict():
            return
        self.note_upstream(url)
        STATS.reset("direct ts découpé par le Raspberry (ffmpeg)")
        sid, s = SESSIONS.open("live", lambda sid: LiveSession(sid, url))
        t = time.time()
        while time.time() - t < 20 and not s.ready():
            time.sleep(0.3)
        if not s.ready():
            SESSIONS.close(sid)
            return self.js({"error": "le direct ne démarre pas"}, 504)
        return self.js({"id": sid, "url": "/s/%s/live.m3u8" % sid})

    def vod_open(self, url, ext, start=0.0):
        if urllib.parse.urlparse(url).hostname not in ("127.0.0.1", "localhost") and self.recording_conflict():
            return
        self.note_upstream(url)
        if ext in ("mp4", "m4v", "mov"):
            STATS.reset("mp4 relayé tel quel par le Raspberry")
            sid, _ = SESSIONS.open("mp4", lambda sid: ProxySession(url))
            return self.js({"id": sid, "url": "/s/%s/file.%s" % (sid, ext)})
        if ext == "mkv":
            STATS.reset("mkv réemballé par le Raspberry")
            sid, s = SESSIONS.open("mkv", lambda sid: MKVSession(url))
            s.start_offset = start
            return self.js({"id": sid, "url": "/s/%s/master.m3u8" % sid, "duration": s.duration})
        if ext == "ts":
            local = urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost")
            STATS.reset("enregistrement relu depuis la clé" if local else "ts découpé par le Raspberry")
            sid, s = SESSIONS.open("local" if local else "ts", lambda sid: TSSession(url))
            s.start_offset = start
            return self.js({"id": sid, "url": "/s/%s/master.m3u8" % sid, "duration": s.duration})
        return self.js({"error": "format .%s non pris en charge" % ext}, 415)

    def serve(self, sid, name, head):
        s = SESSIONS.get(sid)
        if s is None:
            return self.send(404, b"session terminee")
        if isinstance(s, ProxySession):
            return self.proxy(s.url, head)
        body, ctype = s.get(name)
        off = getattr(s, "start_offset", 0)
        if off > 0 and name.endswith(".m3u8") and body.startswith(b"#EXTM3U\n"):
            # Reprise : le lecteur commence directement à cette position
            body = b"#EXTM3U\n" + ("#EXT-X-START:TIME-OFFSET=%.3f,PRECISE=YES\n" % off).encode() + body[8:]
        rng = self.headers.get("Range")
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng or "")
        if m and int(m.group(1)) < len(body):
            a = int(m.group(1))
            b = min(len(body) - 1, int(m.group(2)) if m.group(2) else len(body) - 1)
            return self.send(206, body[a:b + 1], ctype, {"Content-Range": "bytes %d-%d/%d" % (a, b, len(body)), "Accept-Ranges": "bytes"}, head)
        self.send(200, body, ctype, {"Accept-Ranges": "bytes"}, head)

    def proxy(self, url, head):
        """mp4 : la demande de plage du lecteur est transmise au serveur, la réponse renvoyée au fil de l'eau."""
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD" if head else "GET")
        if self.headers.get("Range"):
            req.add_header("Range", self.headers["Range"])
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            r = e
        self.send_response(r.status)
        for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if r.headers.get(k):
                self.send_header(k, r.headers[k])
        self.end_headers()
        if head:
            return
        t = time.time()
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            now = time.time()
            STATS.add(len(chunk), now - t)
            t = now
            self.wfile.write(chunk)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return                       # lecteur qui abandonne une requête : normal
        super().handle_error(request, client_address)


def main():
    os.makedirs(RUN, exist_ok=True)
    threading.Thread(target=SESSIONS.reap, daemon=True).start()
    threading.Thread(target=GUARD.loop, daemon=True).start()
    threading.Thread(target=mtu_keeper, daemon=True).start()
    threading.Thread(target=STORAGE.loop, daemon=True).start()
    threading.Thread(target=RECORDER.loop, daemon=True).start()
    threading.Thread(target=WG.loop, daemon=True).start()
    srv = Server(("0.0.0.0", PORT), Handler)
    log("TV Libre relais %s sur le port %d" % (VERSION, PORT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
