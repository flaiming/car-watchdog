# -*- coding: utf-8 -*-
"""Konfigurace pro aktualizaci žebříčku ojetých aut.

Tady se mění filtry, váhy skóre a kritéria. Logika je v lib.py, běh v aktualizace.py.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# --- soubory ---
ZDE = Path(__file__).resolve().parent
DATA_FILE = ZDE / "zebricek.xlsx"

# Načte ~/auta/.env (pokud existuje) do prostředí. .env je v .gitignore,
# takže config.py jde bez obav commitnout – žádná tajemství tu nejsou.
load_dotenv(ZDE / ".env")


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "ano")


# --- e-mail (denní souhrn) ---
# Citlivé údaje patří do .env (viz .env.example). Tady jen nesensitivní defaulty.
# Pozn.: u Gmailu použij "App password", ne hlavní heslo k účtu.
EMAIL = {
    "enabled": _bool("AUTA_MAIL_ENABLED", False),
    "smtp_host": os.environ.get("AUTA_SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("AUTA_SMTP_PORT", "587")),
    "use_tls": _bool("AUTA_SMTP_TLS", True),      # STARTTLS (port 587)
    "use_ssl": _bool("AUTA_SMTP_SSL", False),     # přímé SSL (port 465)
    "user": os.environ.get("AUTA_SMTP_USER", ""),
    "password": os.environ.get("AUTA_SMTP_PASS", ""),
    "from_addr": os.environ.get("AUTA_MAIL_FROM", "you@example.com"),
    "to_addrs": [a.strip() for a in os.environ.get("AUTA_MAIL_TO", "you@example.com").split(",") if a.strip()],
    "top_n": int(os.environ.get("AUTA_MAIL_TOPN", "5")),
    # Posílat e-mail jen když je nějaká změna (vhodné pro častý běh, např. po hodině).
    "only_on_change": _bool("AUTA_MAIL_ONLY_ON_CHANGE", True),
}

# --- VIN registr (oficiální API Ministerstva dopravy) ---
# Dřív se scrapovala kontrola-vin.cz, ta ale nasadila Cloudflare (403 z Pi).
# Oficiální Registr silničních vozidel má veřejné REST API s free klíčem
# (registrace: https://dataovozidlech.cz/registraceapi, limit 27 dotazů/min).
# Dává počet vlastníků, platnost STK a technická data – NE historii tachometru
# (stáčení). Klíč patří do .env (AUTA_DOV_API_KEY), viz .env.example.
DOV_API_URL = "https://api.dataovozidlech.cz/api/vehicletechnicaldata/v2"
DOV_API_KEY = os.environ.get("AUTA_DOV_API_KEY", "")

# --- sauto filtry (stejné, jako sleduješ v prohlížeči) ---
# Klíč = jen popisek do logu, hodnota = URL filtru.
FILTRY = {
    "Kia Ceed SW (1.5 DPI + 1.6 GDI atmosféra)":
        "https://www.sauto.cz/inzerce/osobni?znacky-modely=39%3A1334%2C9377"
        "&cena-do=300000&vyrobeno-od=2017&vyrobeno-do=2026&objem-od=1490&palivo=benzin&typ=kombi",
    # objem-od=1490 místo stropu výkonu: chceme 1.5 DPI (1497/1498 ccm) i 1.6 GDI.
    # Menší turba (1.0 T-GDI 998, 1.4 T-GDI 1353, 1.5 T-GDI 1482) odfiltruje objem,
    # 1.6 T-GDi (~150 kW) vyřadí classify (MAX_VYKON_NA) – atmosféry mají ≤103 kW.
    "Hyundai i30 kombi (1.5 DPI + 1.6 atmosféra)":
        "https://www.sauto.cz/inzerce/osobni/hyundai/i30?cena-do=300000"
        "&vyrobeno-od=2016&vyrobeno-do=2026&km-do=200000&objem-od=1490&palivo=benzin&typ=kombi",
}

# --- kritéria zařazení nového auta ---
# Atmosféra (bez turba) = jednoduchý motor. Bereme jen tyto objemy:
#   1493/1497/1498 = 1.5 DPI/MPI Smartstream (G4FS, ~81 kW) – sauto hlásí objem
#     různě podle inzerátu, proto všechny tři varianty,
#   1591 = 1.6 GDI (G4FG) + 1.6 MPI/CVVT, 1598 = Smartstream 1.6 MPI/DPI.
# Nejmenší benzíny i30/Ceed (1.0 T-GDI = 998, 1.4 T-GDI = 1353) nechytáme; 1.5 T-GDI
# má 1482 ccm, takže se do množiny nevejde.
# Pozn.: 1598 i 1591 sdílí i turbo verze (Kia 1.6 T-GDI, 1.6 T-GDI GT) – proto
# navíc strop výkonu níže.
OBJEMY_NA = {1493, 1497, 1498, 1591, 1598}
MAX_VYKON_NA = 105            # kW – nad tím už je to turbo (atmosféry zde mají ≤103 kW)
# Palivo a cenu musí hlídat i classify, ne jen URL filtr: sauto do výsledků
# míchá "topované" (placené) inzeráty mimo zadaný filtr. 7.7.2026 tak prošel
# diesel Kia 1.6 CRDi za 320 000 Kč – má 1598 ccm (stejně jako benzínové
# atmosféry) a 100 kW, takže kontrolou objemu i výkonu proklouzl.
PALIVA_OK = ("benz", "hybrid")  # podřetězce názvu paliva ze sauto (Benzín, Hybridní…)
MAX_CENA = 300000             # Kč – stejný strop jako cena-do ve filtrech
KLIMA_POVINNA = True          # auto bez klimy se nepřidává
VYRADIT_LPG = True            # LPG = systém navíc, nepřidáváme automaticky (jen nahlásíme)
MIN_ROK = 2016                # starší auta nezařazujeme (i kdyby je filtr vrátil)
MAX_NAJEZD = 200000           # auta s vyšším nájezdem nezařazujeme

# --- retrofit (Kč) – co se dá levně domontovat, nepenalizujeme cenou auta ---
RETROFIT = {"senzory": 3000, "tempomat": 3000}

# --- váhy skóre (součet = 1.0) ---
VAHY = {
    "najezd": 0.28,            # nižší = lepší
    "efektivni_cena": 0.24,    # nižší = lepší (cena + retrofit)
    "rok": 0.19,               # vyšší = lepší
    "majitele": 0.14,          # méně = lepší
    "stk": 0.10,               # delší platnost = lepší
    "klima": 0.05,             # automatická dostává bonus
}
KLIMA_SKORE = {"auto": 100, "manual": 40, "bez": 0}   # bez klimy = žádný bonus

# --- mapování motorů (jen pro popis v tabulce) ---
def motor_kod(znacka, objem, nazev=""):
    z = (znacka or "").lower()
    n = (nazev or "").lower()
    try:
        obj = int(objem)
    except (TypeError, ValueError):
        obj = 0
    patnactka = 1450 <= obj <= 1520      # 1.5 (Smartstream) vs 1.6
    if "kia" in z or "hyundai" in z:
        if patnactka:
            # 1.5 Smartstream: atmosféra je jen DPI/MPI (G4FS, ~81 kW)
            return "1.5 MPI (Smartstream)" if "mpi" in n else "1.5 DPI (Smartstream)"
        # variantu poznáme jen z názvu inzerátu (objem 1591/1598 sdílí víc motorů)
        if "dpi" in n:
            return "1.6 DPI (Smartstream)"
        if "mpi" in n or "cvvt" in n:
            return "1.6 MPI/CVVT (G4FC/G4FG)"
        return "1.6 GDI (G4FG)"
    return f"{objem} ccm (atmosféra)"

# --- pořadí sloupců v exportu ---
SLOUPCE = ["poradi", "stav", "pridano_dne", "prodano_dne", "skore", "vuz", "znacka", "prodejce", "cena_Kc", "retrofit_Kc",
           "efektivni_cena_Kc", "retrofit_co", "najezd_km", "vykon_kW", "rok",
           "zmen_vlastnika", "STK_do", "tempomat", "park_senzory", "klima",
           "klima_skore", "motor_kod", "turbo", "rozvod", "udrzba",
           "prvni_registrace", "pojistovna", "odometr_historie", "verdikt",
           "vin", "url", "vybava_vse"]
