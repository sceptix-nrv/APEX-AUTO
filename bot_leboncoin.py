import asyncio
import base64
import json
import os
import time
from datetime import datetime

import requests
from camoufox.async_api import AsyncCamoufox

# ── CONFIG TELEGRAM ──────────────────────────────────────────────────────────
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

# ── ÉTAT GLOBAL (SHA pour éviter les conflits GitHub) ────────────────────────
_sha = {"config": None, "annonces": None, "marche": None}


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB
# ═══════════════════════════════════════════════════════════════════════════════

def _gh_headers():
    return {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def _gh_get(file):
    return requests.get(
        f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
        headers=_gh_headers(), timeout=10
    )


def _gh_put(file, data, sha_key, message):
    content = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    ).decode("utf-8")
    body = {"message": message, "content": content}
    if _sha[sha_key]:
        body["sha"] = _sha[sha_key]
    res = requests.put(
        f"https://api.github.com/repos/{GH_REPO}/contents/{file}",
        headers=_gh_headers(), json=body, timeout=15
    )
    if res.ok:
        _sha[sha_key] = res.json()["content"]["sha"]
    return res


def _gh_load(file, sha_key, default):
    try:
        res = _gh_get(file)
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


def charger_config():
    data = _gh_load(GH_FILE_CONFIG, "config", {})
    recherches = data.get("recherches", [])
    actives = [r for r in recherches if r.get("active", True)]
    print(f"  [GitHub] {len(actives)} recherche(s) active(s)")
    return actives


def charger_annonces():
    return _gh_load(GH_FILE_ANNONCES, "annonces", [])


def sauvegarder_annonces(annonces):
    if len(annonces) > 500:
        annonces = annonces[-500:]
    res = _gh_put(GH_FILE_ANNONCES, annonces, "annonces",
                  f"[Expert] {datetime.now().strftime('%d/%m %H:%M')}")
    if not res.ok:
        print(f"  [GitHub] Erreur sauvegarde annonces : {res.status_code}")


def charger_marche():
    return _gh_load(GH_FILE_MARCHE, "marche", [])


