# -*- coding: utf-8 -*-
"""Offline testy logiky (žádná síť). Spusť: .venv/bin/pytest -q"""
import datetime as dt
import pandas as pd
import pytest

import lib
import mailer
import config as C


# ---------- parse_date ----------
@pytest.mark.parametrize("s,expected", [
    ("2027-06-05", dt.date(2027, 6, 5)),
    ("5.3.2028", dt.date(2028, 3, 5)),
    ("10.2026", dt.date(2026, 10, 1)),
    ("2018", dt.date(2018, 1, 1)),
])
def test_parse_date_formaty(s, expected):
    assert lib.parse_date(s) == expected


def test_parse_date_nesmysl():
    assert lib.parse_date("nan") is None
    assert lib.parse_date("") is None


# ---------- id_from_url ----------
def test_id_from_url():
    assert lib.id_from_url(
        "https://www.sauto.cz/osobni/detail/dacia/lodgy/210492287") == 210492287
    assert lib.id_from_url("nan") is None


# ---------- classify ----------
def _item(vol=1598, ac="Manuální", name="Dacia Lodgy 1.6 SCe",
          rok="2019-01-01", km=80000):
    return {"name": name, "engine_volume": vol,
            "aircondition_cb": {"name": ac} if ac else None,
            "in_operation_date": rok, "tachometer": km}


def test_classify_na_s_klimou_je_relevantni():
    ok, _ = lib.classify(_item(1598, "Manuální"))
    assert ok is True


def test_classify_stary_rok_vyradit():
    ok, duvod = lib.classify(_item(rok="2010-05-01"))
    assert ok is False and "rok" in duvod


def test_classify_vysoky_najezd_vyradit():
    ok, duvod = lib.classify(_item(km=255927))
    assert ok is False and "nájezd" in duvod


def test_classify_hranice_2016_projde():
    ok, _ = lib.classify(_item(rok="2016-03-01", km=199000))
    assert ok is True


def test_classify_turbo_vyradit():
    ok, duvod = lib.classify(_item(1332, "Manuální", "Dacia Lodgy 1.3 TCe"))
    assert ok is False and "atmosféra" in duvod


def test_classify_bez_klimy_vyradit():
    ok, duvod = lib.classify(_item(1598, None))
    assert ok is False and "klimat" in duvod.lower()
    ok2, _ = lib.classify(_item(1598, "Bez klimatizace"))
    assert ok2 is False


def test_classify_lpg_vyradit():
    ok, duvod = lib.classify(_item(1598, "Manuální", "Dacia Dokker 1.6 LPG"))
    assert ok is False and "lpg" in duvod.lower()


# ---------- parse_vin ----------
def test_parse_vin_cisty_monotonni():
    t = ("blabla Počet vlastníků: 2 ... Průběh odometru "
         "25.1.2021 70551 25.1.2021 70551 24.1.2023 92329 24.1.2023 92329 "
         "17.1.2025 124510 17.1.2025 124510 Věk a původ")
    r = lib.parse_vin(t)
    assert r["owners"] == 2
    assert r["tampered"] is False
    assert r["ok"] is True
    assert r["odo"][0] == ("25.1.2021", 70551)
    assert r["odo"][-1] == ("17.1.2025", 124510)


def test_parse_vin_staceni_detekce():
    # pokles o víc než 500 km mezi čteními = stáčení
    t = ("Počet vlastníků: 1 Průběh odometru "
         "1.1.2020 150000 1.1.2022 90000 konec")
    r = lib.parse_vin(t)
    assert r["tampered"] is True
    assert r["ok"] is False


def test_parse_vin_tolerance_500():
    # stejnodenní STK vs emise se liší o pár km -> není stáčení
    t = ("Počet vlastníků: 1 Průběh odometru "
         "1.1.2022 64086 1.1.2022 64085 1.1.2024 94943 konec")
    r = lib.parse_vin(t)
    assert r["tampered"] is False


def test_parse_vin_bez_zaznamu():
    r = lib.parse_vin("nic tu neni")
    assert r["odo"] == [] and r["ok"] is False


