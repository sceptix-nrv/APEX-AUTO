# -*- coding: utf-8 -*-
import base64
import json
import os
import random
import time
from datetime import datetime

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── CONFIG TELEGRAM ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "8631165512:AAFtYjnanMCsF_SCwXd_8VEeNX31xM9x5UY"

DESTINATAIRES = {
    "steven": "1460644236",
    "brieuc": "8833772133",
}

# ── CONFIG GITHUB ─────────────────────────────────────────────────────────────
try:
    from secret import GH_TOKEN
    from secret import GROQ_KEY
except ImportError:
    GH_TOKEN  = os.environ.get("GH_TOKEN", "")
    GROQ_KEY  = os.environ.get("GROQ_KEY", "")

GH_REPO          = "sceptix-nrv/APEX-AUTO"
GH_FILE_CONFIG   = "apexauto.json"
GH_FILE_ANNONCES = "annonces.json"
GH_FILE_MARCHE   = "marche.json"
GH_FILE_LOGS     = "logs.json"
GH_FILE_HISTO    = "historique.json"

# ── LOGS ──────────────────────────────────────────────────────────────────────
_logs          = []
_sha_logs      = None
_logs_dirty    = False   # True dès qu'un nouveau log est appended
MAX_LOGS       = 300
import threading


def log(level, msg):
    """level: SYS | SCAN | NET | OK | WARN | ERR | BOT"""
    global _logs_dirty
    entry = {
        "ts":  datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "lvl": level,
        "msg": msg,
    }
    _logs.append(entry)
    _logs_dirty = True
    tag = {"SYS": "[SYS]", "SCAN": "[SCAN]", "NET": "[NET]",
           "OK": "[OK]", "WARN": "[WARN]", "ERR": "[ERR]", "BOT": "[BOT]"}.get(level, f"[{level}]")
    print(f"  {tag} {msg}")


def push_logs():
    global _sha_logs, _logs_dirty
    if not GH_TOKEN:
        return
    payload = _logs[-MAX_LOGS:]
    content = base64.b64encode(
        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    ).decode("utf-8")
    body = {"message": f"[Logs] {datetime.now().strftime('%d/%m %H:%M:%S')}", "content": content}
    if _sha_logs:
        body["sha"] = _sha_logs
    for attempt in range(2):
        try:
            res = requests.put(
                f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_LOGS}",
                headers=_gh_headers(), json=body, timeout=15
            )
            if res.ok:
                _sha_logs = res.json()["content"]["sha"]
                _logs_dirty = False
                return
            elif res.status_code == 409 and attempt == 0:
                # SHA désynchronisé (ex: clear depuis le site) — on re-fetch et on réessaie
                r2 = requests.get(
                    f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_LOGS}",
                    headers=_gh_headers(), timeout=10
                )
                if r2.ok:
                    _sha_logs = r2.json()["sha"]
                    body["sha"] = _sha_logs
            else:
                print(f"  [Logs] Erreur push {res.status_code} : {res.text[:200]}")
                return
        except Exception as e:
            print(f"  [Logs] Exception push : {e}")
            return


def _logs_pusher_thread():
    """Pousse les logs sur GitHub toutes les 15s si de nouveaux logs existent."""
    while True:
        time.sleep(15)
        if _logs_dirty:
            push_logs()


def _load_logs_sha():
    global _sha_logs
    try:
        res = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_LOGS}",
            headers=_gh_headers(), timeout=10
        )
        if res.ok:
            j = res.json()
            _sha_logs = j["sha"]
            existing  = json.loads(base64.b64decode(j["content"]).decode("utf-8"))
            _logs.extend(existing[-MAX_LOGS:])
    except Exception:
        pass

# ── CONFIG BOT ────────────────────────────────────────────────────────────────
INTERVALLE_MINUTES = 10
HEURE_DEBUT        = 0
HEURE_FIN          = 24
FICHIER_HISTORIQUE = "annonces_vues.json"

