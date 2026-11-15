"""Recolector diario de MakerWorld.

Busca diseños recientes de Hobby & DIY y Toys & Games, se queda con los que
tienen un perfil para Bambu A1 (sin AMS, o con AMS y <= 7 h), los puntúa
según calidad y según tus Guardar/Descartar, y escribe docs/data/feed.json.

Solo usa la librería estándar de Python.
"""

import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
FEED_PATH = os.path.join(DATA, "feed.json")
STATE_PATH = os.path.join(DATA, "state.json")
SEEN_PATH = os.path.join(ROOT, "data", "seen.json")

# --- Configuración -----------------------------------------------------------
CATEGORIES = {300: "Hobby & DIY", 800: "Toys & Games"}
PICKS_PER_DAY = 20
PRINTERS_OK = {"A1", "A1 mini"}       # perfiles que caben en la A1
MAX_AMS_HOURS = 7.0                    # con AMS, solo si dura <= 7 h
MAX_NOAMS_HOURS = 10.0                 # sin AMS, solo si dura <= 10 h
MIN_AGE_HOURS = 18                     # dejar que acumule likes antes de juzgar
MAX_AGE_DAYS = 5                       # diseños "nuevos"
TRENDING_MAX_AGE_DAYS = 30             # tendencias recientes
NEW_PAGES_PER_CAT = 15                 # 100 por página
TRENDING_PAGES_PER_CAT = 2
MAX_DETAIL_CALLS = 160
FEED_KEEP_DAYS = 7
DELAY = (0.8, 1.6)                     # pausa entre peticiones (segundos)

API = "https://makerworld.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://makerworld.com/en",
}

NOW = datetime.now(timezone.utc)
STOPWORDS = set("""a an and the of for with to in on by no ams print printed printing
3d model models v2 v3 multi multicolor color colors part parts mini kit set
de la el los las y para con sin en un una""".split())


def log(*a):
    print(*a, flush=True)


def get_json(path, params=None, retries=3):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            time.sleep(random.uniform(*DELAY))
            return json.loads(body)
        except urllib.error.HTTPError as e:
            log(f"  HTTP {e.code} en {url}")
            if e.code in (403, 429, 503):
                time.sleep(10 * (attempt + 1))
            else:
                time.sleep(2)
        except Exception as e:  # noqa: BLE001
            log(f"  error {e!r} en {url}")
            time.sleep(3)
    return None


def parse_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return NOW


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def words(title):
    return {w for w in re.findall(r"[a-záéíóúñ]{3,}", (title or "").lower())
            if w not in STOPWORDS}


# --- 1. Candidatos ------------------------------------------------------------
def list_candidates():
    found = {}
    for cat in CATEGORIES:
        for order, pages in (("newUploads", NEW_PAGES_PER_CAT),
                             ("hotScore", TRENDING_PAGES_PER_CAT)):
            for page in range(pages):
                j = get_json("/search-service/select/design2", {
                    "designType": 0, "orderBy": order, "categories": cat,
                    "limit": 100, "offset": page * 100,
                })
                if j is None:
                    if not found:
                        raise SystemExit("MakerWorld no respondió a la lista. "
                                         "¿Bloqueo desde la nube?")
                    break
                hits = j.get("hits") or []
                oldest = None
                for h in hits:
                    created = parse_time(h.get("createTime", ""))
                    oldest = created
                    h["_cat"] = cat
                    h["_age_h"] = (NOW - created).total_seconds() / 3600
                    found.setdefault(h["id"], h)
                log(f"lista cat={cat} {order} p{page}: {len(hits)} diseños")
                if order == "newUploads" and oldest and \
                        NOW - oldest > timedelta(days=MAX_AGE_DAYS):
                    break
    return list(found.values())


def quality(h):
    """Interés por día, suavizado: favorece diseños que ganan likes rápido."""
    likes = h.get("likeCount", 0)
    dl = h.get("downloadCount", 0)
    prints = h.get("printCount", 0)
    coll = h.get("collectionCount", 0)
    days = max(h.get("_age_h", 24) / 24, 0.75)
    raw = likes * 1.0 + dl * 0.35 + prints * 1.5 + coll * 0.6
    return math.log1p(raw / days ** 0.8)


# --- 2. Aprendizaje de gustos ------------------------------------------------
def build_prefs(state):
    feats = defaultdict(lambda: [0, 0])  # [guardados, descartados]
    for d in state.get("decisions", {}).values():
        action = d.get("action")
        if action not in ("save", "discard", "printed"):
            continue
        idx = 1 if action == "discard" else 0
        for f in d.get("features", []):
            feats[f][idx] += 1
    # Peso suavizado en [-1, 1]
    return {f: (s - d) / (s + d + 2) for f, (s, d) in feats.items()}


def features(h, detail=None):
    fs = set()
    for t in (h.get("tags") or [])[:25]:
        fs.add("tag:" + str(t).lower().strip())
    for w in words(h.get("title")):
        fs.add("w:" + w)
    fs.add(f"cat:{h.get('_cat')}")
    creator = (h.get("designCreator") or {}).get("uid")
    if creator:
        fs.add(f"user:{creator}")
    if detail:
        for c in detail.get("categories") or []:
            fs.add(f"cat:{c.get('id')}")
    return sorted(fs)


