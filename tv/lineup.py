"""Grille TV Libre (même règles que l'app iPhone, fichier lineup.json commun).

Ordre : TNT, autres chaînes françaises par thème, Suisse romande, Royaume-Uni, États-Unis.
Flux Xtream d'abord ; une liste « Netplus » seulement en repli pour les chaînes de la grille.
"""
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DEF = json.load(open(os.path.join(HERE, "lineup.json"), encoding="utf-8"))
QUALITY = {"hd", "fhd", "uhd", "sd", "4k", "hevc", "h265", "h264", "raw", "backup", "vip", "low", "multi",
           "1080p", "720p", "576p", "50fps", "60fps", "hdr", "plus", "full", "ʜᴅ"}
PREFIX = re.compile(r"^\s*[|\[(]?\s*(FR|FRA|CH|SUI|UK|GB|US|USA|EN|BE|CA|RE|AR|DE|IT|ES)\s*[|\])\:\-]\s*", re.I)
REU = re.compile(r"^\s*(C\+\s*)?R[ÉE]U(NION)?\s*[|:\-]\s*", re.I)
GLUED = re.compile(r"(?<=[A-Za-z])\+\d{1,2}(?=$|[\s)\]|_])")
PREFIX2 = re.compile(r"^[\W_]*(FR|FRA|CH|SUI|UK|GB|US|USA|EN|BE|CA|RE)[\W_]+", re.I)
CODES = {"fr", "fra", "ch", "sui", "uk", "gb", "us", "usa", "en", "be", "ca", "re"}
SHIFTED = re.compile(r"(^|[\s(\[|_])(h\s?)?[+\-−–]\s?\d{1,2}\s?(h|hr|hrs|heures?)?(?=$|[\s)\]|_])", re.I)


def fold(s):
    s = unicodedata.normalize("NFKC", s)          # ᴴᴰ, ᶠᴴᴰ, 𝐓𝐅𝟏… ramenés aux lettres ordinaires
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def clean(raw):
    s, n = PREFIX.subn("", raw.strip(), count=1)
    if not n:
        m = PREFIX2.match(s)
        if m and any(not (ch.isspace() or ch.isalpha()) for ch in m.group(0)):
            s = s[m.end():]       # « FR ➾ TF1 », « FR★ TF1 », « [FR] TF1 »
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s).strip()
    return s or raw


def shifted(raw):
    """Chaîne décalée (« TF1 -2 », « M6 +1 ») : doublon du direct, le replay fait mieux"""
    s = unicodedata.normalize("NFKC", raw)
    return bool(SHIFTED.search(s) or GLUED.search(s))


def strip_reunion(s):
    """« C+ RÉU| TF1 HD » → « TF1 HD » : la version Réunion vient en secours de la chaîne"""
    m = REU.match(s)
    return s[m.end():] if m and s[m.end():].strip() else s


def key(raw):
    tokens = [t for t in re.split(r"[^0-9a-z+ʜᴅ]+", fold(clean(strip_reunion(unicodedata.normalize("NFKC", raw))))) if t]
    if len(tokens) > 1 and tokens[0] in CODES:
        tokens.pop(0)
    while len(tokens) > 1 and (tokens[-1] in QUALITY or tokens[-1] in CODES):
        tokens.pop()
    return "".join(tokens).replace("+", "plus")


def display(raw):
    """Nom affiché : sans préfixe de pays ni HD/FHD/4K à la fin"""
    words = clean(raw).split()
    while len(words) > 1 and fold(words[-1]).strip("+") in QUALITY:
        words.pop()
    return " ".join(words)


def text(raw):
    return " " + " ".join(fold(clean(raw)).split()) + " "


def matches(k, keys):
    return any(k.startswith(x[:-1]) if x.endswith("*") else k == x for x in keys)


def country_of(category, name):
    """Pays d'après la catégorie ou le préfixe du nom (« FR | », « UK: »…)"""
    for s in (category or "", name or ""):
        u = fold(s).upper()
        m = re.match(r"^[^A-Z0-9]*([A-Z]{2,3})(?![A-Z0-9])", u)
        if m:
            c = {"FR": "fr", "FRA": "fr", "CH": "ch", "CHE": "ch", "SUI": "ch", "US": "us", "USA": "us",
                 "UK": "uk", "GB": "uk", "EN": "uk"}.get(m.group(1))
            if c:
                return c
        for pat, c in ((r"FRANCE|FRENCH|FRANCAIS|\bTNT\b", "fr"), (r"SUISSE|SWISS|SCHWEIZ|SWITZERLAND", "ch"),
                       (r"\bUSA\b|UNITED STATES|AMERICA", "us"), (r"\bUK\b|UNITED KINGDOM|BRITISH", "uk")):
            if re.search(pat, u):
                return c
    return "other"


