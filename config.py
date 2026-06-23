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

# --- sauto filtry (stejné, jako sleduješ v prohlížeči) ---
# Klíč = jen popisek do logu, hodnota = URL filtru.
FILTRY = {
    "Dacia (Lodgy/Dokker)":
        "https://www.sauto.cz/inzerce/osobni?znacky-modely=15%3A6578%2C6385%2C1097"
        "&cena-do=300000&vyrobeno-od=2016&objem-od=1200&palivo=benzin%2Chybridni&typ=mpv%2Cpick-up",
    "Citroën Berlingo + Peugeot Partner":
        "https://www.sauto.cz/inzerce/osobni?znacky-modely=70%3A1241%7C13%3A1270"
        "&cena-do=300000&vyrobeno-od=2016&objem-od=1200&palivo=benzin%2Chybridni&typ=mpv%2Cpick-up",
}

# --- kritéria zařazení nového auta ---
# Atmosféra (bez turba) = jednoduchý motor. Bereme jen tyto objemy (1.6 SCe / 1.6 VTi).
OBJEMY_NA = {1597, 1598, 1600}
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
KLIMA_SKORE = {"auto": 100, "manual": 40}

# --- mapování motorů (jen pro popis v tabulce) ---
def motor_kod(znacka, objem):
    z = (znacka or "").lower()
    if "dacia" in z:
        return "1.6 SCe (H4M)"
    if "citro" in z or "peugeot" in z:
        return "1.6 VTi 120 (EP6C/5FS)"
    return f"{objem} ccm (atmosféra)"

# --- pořadí sloupců v exportu ---
SLOUPCE = ["poradi", "stav", "pridano_dne", "skore", "vuz", "znacka", "prodejce", "cena_Kc", "retrofit_Kc",
           "efektivni_cena_Kc", "retrofit_co", "najezd_km", "vykon_kW", "rok",
           "zmen_vlastnika", "STK_do", "tempomat", "park_senzory", "klima",
           "klima_skore", "motor_kod", "turbo", "rozvod", "udrzba",
           "prvni_registrace", "pojistovna", "odometr_historie", "verdikt",
           "vin", "url", "vybava_vse"]