def pref_score(fs, prefs):
    vals = [prefs[f] for f in fs if f in prefs]
    if not vals:
        return 0.0
    # media con algo de peso por cantidad de coincidencias
    return sum(vals) / (len(vals) + 3) * min(len(vals), 6) / 2


# --- 3. Detalle: ¿tiene perfil para A1? ---------------------------------------
def best_a1_profile(design_id):
    d = get_json(f"/design-service/design/{design_id}")
    if not d:
        return None, None
    best = None
    for inst in d.get("instances") or []:
        mi = (inst.get("extention") or {}).get("modelInfo") or {}
        printer = (mi.get("compatibility") or {}).get("devProductName")
        if printer not in PRINTERS_OK:
            continue
        hours = (inst.get("prediction") or 0) / 3600
        ams = bool(inst.get("needAms"))
        if hours <= 0:
            continue
        if hours > (MAX_AMS_HOURS if ams else MAX_NOAMS_HOURS):
            continue
        plates = len(mi.get("plates") or []) or 1
        cand = {
            "profileId": inst.get("id"),
            "printer": printer,
            "hours": round(hours, 2),
            "ams": ams,
            "plates": plates,
            "colors": inst.get("materialColorCnt") or 0,
            "profileTitle": inst.get("title") or "",
        }
        key = (cand["ams"], cand["printer"] != "A1", cand["hours"])
        if best is None or key < best[0]:
            best = (key, cand)
    return (best[1] if best else None), d


# --- 4. Principal -------------------------------------------------------------
def main():
    state = load(STATE_PATH, {"decisions": {}})
    decisions = state.get("decisions", {})
    seen = set(load(SEEN_PATH, []))
    feed = load(FEED_PATH, {"days": []})
    prefs = build_prefs(state)
    log(f"preferencias aprendidas: {len(prefs)} rasgos, "
        f"{len(decisions)} decisiones")

    cands = list_candidates()
    log(f"candidatos totales: {len(cands)}")

    pool = []
    for h in cands:
        sid = str(h["id"])
        if sid in decisions or sid in seen or h.get("nsfw"):
            continue
        age_h = h["_age_h"]
        if age_h < MIN_AGE_HOURS or age_h > TRENDING_MAX_AGE_DAYS * 24:
            continue
        fs = features(h)
        h["_pre"] = quality(h) + 1.5 * pref_score(fs, prefs)
        pool.append(h)
    pool.sort(key=lambda x: x["_pre"], reverse=True)
    log(f"tras filtros básicos: {len(pool)}; reviso detalle de los mejores")

    picks = []
    checked = 0
    for h in pool:
        if len(picks) >= PICKS_PER_DAY or checked >= MAX_DETAIL_CALLS:
            break
        checked += 1
        prof, detail = best_a1_profile(h["id"])
        seen.add(str(h["id"]))  # no volver a revisar el mismo diseño
        if not prof:
            continue
        fs = features(h, detail)
        score = quality(h) + 1.5 * pref_score(fs, prefs) + (0.4 if not prof["ams"] else 0)
        cover = h.get("cover") or (detail or {}).get("coverUrl") or ""
        cats = [c.get("name") for c in (detail or {}).get("categories") or []]
        picks.append({
            "id": h["id"],
            "title": h.get("title") or "",
            "url": f"https://makerworld.com/en/models/{h['id']}#profileId-{prof['profileId']}",
            "cover": cover,
            "creator": (h.get("designCreator") or {}).get("name", ""),
            "likes": h.get("likeCount", 0),
            "downloads": h.get("downloadCount", 0),
            "prints": h.get("printCount", 0),
            "created": h.get("createTime", ""),
            "category": CATEGORIES.get(h["_cat"], ""),
            "categories": cats,
            "tags": (h.get("tags") or [])[:12],
            "features": fs,
            "score": round(score, 3),
            **prof,
        })
        log(f"  ✓ {h['id']} {prof['printer']} {prof['hours']}h "
            f"{'AMS' if prof['ams'] else 'sin AMS'} — {h.get('title','')[:50]}")

    picks.sort(key=lambda p: p["score"], reverse=True)
    log(f"detalle revisado: {checked}; seleccionados: {len(picks)}")

    today = NOW.strftime("%Y-%m-%d")
    days = [d for d in feed.get("days", []) if d.get("date") != today]
    days.insert(0, {"date": today, "items": picks})
    cutoff = (NOW - timedelta(days=FEED_KEEP_DAYS)).strftime("%Y-%m-%d")
    days = [d for d in days if d["date"] >= cutoff]
    save(FEED_PATH, {"updated": NOW.isoformat(timespec="seconds"), "days": days})
    save(SEEN_PATH, sorted(seen, key=int)[-20000:])
    if not os.path.exists(STATE_PATH):
        save(STATE_PATH, {"decisions": {}})

    if not picks:
        log("Ningún diseño pasó los filtros hoy.")
        sys.exit(0 if checked else 1)


if __name__ == "__main__":
    main()