def is_event(c):
    """Emplacements d'événements (« Next | 2026-11-07 | … », « - EVENT 12 - », « FFF TV 3 PPV ») : masqués"""
    n = unicodedata.normalize("NFKC", c["name"])
    return (n.count("|") >= 2 or re.search(r"^\W*(next|live)\s*\||^\W*event\s*\d|^\W*no event|\bppv\b", n, re.I) is not None
            or re.search(r"EVENT|EVENEMENT|EVENMENT|ÉVÉNEMENT|PPV|EQUIPE LIVE", c.get("group", ""), re.I) is not None)


def is_reunion(c):
    """Réunion (catégorie ou nom) : seulement en secours des chaînes gardées"""
    t = fold(c.get("group", "") + " " + c["name"])
    g = fold(c.get("group", ""))
    return "reunion" in t or re.search(r"(^|[^0-9])974([^0-9]|$)", t) is not None or re.match(r"^[^a-z0-9]*re([^a-z0-9]|$)", g) is not None


def build(channels, excluded=(), moved=None, merged=None):
    """channels : dicts {name, country, xtream (bool), ...} → [(id, titre, [chaîne de la grille avec 'streams'])]
    excluded : chaînes (« tnt/TF1 », « theme/<clé> ») et sections (« section/<id> ») retirées, communes avec l'iPhone"""
    excluded = set(excluded)
    blocks = DEF["main"] + DEF["after_themes"]
    slots, themed, order = {}, {}, {}
    # versions Réunion en dernier : seulement en secours, après les flux de métropole
    for c in [c for c in channels if not is_reunion(c)] + [c for c in channels if is_reunion(c)]:
        if shifted(c["name"]):
            continue
        k, cc = key(c["name"]), c.get("country", "other")
        placed = False
        for b in blocks:
            if cc not in b["countries"]:
                continue
            for i, e in enumerate(b["channels"]):
                if matches(k, e["keys"]):
                    slot = slots.setdefault(b["id"], {}).setdefault(i, {"name": e["name"], "logo": "", "streams": [],
                                                                        "section": b["title"], "gid": "%s/%s" % (b["id"], e["name"])})
                    slot["streams"].append(c)
                    slot["logo"] = slot["logo"] or c.get("logo", "")
                    placed = True
                    break
            if placed:
                break
        if placed or cc not in DEF["themes"]["countries"] or not c.get("xtream") or is_reunion(c) or is_event(c):
            continue
        t = text(c["name"])
        for g in DEF["themes"]["groups"]:
            if any(m == "" or m in t for m in g["match"]):
                d = themed.setdefault(g["title"], {})
                if k not in d:
                    d[k] = {"name": display(c["name"]), "logo": c.get("logo", ""), "streams": [], "section": g["title"],
                            "gid": "theme/" + k}
                    order.setdefault(g["title"], []).append(k)
                d[k]["streams"].append(c)
                break

    def ordered(ch):
        ch["streams"].sort(key=lambda s: 0 if s.get("xtream") else 1)
        return ch

    out = []

    def add(sid, title, lst):
        lst = [ordered(c) for c in lst if c["gid"] not in excluded]
        if lst and "section/" + sid not in excluded:
            out.append((sid, title, lst))

    for b in DEF["main"]:
        add(b["id"], b["title"], [slots[b["id"]][i] for i in range(len(b["channels"])) if i in slots.get(b["id"], {})])
    for g in DEF["themes"]["groups"]:
        add("theme/" + g["title"], g["title"],
            sorted((themed[g["title"]][k] for k in order.get(g["title"], [])), key=lambda c: fold(c["name"])))
    for b in DEF["after_themes"]:
        add(b["id"], b["title"], [slots[b["id"]][i] for i in range(len(b["channels"])) if i in slots.get(b["id"], {})])
    return overrides(out, moved or {}, merged or {})


def overrides(secs, moved, merged):
    """Choix faits sur l'iPhone : chaîne en secours d'une autre, ou rangée dans une autre section"""
    secs = [[sid, title, list(lst)] for sid, title, lst in secs]

    def find(k):
        for si, s in enumerate(secs):
            for ci, c in enumerate(s[2]):
                if c["gid"] == k:
                    return si, ci
        return None
    for src, dst in merged.items():
        a, b = find(src), find(dst)
        if a and b and src != dst:
            c = secs[a[0]][2][a[1]]
            secs[b[0]][2][b[1]]["streams"] += c["streams"]
            del secs[a[0]][2][a[1]]
    titles = {b["id"]: b["title"] for b in DEF["main"] + DEF["after_themes"]}
    titles.update({"theme/" + g["title"]: g["title"] for g in DEF["themes"]["groups"]})
    for k, target in moved.items():
        a = find(k)
        if not a:
            continue
        ti = next((i for i, s in enumerate(secs) if s[0] == target), None)
        if ti is None and target in titles:
            secs.append([target, titles[target], []])
            ti = len(secs) - 1
        if ti is not None and ti != a[0]:
            secs[ti][2].append(secs[a[0]][2].pop(a[1]))
    return [(sid, title, lst) for sid, title, lst in secs if lst]
