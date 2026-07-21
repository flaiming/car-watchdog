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
          rok="2019-01-01", km=80000, palivo="Benzín", cena=250000):
    return {"name": name, "engine_volume": vol,
            "aircondition_cb": {"name": ac} if ac else None,
            "fuel_cb": {"name": palivo} if palivo else None,
            "price": cena,
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


def test_classify_kia_gdi_atmosfera_projde():
    # Kia 1.6 GDI 99 kW (objem 1591) je atmosféra – musí projít
    it = _item(1591, "Manuální", "Kia Cee´d 1.6 GDI")
    it["engine_power"] = 99
    ok, _ = lib.classify(it)
    assert ok is True


def test_classify_hyundai_gdi_projde():
    # Hyundai i30 1.6 GDI 99 kW (objem 1591) je atmosféra – chceme i GDI
    it = _item(1591, "Manuální", "Hyundai i30, 1.6 GDI kombi")
    it["engine_power"] = 99
    ok, _ = lib.classify(it)
    assert ok is True


def test_classify_hyundai_mpi_projde():
    # Hyundai i30 1.6 MPI 88 kW (objem 1591) je atmosféra – musí projít
    it = _item(1591, "Manuální", "Hyundai i30, 1.6 MPI, ČR")
    it["engine_power"] = 88
    ok, _ = lib.classify(it)
    assert ok is True


def test_classify_i30_15_dpi_bereme():
    # 1.5 DPI Smartstream (1498 ccm, 81 kW) je atmosféra -> bereme
    it = _item(1498, "Automatická", "Hyundai i30 kombi 1.5 DPI")
    it["engine_power"] = 81
    ok, _ = lib.classify(it)
    assert ok is True


def test_classify_i30_maly_objem_vyradit():
    # nejmenší benzíny i30 (1.0 T-GDI = 998, 1.4 T-GDI = 1353) i 1.5 T-GDI (1482) -> ven
    for vol in (998, 1353, 1482):
        ok, duvod = lib.classify(_item(vol, "Manuální", "Hyundai i30 kombi"))
        assert ok is False and "atmosféra" in duvod


def test_classify_turbo_dle_vykonu_vyradit():
    # stejný objem jako atmosféra (1598), ale 110 kW = turbo (Kia/Citroën) -> ven
    it = _item(1598, "Manuální", "Kia 1.6 T-GDi")
    it["engine_power"] = 110
    ok, duvod = lib.classify(it)
    assert ok is False and "kW" in duvod


def test_classify_bez_klimy_vyradit():
    ok, duvod = lib.classify(_item(1598, None))
    assert ok is False and "klimat" in duvod.lower()
    ok2, _ = lib.classify(_item(1598, "Bez klimatizace"))
    assert ok2 is False


def test_classify_lpg_vyradit():
    ok, duvod = lib.classify(_item(1598, "Manuální", "Dacia Dokker 1.6 LPG"))
    assert ok is False and "lpg" in duvod.lower()


def test_classify_nafta_vyradit():
    # Regrese 7.7.2026: sauto do výsledků filtru přimíchalo topovaný inzerát
    # mimo filtr – Kia 1.6 CRDi (nafta) má 1598 ccm a 100 kW, takže prošla
    # kontrolou objemu i výkonu. Palivo se musí hlídat i v classify.
    it = _item(1598, "Automatická", "Kia Cee´d, 1.6 CRDi, Záruka",
               palivo="Nafta", cena=320000)
    it["engine_power"] = 100
    ok, duvod = lib.classify(it)
    assert ok is False and "palivo" in duvod.lower()


def test_classify_cena_nad_strop_vyradit():
    # topovaný inzerát může podlézt i cenový filtr v URL
    ok, duvod = lib.classify(_item(cena=320000))
    assert ok is False and "cena" in duvod


def test_classify_cena_na_hranici_projde():
    ok, _ = lib.classify(_item(cena=C.MAX_CENA))
    assert ok is True


def test_classify_hybrid_projde():
    ok, _ = lib.classify(_item(palivo="Hybridní"))
    assert ok is True


def test_classify_nezname_palivo_a_cena_projde():
    # chybějící údaj auto nevyřadí – hlavní síto zůstává objem motoru
    ok, _ = lib.classify(_item(palivo=None, cena=None))
    assert ok is True


# ---------- motor_kod ----------
@pytest.mark.parametrize("znacka,nazev,expected", [
    ("Hyundai", "Hyundai i30, 1.6 MPI, ČR", "1.6 MPI/CVVT (G4FC/G4FG)"),
    ("Hyundai", "Hyundai i30 1.6 CVVT kombi", "1.6 MPI/CVVT (G4FC/G4FG)"),
    ("Kia", "Kia Ceed SW 1.6 DPI", "1.6 DPI (Smartstream)"),
    ("Kia", "Kia Cee´d 1.6 GDI", "1.6 GDI (G4FG)"),
    ("Kia", "Kia Cee´d", "1.6 GDI (G4FG)"),          # bez varianty v názvu
])
def test_motor_kod_kia_hyundai_varianty(znacka, nazev, expected):
    assert C.motor_kod(znacka, 1591, nazev) == expected


@pytest.mark.parametrize("objem,nazev,expected", [
    (1498, "Hyundai i30 kombi 1.5 DPI", "1.5 DPI (Smartstream)"),
    (1497, "Hyundai i30 kombi", "1.5 DPI (Smartstream)"),   # bez varianty v názvu
    (1498, "Kia Ceed SW 1.5 MPI", "1.5 MPI (Smartstream)"),
])
def test_motor_kod_15_smartstream(objem, nazev, expected):
    assert C.motor_kod("Hyundai" if "Hyundai" in nazev else "Kia", objem, nazev) == expected


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


# ---------- dataovozidlech API (oficiální registr MD) ----------
def _dov_data(**kw):
    base = {"PocetVlastniku": 2,
            "PravidelnaTechnickaProhlidkaDo": "2026-12-09T00:00:00",
            "DatumPrvniRegistraceVCr": "2016-10-19T00:00:00",
            "DatumPrvniRegistrace": "2016-10-19T00:00:00",
            "StatusNazev": "PROVOZOVANÉ"}
    base.update(kw)
    return base


def test_report_z_dov_mapovani():
    rep = lib.report_z_dov(_dov_data())
    assert rep["found"] is True and rep["source"] == "api"
    assert rep["owners"] == 2
    assert rep["stk_do"] == "2026-12-09"
    assert rep["prvni_reg"] == "2016-10-19"
    # API nemá historii tachometru -> stáčení tudy neověříme
    assert rep["odo"] == [] and rep["tampered"] is False and rep["ok"] is False


def test_report_z_dov_chybejici_pole():
    rep = lib.report_z_dov({"StatusNazev": "PROVOZOVANÉ"})
    assert rep["found"] is True and rep["owners"] is None
    assert rep["stk_do"] is None and rep["prvni_reg"] is None


def test_dov_vehicle_bez_klice_je_error(monkeypatch):
    monkeypatch.setattr(C, "DOV_API_KEY", "")
    assert lib.dov_vehicle("X") == ("error", None)


def test_dov_vehicle_ok(monkeypatch):
    import io, json
    monkeypatch.setattr(C, "DOV_API_KEY", "KEY")
    payload = json.dumps({"Status": 1, "Data": _dov_data()}).encode()
    monkeypatch.setattr(lib.urllib.request, "urlopen",
                        lambda req, timeout=25: io.BytesIO(payload))
    stav, data = lib.dov_vehicle("VIN1")
    assert stav == "ok" and data["PocetVlastniku"] == 2


def test_dov_vehicle_neni_v_registru_je_gone(monkeypatch):
    # platný klíč, ale Data=null -> vozidlo v registru není (NE chyba)
    import io, json
    monkeypatch.setattr(C, "DOV_API_KEY", "KEY")
    payload = json.dumps({"Success": False, "Data": None}).encode()
    monkeypatch.setattr(lib.urllib.request, "urlopen",
                        lambda req, timeout=25: io.BytesIO(payload))
    assert lib.dov_vehicle("VIN1") == ("gone", None)


def test_dov_vehicle_401_je_error(monkeypatch):
    # neplatný/neaktivní klíč nesmí vypadat jako "auto není v registru"
    import urllib.error
    monkeypatch.setattr(C, "DOV_API_KEY", "KEY")
    def _401(req, timeout=25):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(lib.urllib.request, "urlopen", _401)
    assert lib.dov_vehicle("VIN1") == ("error", None)


def test_vin_report_gone_ma_source_api(monkeypatch):
    monkeypatch.setattr(lib, "dov_vehicle", lambda vin: ("gone", None))
    rep = lib.vin_report("VIN1")
    assert rep["found"] is False and rep["source"] == "api"


def test_vin_report_error_neoznaci_jako_nenalezeno(monkeypatch):
    monkeypatch.setattr(lib, "dov_vehicle", lambda vin: ("error", None))
    rep = lib.vin_report("VIN1")
    assert rep["found"] is False and rep["source"] == "error"


# ---------- _verdikt ----------
def test_verdikt_api_nalezeno():
    assert "registr MD" in lib._verdikt(lib.report_z_dov(_dov_data(PocetVlastniku=2)))


def test_verdikt_api_ctyri_majitele():
    v = lib._verdikt(lib.report_z_dov(_dov_data(PocetVlastniku=4)))
    assert "registr MD" in v and "majitelé" in v


def test_verdikt_api_neni_v_registru():
    assert "registru MD" in lib._verdikt(lib._prazdny_report(source="api"))


def test_verdikt_api_nedostupne():
    v = lib._verdikt(lib._prazdny_report(source="error"))
    assert "nedostupný" in v


def test_verdikt_rucni_staceni():
    assert "STÁČENÍ" in lib._verdikt({"tampered": True, "odo": [("1.1.2020", 100)]})


def test_verdikt_rucni_monotonni():
    rep = {"tampered": False, "ok": True, "odo": [("1.1.2020", 100)]}
    assert "bez stáčení" in lib._verdikt(rep)


def test_nove_auto_row_bere_stk_z_api():
    # STK a 1. registrace z registru MD mají přednost před sauto inzerátem
    item = {"id": 1, "name": "X", "manufacturer_cb": {"name": "Hyundai"},
            "engine_volume": 1591, "aircondition_cb": {"name": "Automatická"},
            "stk_date": "2025-01-01", "in_operation_date": "2017-01-01",
            "equipment_cb": []}
    vrep = lib.report_z_dov(_dov_data(PravidelnaTechnickaProhlidkaDo="2026-12-09T00:00:00"))
    row = lib.nove_auto_row(item, vrep)
    assert row["STK_do"] == "2026-12-09"
    assert row["prvni_registrace"] == "2016-10-19"
    assert row["zmen_vlastnika"] == 2
    assert "registr MD" in row["verdikt"]


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


@pytest.mark.parametrize("klima,tier", [
    ("Automatická", "auto"),
    ("Dvouzónová automatická", "auto"),
    ("Manuální", "manual"),
    ("Bez klimatizace", "bez"),
    ("?", "bez"),
    ("", "bez"),
    (None, "bez"),
])
def test_klima_tier(klima, tier):
    assert lib.klima_tier(klima) == tier


def test_nema_klimu():
    assert lib.nema_klimu("Bez klimatizace") is True
    assert lib.nema_klimu("Manuální") is False
    assert lib.nema_klimu("?") is False        # neznámá ≠ prokazatelně bez klimy


def test_prepocti_bez_klimy_nedostane_bonus():
    # auto bez klimatizace nesmí dostat manuální bonus (40), ale 0
    df = pd.DataFrame([
        _df_radek(vin="MAN", url="https://x/1", klima="Manuální"),
        _df_radek(vin="BEZ", url="https://x/2", klima="Bez klimatizace"),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    man = out[out["vin"] == "MAN"].iloc[0]
    bez = out[out["vin"] == "BEZ"].iloc[0]
    assert bez["klima_skore"] == C.KLIMA_SKORE["bez"] == 0
    assert man["klima_skore"] == C.KLIMA_SKORE["manual"]
    assert man["skore"] > bez["skore"]


def test_prepocti_vyrazene_auto_pod_aktivnimi():
    # VYŘAZENO (bez klimy) se řadí pod aktivní, i kdyby mělo lepší parametry
    df = pd.DataFrame([
        _df_radek(vin="OUT", url="https://x/1", stav="VYŘAZENO – bez klimy",
                  najezd_km=10000),
        _df_radek(vin="LIVE", url="https://x/2", stav="aktivní", najezd_km=200000),
    ])
    out = lib.prepocti(df, dnes=dt.date(2026, 6, 12))
    assert out.iloc[0]["stav"] == "aktivní"
    assert out.iloc[-1]["vin"] == "OUT"


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
        "id": 210492287, "name": "Kia Cee´d SW 1.6 GDI",
        "manufacturer_cb": {"name": "Kia"}, "manufacturer_seo": "kia",
        "model_seo": "cee-d", "price": 210000, "tachometer": 48451,
        "engine_power": 75, "engine_volume": 1598,
        "in_operation_date": "2019-05-15", "stk_date": "2027-06-05",
        "aircondition_cb": {"name": "Manuální"}, "vin": "UU1J9220062645827",
        "equipment_cb": [{"name": "Tempomat"}, {"name": "ABS"}],
    }
    vrep = {"owners": 3, "odo": [("15.5.2023", 37405)],
            "odo_str": "15.5.2023:37405", "tampered": False, "ok": True}
    row = lib.nove_auto_row(item, vrep)
    assert row["znacka"] == "Kia"
    assert row["tempomat"] == "✅"
    assert row["park_senzory"] == "—"          # senzory v equipmentu nejsou
    assert row["turbo"] == "NE"
    assert row["zmen_vlastnika"] == 3
    assert row["rok"] == "2019"
    assert "bez stáčení" in row["verdikt"]
    assert row["motor_kod"] == "1.6 GDI (G4FG)"


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


# ---------- sauto_check (rozlišení "pryč" vs "výpadek sítě") ----------
def test_sauto_check_vypadek_site_je_error(monkeypatch):
    # Regrese incidentu 22.6.2026: DNS výpadek nesmí vypadat jako "prodáno".
    def _spadni(url, timeout=25):
        raise OSError("Temporary failure in name resolution")
    monkeypatch.setattr(lib, "_get", _spadni)
    stav, item = lib.sauto_check(123)
    assert stav == "error" and item is None


def test_sauto_check_404_je_gone(monkeypatch):
    import urllib.error
    def _ctyrnula(url, timeout=25):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(lib, "_get", _ctyrnula)
    assert lib.sauto_check(123) == ("gone", None)


def test_sauto_check_aktivni(monkeypatch):
    import json
    monkeypatch.setattr(lib, "_get",
                        lambda url, timeout=25: json.dumps(
                            {"result": {"status": "active", "id": 1}}).encode())
    stav, item = lib.sauto_check(1)
    assert stav == "active" and item["id"] == 1


# ---------- prodejce_name ----------
def test_prodejce_name_z_premise():
    assert lib.prodejce_name({"premise": {"name": "AAA AUTO"}}) == "AAA AUTO"


def test_prodejce_name_orizne_poznamku():
    assert lib.prodejce_name(
        {"premise": {"name": "Louda Auto+   ( 8 poboček v 6 krajích )"}}) == "Louda Auto+"


def test_prodejce_name_soukromnik():
    assert lib.prodejce_name({"premise": None}) == "soukromý prodejce"


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
