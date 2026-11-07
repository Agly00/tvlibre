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
           "1080p", "720p", "576p", "50fps", "60fps", "hdr", "plus"}
PREFIX = re.compile(r"^\s*[|\[(]?\s*[A-Za-z]{2,3}\s*[|\])\:\-]\s*")


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def clean(raw):
    s = PREFIX.sub("", raw.strip(), count=1)
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s).strip()
    return s or raw


def key(raw):
    tokens = [t for t in re.split(r"[^0-9a-z+]+", fold(clean(raw))) if t]
    while len(tokens) > 1 and tokens[-1] in QUALITY:
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
        m = re.match(r"^\s*[|\[(]?\s*([A-Z]{2,3})\s*[|\])\:\-]", u)
        if m:
            c = {"FR": "fr", "FRA": "fr", "CH": "ch", "CHE": "ch", "SUI": "ch", "US": "us", "USA": "us",
                 "UK": "uk", "GB": "uk", "EN": "uk"}.get(m.group(1))
            if c:
                return c
        for pat, c in ((r"FRANCE|FRENCH|FRANCAIS", "fr"), (r"SUISSE|SWISS|SCHWEIZ|SWITZERLAND", "ch"),
                       (r"\bUSA\b|UNITED STATES|AMERICA", "us"), (r"\bUK\b|UNITED KINGDOM|BRITISH", "uk")):
            if re.search(pat, u):
                return c
    return "other"


def build(channels, excluded=()):
    """channels : dicts {name, country, xtream (bool), ...} → [(id, titre, [chaîne de la grille avec 'streams'])]
    excluded : chaînes (« tnt/TF1 », « theme/<clé> ») et sections (« section/<id> ») retirées, communes avec l'iPhone"""
    excluded = set(excluded)
    blocks = DEF["main"] + DEF["after_themes"]
    slots, themed, order = {}, {}, {}
    for c in channels:
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
        if placed or cc not in DEF["themes"]["countries"] or not c.get("xtream"):
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
    return out