# ── RÉFÉRENTIELS ──────────────────────────────────────────────────────────────
VILLES = {
    "nancy":  {"lat": 48.6954, "lng": 6.1844,  "nom": "Nancy"},
    "orthez": {"lat": 43.4894, "lng": -0.7728, "nom": "Orthez"},
}

FUEL_CODES  = {"diesel": "2", "essence": "1", "electrique": "3", "hybride": "5"}
FUEL_LABELS = {"2": "diesel", "1": "essence", "3": "electrique", "5": "hybride"}

_sha = {"config": None, "annonces": None, "marche": None, "histo": None}


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB
# ═══════════════════════════════════════════════════════════════════════════════

def _gh_headers():
    return {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def _gh_load(file, sha_key, default):
    try:
        res = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
            headers=_gh_headers(), timeout=10
        )
        if res.status_code == 404:
            return default
        if not res.ok:
            print(f"  [GitHub] Erreur {res.status_code} sur {file}")
            return default
        j = res.json()
        _sha[sha_key] = j["sha"]
        return json.loads(base64.b64decode(j["content"]).decode("utf-8"))
    except Exception as e:
        print(f"  [GitHub] Erreur lecture {file} : {e}")
        return default


def _gh_put(file, data, sha_key, message):
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    ).decode("utf-8")
    body = {"message": message, "content": content}
    if _sha[sha_key]:
        body["sha"] = _sha[sha_key]
    for attempt in range(2):
        try:
            res = requests.put(
                f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
                headers=_gh_headers(), json=body, timeout=15
            )
            if res.ok:
                _sha[sha_key] = res.json()["content"]["sha"]
                return
            elif res.status_code == 409 and attempt == 0:
                # SHA désynchronisé — re-fetch le vrai SHA et réessaie
                r2 = requests.get(
                    f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
                    headers=_gh_headers(), timeout=10
                )
                if r2.ok:
                    _sha[sha_key] = r2.json()["sha"]
                    body["sha"] = _sha[sha_key]
            else:
                print(f"  [GitHub] Erreur sauvegarde {file} : {res.status_code}")
                return
        except Exception as e:
            print(f"  [GitHub] Erreur sauvegarde {file} : {e}")
            return


def charger_config():
    log("SYS", "Chargement config depuis GitHub...")
    data    = _gh_load(GH_FILE_CONFIG, "config", {})
    actives = [r for r in data.get("recherches", []) if r.get("active", True)]
    log("SYS", f"{len(actives)} recherche(s) active(s) chargee(s)")
    return actives


def charger_annonces():
    log("SYS", "Chargement index annonces...")
    return _gh_load(GH_FILE_ANNONCES, "annonces", [])


def sauvegarder_annonces(annonces):
    log("SYS", f"Sauvegarde annonces — {len(annonces)} entrees")
    _gh_put(GH_FILE_ANNONCES, annonces[-500:], "annonces",
            f"[Expert] {datetime.now().strftime('%d/%m %H:%M')}")


def charger_marche():
    log("SYS", "Chargement base marche...")
    return _gh_load(GH_FILE_MARCHE, "marche", [])


def sauvegarder_marche(marche):
    log("SYS", f"Sauvegarde marche — {len(marche)} entrees")
    _gh_put(GH_FILE_MARCHE, marche[-5000:], "marche",
            f"[Marche] {datetime.now().strftime('%d/%m %H:%M')}")


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORIQUE — GitHub (+ fallback local pour compatibilité)
# ═══════════════════════════════════════════════════════════════════════════════

def charger_historique():
    log("SYS", "Chargement historique annonces vues...")
    ids = set()
    # 1. Charge depuis GitHub
    data = _gh_load(GH_FILE_HISTO, "histo", [])
    ids.update(str(i) for i in data)
    # 2. Fusionne avec le fichier local s'il existe (migration)
    if os.path.exists(FICHIER_HISTORIQUE):
        try:
            with open(FICHIER_HISTORIQUE, "r", encoding="utf-8") as f:
                local = json.load(f)
                ids.update(str(i) for i in local)
        except Exception:
            pass
    log("SYS", f"Historique : {len(ids)} annonces deja vues")
    return ids