def sauvegarder_marche(marche):
    if len(marche) > 5000:
        marche = marche[-5000:]
    _gh_put(GH_FILE_MARCHE, marche, "marche",
            f"[Marché] {datetime.now().strftime('%d/%m %H:%M')}")


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
    liste = list(histo)
    if len(liste) > 2000:
        liste = liste[-2000:]
    with open(FICHIER_HISTORIQUE, "w", encoding="utf-8") as f:
        json.dump(liste, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# LEBONCOIN — URL
# ═══════════════════════════════════════════════════════════════════════════════

def construire_url(r):
    ville   = VILLES.get(r.get("ville", "nancy"), VILLES["nancy"])
    rayon_m = int(r.get("rayon", 100)) * 1000
    location = f"{ville['nom']}__{ville['lat']}_{ville['lng']}_0_{rayon_m}"

    params = [
        "category=2",
        f"text={requests.utils.quote(r.get('keywords', ''))}",
        f"locations={location}",
        "sort=time",
        "order=desc",
    ]
    if r.get("prix_max"):
        params.append(f"price=max-{int(r['prix_max'])}")
    if r.get("km_max"):
        params.append(f"mileage=max-{int(r['km_max'])}")
    if r.get("fuel") and r["fuel"] in FUEL_CODES:
        params.append(f"fuel={FUEL_CODES[r['fuel']]}")

    return "https://www.leboncoin.fr/recherche?" + "&".join(params)


# ═══════════════════════════════════════════════════════════════════════════════
# LEBONCOIN — SCRAPING
# ═══════════════════════════════════════════════════════════════════════════════

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

    if r.get("prix_max") and prix is not None:
        if float(prix) > r["prix_max"]:
            return False
    km = attrs.get("mileage")
    if r.get("km_max") and km is not None:
        if int(km) > r["km_max"]:
            return False
    fuel = attrs.get("fuel")
    if r.get("fuel") and r["fuel"] and fuel is not None:
        if FUEL_LABELS.get(str(fuel), "") != r["fuel"]:
            return False
    return True


async def scraper_page(page, url, nom):
    try:
        await page.goto(url, wait_until="networkidle", timeout=35000)
        await page.wait_for_timeout(2500)
    except Exception as e:
        print(f"\n  [WARN] Navigation ({nom}) : {e}")

    next_data = await page.evaluate("""
        () => {
            const el = document.getElementById('__NEXT_DATA__');
            if (!el) return null;
            try { return JSON.parse(el.textContent); } catch { return null; }
        }
    """)

    if not next_data:
        # Sauvegarder pour diagnostic
        html = await page.content()
        with open("debug_lbc.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [WARN] Pas de données pour {nom} — voir debug_lbc.html")
        return []

    try:
        props = next_data.get("props", {}).get("pageProps", {})
        ads   = (props.get("searchData") or props.get("hydrationData", {}).get("searchData") or {}).get("ads", [])
        return ads
    except Exception as e:
        print(f"  [WARN] Parsing ({nom}) : {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE D'OPPORTUNITÉ
# ═══════════════════════════════════════════════════════════════════════════════

def calculer_score(prix, tous_prix):
    if not tous_prix or len(tous_prix) < 2 or prix is None:
        return None
    median = sorted(tous_prix)[len(tous_prix) // 2]
    if median == 0:
        return None
    return max(-99, min(99, round((median - float(prix)) / median * 100)))


def score_label(score):
    if score is None:         return ""
    if score >= 20:           return f"🟢 Excellente affaire ({score:+d}% vs marché)"
    if score >= 10:           return f"🔵 Bonne affaire ({score:+d}% vs marché)"
    if score >= 0:            return f"🟠 Prix correct ({score:+d}% vs marché)"
    return                           f"🔴 Surévalué ({score:+d}% vs marché)"


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def envoyer_telegram(message, chat_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for _ in range(3):
        try:
            requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            }, timeout=10)
            return
        except Exception:
            time.sleep(2)


def formater_message(ad, nom, score):
    attrs  = _get_attrs(ad)
    ad_id  = str(ad.get("list_id", ""))
    titre  = ad.get("subject", "Sans titre")
    prix   = ad.get("price", [None])[0] if ad.get("price") else "N/C"
    lieu   = ad.get("location", {}).get("city", "")
    lien   = f"https://www.leboncoin.fr/voitures/{ad_id}.htm"
    km_raw = attrs.get("mileage")
    an_raw = attrs.get("regdate", "")
    km     = f"{int(km_raw):,} km".replace(",", " ") if km_raw else "N/C"
    annee  = an_raw[:4] if an_raw else "N/C"
    sl     = f"\n{score_label(score)}" if score is not None else ""

    return (
        f"*NOUVELLE ANNONCE — {nom}*\n\n"
        f"*{titre}*\n"
        f"Année : {annee} | Ville : {lieu}\n"
        f"Kilométrage : {km}\n"
        f"Prix : *{prix} €*{sl}\n\n"
        f"[Voir l'annonce]({lien})"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION DONNÉES
# ═══════════════════════════════════════════════════════════════════════════════

def extraire_annonce(ad, nom, score):
    attrs  = _get_attrs(ad)
    prix   = ad.get("price", [None])[0] if ad.get("price") else None
    km_raw = attrs.get("mileage")
    an_raw = attrs.get("regdate", "")
    return {
        "id":        str(ad.get("list_id", "")),
        "titre":     ad.get("subject", ""),
        "prix":      float(prix) if prix else None,
        "km":        int(km_raw) if km_raw else None,
        "annee":     an_raw[:4] if an_raw else "",
        "ville":     ad.get("location", {}).get("city", ""),
        "lien":      f"https://www.leboncoin.fr/voitures/{ad.get('list_id', '')}.htm",
        "recherche": nom,
        "score":     score,
        "date":      datetime.now().isoformat(timespec="minutes"),
        "statut":    "nouvelle",
    }


def extraire_marche(ad, nom):
    attrs  = _get_attrs(ad)
    prix   = ad.get("price", [None])[0] if ad.get("price") else None
    km_raw = attrs.get("mileage")
    an_raw = attrs.get("regdate", "")
    return {
        "id":        str(ad.get("list_id", "")),
        "titre":     ad.get("subject", ""),
        "prix":      float(prix) if prix else None,
        "km":        int(km_raw) if km_raw else None,
        "annee":     int(an_raw[:4]) if an_raw and len(an_raw) >= 4 else None,
        "carburant": FUEL_LABELS.get(str(attrs.get("fuel", "")), ""),
        "ville":     ad.get("location", {}).get("city", ""),
        "cp":        ad.get("location", {}).get("zipcode", ""),
        "recherche": nom,
        "date":      datetime.now().strftime("%Y-%m-%d"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

async def scan():
    heure = datetime.now().hour
    if not (HEURE_DEBUT <= heure < HEURE_FIN):
        print(f"[{datetime.now().strftime('%H:%M')}] Mode nuit — expert en pause.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M')}] Scan en cours...")

    recherches = charger_config()
    if not recherches:
        print("  Aucune recherche active.")
        return

    historique         = charger_historique()
    annonces_existantes = charger_annonces()
    marche             = charger_marche()
    ids_annonces       = {a["id"] for a in annonces_existantes}
    ids_marche         = {a["id"] for a in marche}
    nouvelles_annonces = []
    nouvelles_marche   = []
    total_new          = 0

    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()

        # Visite la homepage en premier pour établir les cookies DataDome
        print("  Établissement session LeBonCoin...", end=" ", flush=True)
        try:
            await page.goto("https://www.leboncoin.fr", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
            print("OK")
        except Exception as e:
            print(f"WARN ({e})")

        for r in recherches:
            nom = r.get("nom", "Recherche")
            url = construire_url(r)
            print(f"  Scan : {nom}...", end=" ", flush=True)

            annonces  = await scraper_page(page, url, nom)
            valides   = [ad for ad in annonces if annonce_valide(ad, r)]
            tous_prix = [float(ad["price"][0]) for ad in valides
                         if ad.get("price") and ad["price"][0]]

            # Données marché (toutes les annonces valides)
            for ad in valides:
                ad_id = str(ad.get("list_id", ""))
                if ad_id and ad_id not in ids_marche:
                    nouvelles_marche.append(extraire_marche(ad, nom))
                    ids_marche.add(ad_id)

            # Nouvelles annonces uniquement
            nouvelles = 0
            for ad in reversed(valides):
                ad_id = str(ad.get("list_id", ""))
                if not ad_id or ad_id in historique:
                    continue

                prix  = ad.get("price", [None])[0] if ad.get("price") else None
                score = calculer_score(prix, tous_prix)

                dest = DESTINATAIRES.get(r.get("destinataire", "steven"), DESTINATAIRES["steven"])
                envoyer_telegram(formater_message(ad, nom, score), dest)
                historique.add(ad_id)

                if ad_id not in ids_annonces:
                    nouvelles_annonces.append(extraire_annonce(ad, nom, score))
                    ids_annonces.add(ad_id)

                nouvelles += 1
                total_new += 1

            print(f"{len(annonces)} scannées, {nouvelles} nouvelle(s)")
            await page.wait_for_timeout(2000)

        await page.close()

    sauvegarder_historique(historique)

    if nouvelles_annonces:
        annonces_existantes.extend(nouvelles_annonces)
        sauvegarder_annonces(annonces_existantes)

    if nouvelles_marche:
        marche.extend(nouvelles_marche)
        sauvegarder_marche(marche)
        print(f"  [Marché] +{len(nouvelles_marche)} entrées ({len(marche)} total)")

    print(f"Terminé — {total_new} nouvelle(s) annonce(s).")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("Expert LeBonCoin démarré !")
    print(f"Intervalle : {INTERVALLE_MINUTES} min | Plage : {HEURE_DEBUT}h–{HEURE_FIN}h\n")

    asyncio.run(scan())
    while True:
        time.sleep(INTERVALLE_MINUTES * 60)
        asyncio.run(scan())


if __name__ == "__main__":
    main()
