import asyncio
import base64
import json
import os
import time
from datetime import datetime

import requests
from camoufox.async_api import AsyncCamoufox

# CONFIG TELEGRAM
TELEGRAM_TOKEN = "8631165512:AAFtYjnanMCsF_SCwXd_8VEeNX31xM9x5UY"
TELEGRAM_CHAT_ID = "1460644236"

INTERVALLE_MINUTES = 1
HEURE_DEBUT = 0
HEURE_FIN = 24

FICHIER_HISTORIQUE = "annonces_vues.json"

# CONFIG GITHUB
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO  = "sceptix-nrv/APEX-AUTO"
GH_FILE  = "apexauto.json"
GH_FILE_ANNONCES = "annonces.json"

VILLES = {
    "nancy":  {"lat": 48.695216237319016, "lng": 6.166461899450906,  "zip": 5021, "nom": "Nancy"},
    "orthez": {"lat": 43.489400,          "lng": -0.772800,           "zip": 0,    "nom": "Orthez"},
}

DESTINATAIRES = {
    "steven": "1460644236",
    "brieuc": "8833772133",
}

FUEL_CODES  = {"diesel": "2", "essence": "1", "electrique": "3", "hybride": "5"}
FUEL_LABELS = {"1": "essence", "2": "diesel", "3": "electrique", "5": "hybride"}

annonces_sha = None


def gh_headers():
    return {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def gh_get(file):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{file}"
    return requests.get(url, headers=gh_headers(), timeout=10)


def gh_put(file, content_str, sha, message):
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{file}"
    content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    return requests.put(url, json=body, headers=gh_headers(), timeout=15)


def charger_config_github():
    try:
        res = gh_get(GH_FILE)
        if res.status_code != 200:
            print(f"  [GitHub] Erreur {res.status_code} lors du chargement de la config")
            return []
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        config = json.loads(content)
        recherches = config.get("recherches", [])
        actives = [r for r in recherches if r.get("active", True)]
        print(f"  [GitHub] {len(actives)} recherche(s) active(s) chargee(s)")
        return actives
    except Exception as e:
        print(f"  [GitHub] Erreur : {e}")
        return []


def charger_annonces_github():
    global annonces_sha
    try:
        res = gh_get(GH_FILE_ANNONCES)
        if res.status_code == 404:
            annonces_sha = None
            return []
        if res.status_code != 200:
            return []
        data = res.json()
        annonces_sha = data["sha"]
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content)
    except Exception as e:
        print(f"  [GitHub] Erreur chargement annonces : {e}")
        return []


