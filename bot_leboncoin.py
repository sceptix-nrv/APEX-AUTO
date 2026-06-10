# -*- coding: utf-8 -*-
import base64
import json
import os
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
except ImportError:
    GH_TOKEN = os.environ.get("GH_TOKEN", "")

GH_REPO          = "sceptix-nrv/APEX-AUTO"
GH_FILE_CONFIG   = "apexauto.json"
GH_FILE_ANNONCES = "annonces.json"
GH_FILE_MARCHE   = "marche.json"

# ── CONFIG BOT ────────────────────────────────────────────────────────────────
INTERVALLE_MINUTES = 1
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

_sha = {"config": None, "annonces": None, "marche": None}


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
    try:
        res = requests.put(
            f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
            headers=_gh_headers(), json=body, timeout=15
        )
        if res.ok:
            _sha[sha_key] = res.json()["content"]["sha"]
        else:
            print(f"  [GitHub] Erreur sauvegarde {file} : {res.status_code}")
    except Exception as e:
        print(f"  [GitHub] Erreur sauvegarde {file} : {e}")


def charger_config():
    data = _gh_load(GH_FILE_CONFIG, "config", {})
    actives = [r for r in data.get("recherches", []) if r.get("active", True)]
    print(f"  [GitHub] {len(actives)} recherche(s) active(s)")
    return actives


def charger_annonces():
    return _gh_load(GH_FILE_ANNONCES, "annonces", [])


def sauvegarder_annonces(annonces):
    _gh_put(GH_FILE_ANNONCES, annonces[-500:], "annonces",
            f"[Expert] {datetime.now().strftime('%d/%m %H:%M')}")


def charger_marche():
    return _gh_load(GH_FILE_MARCHE, "marche", [])


def sauvegarder_marche(marche):
    _gh_put(GH_FILE_MARCHE, marche[-5000:], "marche",
            f"[Marche] {datetime.now().strftime('%d/%m %H:%M')}")


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORIQUE LOCAL
# ═══════════════════════════════════════════════════════════════════════════════

def charger_historique():
    if os.path.exists(FICHIER_HISTORIQUE):
        with open(FICHIER_HISTORIQUE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except Exception:
                return set()
    return set()


def sauvegarder_historique(histo):
    liste = list(histo)[-2000:]
    with open(FICHIER_HISTORIQUE, "w", encoding="utf-8") as f:
        json.dump(liste, f, indent=2)


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
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script(
                "return !!document.getElementById('__NEXT_DATA__')"
            )
        )
        time.sleep(1)
    except Exception:
        with open("debug_lbc.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"  [WARN] Timeout {nom} — voir debug_lbc.html")
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
        return ads
    except Exception as e:
        print(f"  [WARN] Parsing {nom} : {e}")
        return []


def _get_attrs(ad):
    result = {}
    for a in ad.get("attributes", []):
        key = a.get("key")
        val = a.get("value") or (a.get("values") or [None])[0]
        if key and val is not None:
            result[key] = val
    return result


def annonce_valide(ad, r):
    attrs = _get_attrs(ad)
    prix  = ad.get("price", [None])[0]
    if r.get("prix_max") and prix and float(prix) > r["prix_max"]:
        return False
    km = attrs.get("mileage")
    if r.get("km_max") and km and int(km) > r["km_max"]:
        return False
    fuel = attrs.get("fuel")
    if r.get("fuel") and r["fuel"] and fuel:
        if FUEL_LABELS.get(str(fuel), "") != r["fuel"]:
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


def formater_message(ad, nom, score):
    attrs = _get_attrs(ad)
    titre = ad.get("subject", "Sans titre")
    prix  = ad.get("price", [None])[0] if ad.get("price") else "N/C"
    lieu  = ad.get("location", {}).get("city", "")
    lien  = f"https://www.leboncoin.fr/voitures/{ad.get('list_id', '')}.htm"
    km    = f"{int(attrs['mileage']):,} km".replace(",", " ") if attrs.get("mileage") else "N/C"
    annee = (attrs.get("regdate") or "")[:4] or "N/C"
    sl    = score_label(score)
    return (
        f"*NOUVELLE ANNONCE - {nom}*\n\n"
        f"*{titre}*\n"
        f"Annee : {annee} | Ville : {lieu}\n"
        f"Kilometrage : {km}\n"
        f"Prix : *{prix} EUR*{sl}\n\n"
        f"[Voir l'annonce]({lien})"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extraire_annonce(ad, nom, score):
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
        "recherche": nom,
        "score":     score,
        "date":      datetime.now().isoformat(timespec="minutes"),
        "statut":    "nouvelle",
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
# SCAN
# ═══════════════════════════════════════════════════════════════════════════════

def scan():
    heure = datetime.now().hour
    if not (HEURE_DEBUT <= heure < HEURE_FIN):
        print(f"[{datetime.now().strftime('%H:%M')}] Mode nuit.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M')}] Scan en cours...")

    recherches = charger_config()
    if not recherches:
        print("  Aucune recherche active.")
        return

    historique          = charger_historique()
    annonces_existantes = charger_annonces()
    marche              = charger_marche()
    ids_annonces        = {a["id"] for a in annonces_existantes}
    ids_marche          = {a["id"] for a in marche}
    nouvelles_annonces  = []
    nouvelles_marche    = []
    total_new           = 0

    driver = None
    try:
        opts = uc.ChromeOptions()
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        driver = uc.Chrome(options=opts, headless=False)

        # Visite homepage pour établir la session DataDome
        print("  Session LeBonCoin...", end=" ", flush=True)
        driver.get("https://www.leboncoin.fr")
        time.sleep(4)
        print("OK")

        for r in recherches:
            nom = r.get("nom", "Recherche")
            url = construire_url(r)
            print(f"  Scan : {nom}...", end=" ", flush=True)

            try:
                annonces  = scraper_url(driver, url, nom)
                valides   = [ad for ad in annonces if annonce_valide(ad, r)]
                tous_prix = [float(ad["price"][0]) for ad in valides
                             if ad.get("price") and ad["price"][0]]

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
                    dest  = DESTINATAIRES.get(r.get("destinataire", "steven"), DESTINATAIRES["steven"])
                    envoyer_telegram(formater_message(ad, nom, score), dest)
                    historique.add(ad_id)
                    if ad_id not in ids_annonces:
                        nouvelles_annonces.append(extraire_annonce(ad, nom, score))
                        ids_annonces.add(ad_id)
                    nouvelles += 1
                    total_new += 1

                print(f"{len(annonces)} scannees, {nouvelles} nouvelle(s)")

            except Exception as e:
                print(f"Erreur : {e}")

            time.sleep(3)

    except Exception as e:
        print(f"  Erreur navigateur : {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    sauvegarder_historique(historique)

    if nouvelles_annonces:
        annonces_existantes.extend(nouvelles_annonces)
        sauvegarder_annonces(annonces_existantes)

    if nouvelles_marche:
        marche.extend(nouvelles_marche)
        sauvegarder_marche(marche)
        print(f"  [Marche] +{len(nouvelles_marche)} entrees ({len(marche)} total)")

    print(f"Termine - {total_new} nouvelle(s) annonce(s).")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("Expert LeBonCoin demarre !")
    print(f"Intervalle : {INTERVALLE_MINUTES} min | Plage : {HEURE_DEBUT}h-{HEURE_FIN}h\n")
    scan()
    while True:
        time.sleep(INTERVALLE_MINUTES * 60)
        scan()


if __name__ == "__main__":
    main()