# ---------- scoring / prepocti ----------
def _df_radek(**kw):
    base = dict(stav="aktivní", vuz="Test", znacka="Dacia", cena_Kc=200000,
                najezd_km=100000, vykon_kW=75, rok=2018, zmen_vlastnika=1,
                STK_do="2028-01-01", tempomat="✅", park_senzory="✅",
                klima="Manuální", motor_kod="x", turbo="NE", rozvod="řetěz",
                udrzba="x", prvni_registrace="2018", pojistovna=float("nan"),
                odometr_historie="", verdikt="", vin="V1",
                url="https://x/1", vybava_vse="")
    base.update(kw)
    return base


def test_prepocti_retrofit_a_efektivni_cena():
    df = pd.DataFrame([
        _df_radek(vin="A", url="https://x/1", tempomat="—", park_senzory="—",
                  cena_Kc=100000),
        _df_radek(vin="B", url="https://x/2", tempomat="✅", park_senzory="✅",
                  cena_Kc=100000),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    a = out[out["vin"] == "A"].iloc[0]
    b = out[out["vin"] == "B"].iloc[0]
    # auto bez senzorů i tempomatu má retrofit za obojí
    assert a["retrofit_Kc"] == C.RETROFIT["senzory"] + C.RETROFIT["tempomat"]
    assert a["efektivni_cena_Kc"] == 100000 + a["retrofit_Kc"]
    assert b["retrofit_Kc"] == 0
    assert b["efektivni_cena_Kc"] == 100000


def test_prepocti_prodana_na_konci():
    df = pd.DataFrame([
        _df_radek(vin="SOLD", url="https://x/1", stav="PRODÁNO", najezd_km=10000),
        _df_radek(vin="LIVE", url="https://x/2", stav="aktivní", najezd_km=200000),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    # i když má prodané lepší parametry, musí být až za aktivními
    assert out.iloc[0]["stav"] == "aktivní"
    assert out.iloc[-1]["stav"] == "PRODÁNO"
    assert list(out["poradi"]) == [1, 2]


def test_prepocti_klima_skore_bonus():
    df = pd.DataFrame([
        _df_radek(vin="AUTO", url="https://x/1", klima="Automatická"),
        _df_radek(vin="MAN", url="https://x/2", klima="Manuální"),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    auto = out[out["vin"] == "AUTO"].iloc[0]
    man = out[out["vin"] == "MAN"].iloc[0]
    assert auto["klima_skore"] == C.KLIMA_SKORE["auto"]
    assert man["klima_skore"] == C.KLIMA_SKORE["manual"]


def test_prepocti_chybejici_majitele_nespadne():
    # nové auto bez počtu majitelů (VIN neměl údaj) -> "?" ve sloupci.
    # Scoring nesmí spadnout a auto má dostat nejhorší konec škály.
    df = pd.DataFrame([
        _df_radek(vin="ZNAMY", url="https://x/1", zmen_vlastnika=1),
        _df_radek(vin="NEZNAMY", url="https://x/2", zmen_vlastnika="?"),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 15))   # nesmí vyhodit výjimku
    assert out["skore"].notna().all()
    znamy = out[out["vin"] == "ZNAMY"].iloc[0]["skore"]
    neznamy = out[out["vin"] == "NEZNAMY"].iloc[0]["skore"]
    assert znamy >= neznamy   # neznámý počet majitelů auto nezvýhodní


def test_prepocti_nizsi_najezd_lepsi_skore():
    df = pd.DataFrame([
        _df_radek(vin="LOW", url="https://x/1", najezd_km=50000),
        _df_radek(vin="HIGH", url="https://x/2", najezd_km=250000),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    low = out[out["vin"] == "LOW"].iloc[0]["skore"]
    high = out[out["vin"] == "HIGH"].iloc[0]["skore"]
    assert low > high


# ---------- nove_auto_row ----------
def test_nove_auto_row_sestaveni():
    item = {
        "id": 210492287, "name": "Dacia Lodgy 1.6 SCe Arctic, 7 míst",
        "manufacturer_cb": {"name": "Dacia"}, "manufacturer_seo": "dacia",
        "model_seo": "lodgy", "price": 210000, "tachometer": 48451,
        "engine_power": 75, "engine_volume": 1598,
        "in_operation_date": "2019-05-15", "stk_date": "2027-06-05",
        "aircondition_cb": {"name": "Manuální"}, "vin": "UU1J9220062645827",
        "equipment_cb": [{"name": "Tempomat"}, {"name": "ABS"}],
    }
    vrep = {"owners": 3, "odo": [("15.5.2023", 37405)],
            "odo_str": "15.5.2023:37405", "tampered": False, "ok": True}
    row = lib.nove_auto_row(item, vrep)
    assert row["znacka"] == "Dacia"
    assert row["tempomat"] == "✅"
    assert row["park_senzory"] == "—"          # senzory v equipmentu nejsou
    assert row["turbo"] == "NE"
    assert row["zmen_vlastnika"] == 3
    assert row["rok"] == "2019"
    assert "bez stáčení" in row["verdikt"]
    assert row["motor_kod"] == "1.6 SCe (H4M)"


def test_nove_auto_row_pridano_dne():
    item = {"id": 1, "name": "X", "manufacturer_cb": {"name": "Dacia"},
            "engine_volume": 1598, "aircondition_cb": {"name": "Manuální"},
            "equipment_cb": []}
    vrep = {"owners": 1, "odo": [], "odo_str": "", "tampered": False, "ok": False}
    row = lib.nove_auto_row(item, vrep, dnes=dt.date(2026, 6, 17))
    assert row["pridano_dne"] == "2026-06-17"


def test_nove_auto_row_staceni_ve_verdiktu():
    item = {"id": 1, "name": "X", "manufacturer_cb": {"name": "Dacia"},
            "engine_volume": 1598, "aircondition_cb": {"name": "Manuální"},
            "equipment_cb": []}
    vrep = {"owners": 2, "odo": [], "odo_str": "", "tampered": True, "ok": False}
    row = lib.nove_auto_row(item, vrep)
    assert "STÁČENÍ" in row["verdikt"]


# ---------- mailer.build_summary ----------
def _df_pro_mail():
    df = pd.DataFrame([
        _df_radek(vin="A", url="https://x/1", vuz="Dacia Lodgy", najezd_km=50000),
        _df_radek(vin="B", url="https://x/2", vuz="Citroën Berlingo", najezd_km=150000),
        _df_radek(vin="C", url="https://x/3", vuz="Prodané", stav="PRODÁNO"),
    ])
    return lib.prepocti(df, dnes=dt.date(2026, 6, 12))


def test_build_summary_se_zmenami():
    df = _df_pro_mail()
    prodano_row = {"poradi": 3, "skore": 50.0, "vuz": "Staré auto",
                   "cena_Kc": 199000, "najezd_km": 88000, "rok": "2018",
                   "tempomat": "✅", "park_senzory": "—", "klima": "Manuální",
                   "url": "https://x/9"}
    changes = {"prodano": [prodano_row], "aktivni": [], "pridano": ["Nová Dacia"]}
    subj, text, html = mailer.build_summary(df, changes, "2026-06-12")
    assert "2026-06-12" in subj
    assert "Nová Dacia" in text and "Staré auto" in text
    assert "Nová Dacia" in html
    assert "<table" in html
    # u prodaného auta je vidět jeho místo v žebříčku
    assert "#3" in text and "#3" in html
    # u prodaného auta jsou kompletní info jako v žebříčku (cena, nájezd, odkaz)
    assert "199 000" in text and "199 000" in html
    assert "88 000" in text and "88 000" in html
    assert "https://x/9" in text and "href='https://x/9'" in html
    # aktivní auta jsou v žebříčku v mailu
    assert "Dacia Lodgy" in text
    # ke každému autu je prokliknutelný odkaz na inzerát
    assert "https://x/1" in text
    assert "href='https://x/1'" in html


def test_build_summary_prodano_bez_poradi():
    # snese i holý název (None místo pořadí/info) – nesmí spadnout
    df = _df_pro_mail()
    changes = {"prodano": ["Bezejmenné"], "aktivni": [], "pridano": []}
    subj, text, html = mailer.build_summary(df, changes, "2026-06-12")
    assert "Bezejmenné" in text and "#?" in text


def test_build_summary_beze_zmen():
    df = _df_pro_mail()
    changes = {"prodano": [], "aktivni": [], "pridano": []}
    subj, text, html = mailer.build_summary(df, changes, "2026-06-12")
    assert "beze změny" in subj
    assert "Žádné změny" in text


def test_send_vypnuty_neposila():
    # při enabled=False se nic neodešle a vrátí False
    old = C.EMAIL["enabled"]
    C.EMAIL["enabled"] = False
    try:
        assert mailer.send("s", "t", "<p>h</p>") is False
    finally:
        C.EMAIL["enabled"] = old