def sauvegarder_annonces_github(annonces):
    global annonces_sha
    if len(annonces) > 500:
        annonces = annonces[-500:]
    try:
        content_str = json.dumps(annonces, indent=2, ensure_ascii=False)
        res = gh_put(
            GH_FILE_ANNONCES,
            content_str,
            annonces_sha,
            f"[Expert] Scan {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        if res.ok:
            annonces_sha = res.json()["content"]["sha"]
            print(f"  [GitHub] Annonces sauvegardees ({len(annonces)} total)")
        else:
            print(f"  [GitHub] Erreur sauvegarde annonces : {res.status_code}")
    except Exception as e:
        print(f"  [GitHub] Erreur sauvegarde annonces : {e}")


def calculer_score(prix, tous_prix):
    if not tous_prix or len(tous_prix) < 2 or prix is None:
        return None
    median = sorted(tous_prix)[len(tous_prix) // 2]
    if median == 0:
        return None
    score = round((median - float(prix)) / median * 100)
    return max(-99, min(99, score))


def score_label(score):
    if score is None:
        return ""
    if score >= 20:
        return f"🟢 Excellente affaire ({score:+d}% vs marché)"
    if score >= 10:
        return f"🔵 Bonne affaire ({score:+d}% vs marché)"
    if score >= 0:
        return f"🟠 Prix correct ({score:+d}% vs marché)"
    return f"🔴 Surévalué ({score:+d}% vs marché)"


def construire_url(r):
    rayon_m = int(r.get("rayon", 100)) * 1000
    ville = VILLES.get(r.get("ville", "nancy"), VILLES["nancy"])
    location = f"{ville['nom']}__{ville['lat']}_{ville['lng']}_{ville['zip']}_{rayon_m}"

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


def envoyer_telegram(message, chat_id=None):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    dest = chat_id or TELEGRAM_CHAT_ID
    for _ in range(3):
        try:
            requests.post(api_url, json={
                "chat_id": dest,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            }, timeout=10)
            return
        except Exception:
            time.sleep(2)
    print("  Erreur Telegram : message non envoye apres 3 tentatives")


def charger_historique():
    if os.path.exists(FICHIER_HISTORIQUE):
        with open(FICHIER_HISTORIQUE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except Exception:
                return set()
    return set()


def sauvegarder_historique(historique):
    liste = list(historique)
    if len(liste) > 2000:
        liste = liste[-2000:]
    with open(FICHIER_HISTORIQUE, "w", encoding="utf-8") as f:
        json.dump(liste, f, indent=2)


def annonce_valide(ad, r):
    attrs = {a["key"]: a["values"][0] for a in ad.get("attributes", []) if a.get("values")}

    prix = ad.get("price", [None])[0]
    if r.get("prix_max") and prix is not None:
        if float(prix) > r["prix_max"]:
            return False

    km_raw = attrs.get("mileage")
    if r.get("km_max") and km_raw is not None:
        if int(km_raw) > r["km_max"]:
            return False

    fuel_raw = attrs.get("fuel")
    if r.get("fuel") and r["fuel"] and fuel_raw is not None:
        if FUEL_LABELS.get(str(fuel_raw), "") != r["fuel"]:
            return False

    return True


def formater_annonce(ad, nom_recherche, score=None):
    ad_id = str(ad.get("list_id", ""))
    titre = ad.get("subject", "Sans titre")
    prix  = ad.get("price", [None])[0] if ad.get("price") else "N/C"
    lieu  = ad.get("location", {}).get("city", "")
    lien  = f"https://www.leboncoin.fr/voitures/{ad_id}.htm"

    attrs  = {a["key"]: a["values"][0] for a in ad.get("attributes", []) if a.get("values")}
    km_raw = attrs.get("mileage")
    an_raw = attrs.get("regdate", "")
    km     = f"{int(km_raw):,} km".replace(",", " ") if km_raw else "N/C"
    annee  = an_raw[:4] if an_raw else "N/C"

    score_line = f"\n{score_label(score)}" if score is not None else ""

    msg = (
        f"*NOUVELLE ANNONCE — {nom_recherche}*\n\n"
        f"*{titre}*\n"
        f"Année : {annee}\n"
        f"Ville : {lieu}\n"
        f"Kilométrage : {km}\n"
        f"Prix : *{prix} €*"
        f"{score_line}\n\n"
        f"[Voir l'annonce]({lien})"
    )
    return ad_id, msg


def extraire_annonce_data(ad, nom_recherche, score=None):
    ad_id  = str(ad.get("list_id", ""))
    attrs  = {a["key"]: a["values"][0] for a in ad.get("attributes", []) if a.get("values")}
    prix   = ad.get("price", [None])[0] if ad.get("price") else None
    km_raw = attrs.get("mileage")
    an_raw = attrs.get("regdate", "")
    return {
        "id":       ad_id,
        "titre":    ad.get("subject", ""),
        "prix":     float(prix) if prix else None,
        "km":       int(km_raw) if km_raw else None,
        "annee":    an_raw[:4] if an_raw else "",
        "ville":    ad.get("location", {}).get("city", ""),
        "lien":     f"https://www.leboncoin.fr/voitures/{ad_id}.htm",
        "recherche": nom_recherche,
        "score":    score,
        "date":     datetime.now().isoformat(timespec="minutes"),
        "statut":   "nouvelle",
    }


async def scraper_recherche(page, url, nom):
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"  Erreur navigation ({nom}) : {e}")
        return []

    next_data = await page.evaluate("""
        () => {
            const el = document.getElementById('__NEXT_DATA__');
            if (!el) return null;
            try { return JSON.parse(el.textContent); }
            catch { return null; }
        }
    """)

    if not next_data:
        print(f"  [WARN] __NEXT_DATA__ introuvable pour {nom}")
        return []

    try:
        props = next_data.get("props", {}).get("pageProps", {})
        ads = props.get("searchData", {}).get("ads", [])
        if not ads:
            ads = props.get("hydrationData", {}).get("searchData", {}).get("ads", [])
        if not ads:
            ads = props.get("initialProps", {}).get("searchData", {}).get("ads", [])
        return ads
    except Exception as e:
        print(f"  Erreur parsing ({nom}) : {e}")
        return []


async def scan():
    heure = datetime.now().hour
    if heure >= HEURE_FIN or heure < HEURE_DEBUT:
        print(f"[{datetime.now().strftime('%H:%M')}] Mode nuit — expert en pause.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M')}] Scan en cours...")

    recherches = charger_config_github()
    if not recherches:
        print("  Aucune recherche active. Vérifie la config dans l'onglet Expert.")
        return

    historique = charger_historique()
    annonces_sauvegardees = charger_annonces_github()
    ids_existants = {a["id"] for a in annonces_sauvegardees}
    nouvelles_annonces = []
    total_nouvelles = 0

    try:
        async with AsyncCamoufox(headless=True) as browser:
            for r in recherches:
                nom = r.get("nom", "Recherche")
                url = construire_url(r)
                print(f"  Scan : {nom}...", end=" ", flush=True)
                try:
                    page = await browser.new_page()
                    annonces = await scraper_recherche(page, url, nom)
                    await page.close()

                    valides = [ad for ad in annonces if annonce_valide(ad, r)]
                    tous_prix = [float(ad["price"][0]) for ad in valides if ad.get("price") and ad["price"][0]]

                    nouvelles = 0
                    for ad in reversed(valides):
                        ad_id = str(ad.get("list_id", ""))
                        if not ad_id or ad_id in historique:
                            continue

                        prix  = ad.get("price", [None])[0]
                        score = calculer_score(prix, tous_prix) if prix else None

                        _, msg = formater_annonce(ad, nom, score)
                        dest = DESTINATAIRES.get(r.get("destinataire", "steven"), DESTINATAIRES["steven"])
                        envoyer_telegram(msg, dest)
                        historique.add(ad_id)

                        if ad_id not in ids_existants:
                            nouvelles_annonces.append(extraire_annonce_data(ad, nom, score))
                            ids_existants.add(ad_id)

                        nouvelles += 1
                        total_nouvelles += 1

                    print(f"{len(annonces)} scannées, {nouvelles} nouvelle(s)")

                except Exception as e:
                    print(f"Erreur : {e}")

                await asyncio.sleep(2)

    except Exception as e:
        print(f"  Erreur navigateur : {e}")

    sauvegarder_historique(historique)

    if nouvelles_annonces:
        annonces_sauvegardees.extend(nouvelles_annonces)
        sauvegarder_annonces_github(annonces_sauvegardees)

    print(f"Terminé — {total_nouvelles} nouvelle(s) annonce(s).")


def main():
    print("Expert LeBonCoin démarré !")
    print(f"Intervalle : toutes les {INTERVALLE_MINUTES} min | Plage : {HEURE_DEBUT}h–{HEURE_FIN}h")
    print("Config chargée depuis GitHub à chaque scan.\n")

    asyncio.run(scan())

    while True:
        time.sleep(INTERVALLE_MINUTES * 60)
        asyncio.run(scan())


if __name__ == "__main__":
    main()