def sauvegarder_historique(histo):
    liste = list(histo)[-5000:]
    # Sauvegarde GitHub
    _gh_put(GH_FILE_HISTO, liste, "histo",
            f"[Histo] {datetime.now().strftime('%d/%m %H:%M')}")
    # Sauvegarde locale (backup)
    try:
        with open(FICHIER_HISTORIQUE, "w", encoding="utf-8") as f:
            json.dump(liste, f, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# LEBONCOIN
# ═══════════════════════════════════════════════════════════════════════════════

def construire_url(r):
    ville   = VILLES.get(r.get("ville", "nancy"), VILLES["nancy"])
    rayon_m = int(r.get("rayon", 100)) * 1000
    location = f"{ville['nom']}__{ville['lat']}_{ville['lng']}_0_{rayon_m}"
    params = [
        "category=2",
        f"text={requests.utils.quote(r.get('keywords', ''))}",
        f"locations={location}",
        "sort=time", "order=desc",
    ]
    if r.get("prix_max"):
        params.append(f"price=max-{int(r['prix_max'])}")
    if r.get("km_max"):
        params.append(f"mileage=max-{int(r['km_max'])}")
    if r.get("fuel") and r["fuel"] in FUEL_CODES:
        params.append(f"fuel={FUEL_CODES[r['fuel']]}")
    return "https://www.leboncoin.fr/recherche?" + "&".join(params)


def scraper_url(driver, url, nom):
    log("NET", f"GET {url[:80]}...")
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script(
                "return !!document.getElementById('__NEXT_DATA__')"
            )
        )
        time.sleep(random.uniform(1.5, 3))
        driver.execute_script("window.scrollTo({top: random.randint(200,600), behavior: 'smooth'})"
                              .replace("random.randint(200,600)", str(random.randint(200, 600))))
        time.sleep(random.uniform(1, 2))
        log("NET", f"Page chargee — extraction __NEXT_DATA__")
    except Exception:
        with open("debug_lbc.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log("WARN", f"Timeout sur '{nom}' — DataDome probable, dump HTML sauvegarde")
        return []

    try:
        raw = driver.execute_script(
            "return JSON.parse(document.getElementById('__NEXT_DATA__').textContent)"
        )
        props = raw.get("props", {}).get("pageProps", {})
        ads = (
            props.get("searchData") or
            (props.get("hydrationData") or {}).get("searchData") or {}
        ).get("ads", [])
        log("NET", f"Parsing OK — {len(ads)} annonce(s) brute(s) recues")
        return ads
    except Exception as e:
        log("ERR", f"Parsing JSON echoue sur '{nom}' : {e}")
        return []


def _get_attrs(ad):
    result = {}
    for a in ad.get("attributes", []):
        key = a.get("key")
        val = a.get("value") or (a.get("values") or [None])[0]
        if key and val is not None:
            result[key] = val
    return result


KM_MAX_ESSENCE = 180_000   # essence au-delà = invendable

def annonce_valide(ad, r):
    attrs = _get_attrs(ad)
    prix  = ad.get("price", [None])[0]
    km    = attrs.get("mileage")
    fuel  = attrs.get("fuel")

    if r.get("prix_max") and prix and float(prix) > r["prix_max"]:
        return False
    if r.get("km_max") and km and int(km) > r["km_max"]:
        return False
    if r.get("fuel") and r["fuel"] and fuel:
        if FUEL_LABELS.get(str(fuel), "") != r["fuel"]:
            return False
    # Règle globale : essence avec km > 180 000 → rejeté
    if km and fuel and FUEL_LABELS.get(str(fuel), "") == "essence":
        if int(km) > KM_MAX_ESSENCE:
            return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_score(prix, tous_prix):
    if not tous_prix or len(tous_prix) < 2 or prix is None:
        return None
    median = sorted(tous_prix)[len(tous_prix) // 2]
    if not median:
        return None
    return max(-99, min(99, round((median - float(prix)) / median * 100)))


def score_label(score):
    if score is None:  return ""
    if score >= 20:    return f"\n{chr(55356)}{chr(57056)} Excellente affaire ({score:+d}% vs marche)"
    if score >= 10:    return f"\n{chr(55357)}{chr(56320)} Bonne affaire ({score:+d}% vs marche)"
    if score >= 0:     return f"\n{chr(55356)}{chr(57104)} Prix correct ({score:+d}% vs marche)"
    return                    f"\n{chr(55356)}{chr(57088)} Surevalue ({score:+d}% vs marche)"


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def envoyer_telegram(msg, chat_id):
    for _ in range(3):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg,
                      "parse_mode": "Markdown", "disable_web_page_preview": False},
                timeout=10
            )
            return
        except Exception:
            time.sleep(2)


FUEL_EMOJI = {"diesel": "🛢", "essence": "⛽", "electrique": "⚡", "hybride": "🔋"}

def formater_message(ad, nom, score, ia=None):
    attrs  = _get_attrs(ad)
    titre  = ad.get("subject", "Sans titre")
    prix   = ad.get("price", [None])[0] if ad.get("price") else None
    lieu   = ad.get("location", {}).get("city", "")
    cp     = ad.get("location", {}).get("zipcode", "")
    lien   = f"https://www.leboncoin.fr/voitures/{ad.get('list_id', '')}.htm"
    km     = f"{int(attrs['mileage']):,} km".replace(",", " ") if attrs.get("mileage") else "N/C"
    annee  = (attrs.get("regdate") or "")[:4] or "N/C"
    fuel_k = FUEL_LABELS.get(str(attrs.get("fuel", "")), "")
    fuel_e = FUEL_EMOJI.get(fuel_k, "")
    fuel_s = f"{fuel_e} {fuel_k.capitalize()}" if fuel_k else "N/C"
    prix_s = f"{int(float(prix)):,} €".replace(",", " ") if prix else "N/C"

    # Score
    if score is None:
        score_s = ""
    elif score >= 20:
        score_s = f"  🔥 *{score:+d}% vs marché*"
    elif score >= 10:
        score_s = f"  ✅ *{score:+d}% vs marché*"
    elif score >= 0:
        score_s = f"  〰️ {score:+d}% vs marché"
    else:
        score_s = f"  📈 {score:+d}% vs marché"

    # Avis expert IA
    if ia:
        verdict  = ia.get("verdict", "OK")
        avis     = ia.get("avis", "").strip()
        fiab     = ia.get("fiabilite_moteur", "").strip()
        if verdict == "ATTENTION":
            # redflag uniquement si redflag=true, sinon on n'affiche que l'avis
            redflag = (ia.get("redflag_detail") or "").strip() if ia.get("redflag") else ""
            ia_s  = f"⚠️ *Expert :* {redflag}\n💬 {avis}" if redflag else f"⚠️ *Expert :* {avis}"
        else:
            ia_s  = f"🧠 *Expert :* {avis}" if avis else "🧠 *Expert :* Pas d'infos suffisantes."
        if fiab:
            ia_s += f"\n🔧 *Moteur :* {fiab}"
    else:
        ia_s = "🧠 *Expert :* Analyse IA non disponible."

    lieu_s = f"{lieu} ({cp[:2]})" if lieu and cp else lieu or "N/C"

    return (
        f"🚗 *{titre}*\n"
        f"\n"
        f"💰  *{prix_s}*{score_s}\n"
        f"📅  {annee}   ·   🛣  {km}   ·   {fuel_s}\n"
        f"📍  {lieu_s}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{ia_s}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"🔍 _{nom}_\n"
        f"👉 [Voir l'annonce]({lien})"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extraire_annonce(ad, nom, score, ia=None):
    attrs = _get_attrs(ad)
    prix  = ad.get("price", [None])[0] if ad.get("price") else None
    an    = (attrs.get("regdate") or "")[:4]
    return {
        "id":        str(ad.get("list_id", "")),
        "titre":     ad.get("subject", ""),
        "prix":      float(prix) if prix else None,
        "km":        int(attrs["mileage"]) if attrs.get("mileage") else None,
        "annee":     an,
        "ville":     ad.get("location", {}).get("city", ""),
        "lien":      f"https://www.leboncoin.fr/voitures/{ad.get('list_id', '')}.htm",
        "recherche":  nom,
        "score":      score,
        "date":       datetime.now().isoformat(timespec="minutes"),
        "statut":     "nouvelle",
        "ia_verdict": ia.get("verdict") if ia else None,
        "ia_detail":  (ia.get("redflag_detail") or ia.get("raison_correspond", "")) if ia else None,
        "ia_avis":    ia.get("avis", "") if ia else None,
        "ia_moteur":  ia.get("fiabilite_moteur", "") if ia else None,
    }


def extraire_marche(ad, nom):
    attrs = _get_attrs(ad)
    prix  = ad.get("price", [None])[0] if ad.get("price") else None
    an    = (attrs.get("regdate") or "")[:4]
    return {
        "id":        str(ad.get("list_id", "")),
        "titre":     ad.get("subject", ""),
        "prix":      float(prix) if prix else None,
        "km":        int(attrs["mileage"]) if attrs.get("mileage") else None,
        "annee":     int(an) if an else None,
        "carburant": FUEL_LABELS.get(str(attrs.get("fuel", "")), ""),
        "ville":     ad.get("location", {}).get("city", ""),
        "cp":        ad.get("location", {}).get("zipcode", ""),
        "recherche": nom,
        "date":      datetime.now().strftime("%Y-%m-%d"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSE IA (Groq — gratuit, 14 400 req/jour, llama-3.1-8b-instant)
# ═══════════════════════════════════════════════════════════════════════════════

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

PROMPT_ANALYSE = """Tu es un expert en achat de voitures d'occasion en France. Analyse cette annonce LeBonCoin.

Recherche : "{nom}" (mots-clés : {keywords})
Titre : {titre}
Prix : {prix} EUR | Kilométrage : {km} km | Année : {annee} | Carburant : {fuel}

Réponds UNIQUEMENT en JSON valide, sans markdown :
{{"correspond": true/false, "raison_correspond": "...", "redflag": true/false, "redflag_detail": "...", "verdict": "OK", "avis": "...", "fiabilite_moteur": "..."}}

Règles :
- "correspond" = le titre correspond EXACTEMENT au modèle recherché (cherche "206" → "207", "206+", "206 SW" = false)
- "redflag" = true SEULEMENT si le titre mentionne : épave, accidenté, pour pièces, moteur HS, papiers étrangers
- "verdict" : "OK", "ATTENTION" ou "ELIMINER"
- "avis" : commence par une recommandation claire ("Fonce", "Intéressant", "Méfie-toi", "À négocier", etc.) puis commente le rapport km/année/prix en 1 phrase. Pas de description dispo, base-toi sur les chiffres.
- "fiabilite_moteur" : 1-2 phrases sur la fiabilité du moteur identifié dans le titre (ex: "1.4 HDI fiable et économique" ou "1.6 THP fragile, surveille la distribution"). Si moteur non identifiable, dis-le."""


def analyser_annonce_ia(ad, r, driver=None):
    """Retourne dict avec correspond/redflag/verdict, ou None si IA indisponible."""
    if not GROQ_KEY:
        return None

    attrs  = _get_attrs(ad)
    titre  = ad.get("subject", "")
    prix   = ad.get("price", [None])[0] if ad.get("price") else "?"
    km     = attrs.get("mileage", "?")
    annee  = (attrs.get("regdate") or "")[:4] or "?"
    fuel   = FUEL_LABELS.get(str(attrs.get("fuel", "")), "?")
    carros = attrs.get("vehicle_type", "")

    prompt = PROMPT_ANALYSE.format(
        nom=r.get("nom", ""), keywords=r.get("keywords", ""),
        titre=titre, prix=prix, km=km, annee=annee, fuel=fuel, carros=carros,
    )

    for attempt in range(3):
        try:
            res = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_KEY}",
                         "Content-Type": "application/json"},
                json={"model": GROQ_MODEL, "temperature": 0.1,
                      "messages": [{"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
                timeout=15
            )
            if res.status_code == 429:
                wait = 10 * (attempt + 1)
                log("WARN", f"Groq rate limit — attente {wait}s ({attempt+1}/3)")
                time.sleep(wait)
                continue
            if not res.ok:
                log("WARN", f"Groq {res.status_code} : {res.text[:120]}")
                return None
            text = res.json()["choices"][0]["message"]["content"]
            return json.loads(text)
        except Exception as e:
            log("WARN", f"Groq erreur : {e}")
            return None
    log("WARN", "Groq indisponible apres 3 tentatives")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN
# ═══════════════════════════════════════════════════════════════════════════════

def scan():
    heure = datetime.now().hour
    if not (HEURE_DEBUT <= heure < HEURE_FIN):
        log("SYS", f"Mode nuit actif ({heure}h — plage {HEURE_DEBUT}h-{HEURE_FIN}h). Scan suspendu.")
        push_logs()
        return

    log("SYS", f"=== CYCLE DE SCAN INITIE [{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] ===")

    recherches = charger_config()
    if not recherches:
        log("WARN", "Aucune recherche active trouvee dans la config. Arret.")
        push_logs()
        return

    historique          = charger_historique()
    annonces_existantes = charger_annonces()
    marche              = charger_marche()
    ids_annonces        = {a["id"] for a in annonces_existantes}
    ids_marche          = {a["id"] for a in marche}
    nouvelles_annonces  = []
    nouvelles_marche    = []
    total_new           = 0

    log("SYS", f"Base chargee — {len(historique)} vues | {len(annonces_existantes)} annonces | {len(marche)} entrees marche")

    for idx, r in enumerate(recherches):
        nom = r.get("nom", "Recherche")
        url = construire_url(r)

        if idx > 0:
            pause = random.uniform(45, 90)
            log("BOT", f"Pause anti-detection {int(pause)}s avant prochaine session Chrome...")
            time.sleep(pause)

        log("SCAN", f"--- Recherche [{idx+1}/{len(recherches)}] : {nom} ---")

        driver = None
        try:
            w = random.randint(1200, 1400)
            h = random.randint(800, 1000)
            log("BOT", f"Lancement Chrome stealth — viewport {w}x{h}")
            opts = uc.ChromeOptions()
            opts.add_argument(f"--window-size={w},{h}")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            driver = uc.Chrome(options=opts, headless=False)

            log("BOT", "Navigation homepage LeBonCoin — etablissement session...")
            driver.get("https://www.leboncoin.fr")
            t_home = random.uniform(5, 8)
            time.sleep(t_home)
            scroll_y = random.randint(100, 400)
            driver.execute_script(f"window.scrollTo({{top: {scroll_y}, behavior: 'smooth'}})")
            time.sleep(random.uniform(2, 4))
            driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'})")
            time.sleep(random.uniform(1, 3))
            log("BOT", f"Session etablie ({t_home:.1f}s, scroll {scroll_y}px) — fingerprint OK")

            annonces  = scraper_url(driver, url, nom)
            valides   = [ad for ad in annonces if annonce_valide(ad, r)]
            tous_prix = [float(ad["price"][0]) for ad in valides
                         if ad.get("price") and ad["price"][0]]

            log("SCAN", f"{len(annonces)} brutes → {len(valides)} correspondent aux criteres")
            if tous_prix:
                median = sorted(tous_prix)[len(tous_prix) // 2]
                log("SCAN", f"Prix marche : min {int(min(tous_prix))}€ | median {int(median)}€ | max {int(max(tous_prix))}€")

            for ad in valides:
                ad_id = str(ad.get("list_id", ""))
                if ad_id and ad_id not in ids_marche:
                    nouvelles_marche.append(extraire_marche(ad, nom))
                    ids_marche.add(ad_id)

            nouvelles = 0
            for ad in reversed(valides):
                ad_id = str(ad.get("list_id", ""))
                if not ad_id or ad_id in historique:
                    continue
                prix  = ad.get("price", [None])[0] if ad.get("price") else None
                score = calculer_score(prix, tous_prix)
                titre = ad.get("subject", "?")[:50]

                # ── Analyse IA ────────────────────────────────────────────────
                ia = None
                if GROQ_KEY:
                    log("SYS", f"Analyse IA : {titre[:40]}...")
                    ia = analyser_annonce_ia(ad, r)
                    if ia:
                        verdict = ia.get("verdict", "OK")
                        if not ia.get("correspond", True):
                            log("WARN", f"IA ELIMINE (modele) : {titre[:40]} — {ia.get('raison_correspond','')}")
                            historique.add(ad_id)   # on ne veut plus le revoir
                            continue
                        if ia.get("redflag", False):
                            log("WARN", f"IA REDFLAG : {titre[:40]} — {ia.get('redflag_detail','')}")
                            historique.add(ad_id)
                            continue
                        if verdict == "ATTENTION":
                            log("WARN", f"IA ATTENTION : {titre[:40]} — {ia.get('redflag_detail','')}")
                        else:
                            log("OK", f"IA OK : {titre[:40]}")

                # ── Telegram ──────────────────────────────────────────────────
                dest = DESTINATAIRES.get(r.get("destinataire", "steven"), DESTINATAIRES["steven"])
                envoyer_telegram(formater_message(ad, nom, score, ia), dest)
                historique.add(ad_id)
                score_str = f" | score {score:+d}%" if score is not None else ""
                log("OK", f"NOUVELLE : [{ad_id}] {titre} — {prix}€{score_str} → Telegram envoye")
                if ad_id not in ids_annonces:
                    nouvelles_annonces.append(extraire_annonce(ad, nom, score, ia))
                    ids_annonces.add(ad_id)
                nouvelles += 1
                total_new += 1

            if nouvelles == 0:
                log("SCAN", f"Aucune nouvelle annonce — {len(valides)} deja connues")
            else:
                log("OK", f"{nouvelles} alerte(s) envoyee(s) pour '{nom}'")

        except Exception as e:
            log("ERR", f"Exception sur '{nom}' : {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                    log("BOT", "Session Chrome fermee proprement")
                    time.sleep(2)
                except Exception:
                    log("WARN", "Fermeture Chrome forcee")

    sauvegarder_historique(historique)

    if nouvelles_annonces:
        annonces_existantes.extend(nouvelles_annonces)
        sauvegarder_annonces(annonces_existantes)

    if nouvelles_marche:
        marche.extend(nouvelles_marche)
        sauvegarder_marche(marche)
        log("SYS", f"Base marche mise a jour +{len(nouvelles_marche)} entrees ({len(marche)} total)")

    log("SYS", f"=== SCAN TERMINE — {total_new} nouvelle(s) | prochain cycle dans {INTERVALLE_MINUTES} min ===")
    push_logs()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("Expert LeBonCoin demarre !")
    print(f"Intervalle : {INTERVALLE_MINUTES} min | Plage : {HEURE_DEBUT}h-{HEURE_FIN}h\n")
    _load_logs_sha()
    log("SYS", f"=== APEX AUTO EXPERT DEMARRE — v2 — intervalle {INTERVALLE_MINUTES}min ===")
    # Thread de push continu (toutes les 15s)
    t = threading.Thread(target=_logs_pusher_thread, daemon=True)
    t.start()
    scan()
    while True:
        time.sleep(INTERVALLE_MINUTES * 60)
        scan()


if __name__ == "__main__":
    main()
