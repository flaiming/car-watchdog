# -*- coding: utf-8 -*-
"""Sdílené funkce: sauto API, VIN registr (kontrola-vin.cz), scoring.

Žádný stav se tu nedrží – jen čisté funkce, které volá aktualizace.py.
"""
import re, json, html as ihtml, datetime as dt, urllib.request, urllib.error
import pandas as pd
import config as C

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "cs-CZ,cs;q=0.9"})
    return urllib.request.urlopen(req, timeout=timeout).read()


# ---------- sauto ----------
def sauto_item(item_id):
    """Detail inzerátu z sauto API. Vrací dict 'result' nebo None při chybě."""
    try:
        data = json.loads(_get(f"https://www.sauto.cz/api/v1/items/{item_id}"))
        return data.get("result")
    except Exception as e:
        print(f"  ! sauto_item({item_id}) chyba: {e}")
        return None


def sauto_status(item_id):
    """'active' / 'deleted' / None."""
    r = sauto_item(item_id)
    return r.get("status") if r else None


def sauto_check(item_id):
    """Robustní kontrola stavu inzerátu. Vrací (stav, item):

      ("active", item)  – inzerát je aktivní
      ("gone",   item)  – inzerát definitivně pryč (404 nebo status != active)
      ("error",  None)  – nepodařilo se zeptat (výpadek sítě, timeout, 5xx…)

    Rozdíl mezi "gone" a "error" je zásadní: na "error" se NESMÍ sahat na stav,
    jinak výpadek sítě označí celý žebříček jako prodaný (viz incident 22.6.2026).
    """
    try:
        raw = _get(f"https://www.sauto.cz/api/v1/items/{item_id}")
    except urllib.error.HTTPError as e:
        # 404 = inzerát neexistuje (smazaný/prodaný); 4xx/5xx jiné = nejisté
        return ("gone", None) if e.code == 404 else ("error", None)
    except Exception as e:
        print(f"  ! sauto_check({item_id}) síť: {e}")
        return ("error", None)
    try:
        item = json.loads(raw).get("result")
    except Exception as e:
        print(f"  ! sauto_check({item_id}) JSON: {e}")
        return ("error", None)
    if not item:
        return ("gone", None)
    return ("active" if item.get("status") == "active" else "gone", item)


def sauto_filter_ids(url):
    """Seznam ID inzerátů z výsledků filtru (parsuje /detail/.../<id> odkazy).

    Pozor: seo jména značky/modelu můžou mít pomlčku/číslici (např. Kia 'cee-d'),
    proto [a-z0-9-]+ a ne jen [a-z]+ – jinak by se model s pomlčkou nenašel.
    """
    html = _get(url).decode("utf-8", "replace")
    ids = re.findall(r'/detail/[a-z0-9-]+/[a-z0-9-]+/(\d+)', html)
    return sorted(set(int(i) for i in ids))


def _eq_names(item):
    eq = item.get("equipment_cb") or []
    return [(e.get("name") if isinstance(e, dict) else str(e)) for e in eq]


def _ac_name(item):
    ac = item.get("aircondition_cb")
    return ac.get("name") if isinstance(ac, dict) else ac


def klima_tier(klima):
    """Zařadí text klimatizace do tieru pro skóre: 'auto' / 'manual' / 'bez'.

    'bez' dostane i prázdná/neznámá hodnota ('?', None) – neověřená klima
    auto nezvýhodní (stejný princip jako u ostatních neznámých údajů)."""
    t = str(klima).strip().lower()
    if t in ("", "nan", "none", "?") or "bez klim" in t:
        return "bez"
    if "utomat" in t:
        return "auto"
    return "manual"


def nema_klimu(klima):
    """True, pokud text klimatizace znamená 'bez klimatizace' (ne neznámá)."""
    return "bez klim" in str(klima).strip().lower()


def prodejce_name(item):
    """Název prodejce (AAA AUTO, Auto ESA…) z premise. Bez premise = soukromý.

    Některé bazary mají v názvu odsazení/poznámky – zkrátíme na čistý název."""
    p = item.get("premise")
    if not isinstance(p, dict):
        return "soukromý prodejce"
    name = re.sub(r'\s+', ' ', str(p.get("name") or "")).strip()
    # odřízneme případnou poznámku v závorce ("Louda Auto+ ( 8 poboček )")
    name = re.sub(r'\s*\(.*$', '', name).strip()
    return name or "soukromý prodejce"


def classify(item):
    """Rozhodne, jestli auto patří do žebříčku (atmosféra 1.6 + klima).

    Vrací (relevant: bool, duvod: str).
    """
    name = (item.get("name") or "")
    vol = item.get("engine_volume")
    ac = _ac_name(item)
    has_ac = bool(ac) and "bez klimat" not in str(ac).lower()

    # palivo + cena: URL filtr je nehlídá spolehlivě – sauto do výsledků míchá
    # topované inzeráty mimo filtr (viz config.PALIVA_OK / MAX_CENA).
    # Neznámé palivo/cenu nevyřazujeme – hlavní síto je objem níže.
    palivo = item.get("fuel_cb")
    palivo = (palivo.get("name") if isinstance(palivo, dict) else palivo) or ""
    if palivo and not any(p in palivo.lower() for p in C.PALIVA_OK):
        return False, f"palivo {palivo} (bereme jen benzín/hybrid)"
    cena = item.get("price")
    if isinstance(cena, (int, float)) and cena > C.MAX_CENA:
        return False, f"cena {int(cena)} Kč > {C.MAX_CENA} (mimo profil)"

    if vol not in C.OBJEMY_NA:
        return False, f"není atmosféra 1.6 ({vol} ccm – turbo/diesel/jiné)"
    # Strop výkonu: některé objemy sdílí turbo verze (1598 THP/T-GDI, 1591 T-GDI GT).
    # Atmosféry mají ≤103 kW, turba ≥110 kW – vyšší výkon = turbo, nezařazujeme.
    kw = item.get("engine_power")
    if isinstance(kw, (int, float)) and kw > C.MAX_VYKON_NA:
        return False, f"výkon {int(kw)} kW > {C.MAX_VYKON_NA} (turbo, ne atmosféra)"
    if C.VYRADIT_LPG and "lpg" in name.lower():
        return False, "LPG (systém navíc)"
    if C.KLIMA_POVINNA and not has_ac:
        return False, "nemá klimatizaci"

    # rok (sauto filtr občas vrátí i auto mimo rozsah – mívá špatně zadaný rok)
    rok = None
    for k in ("in_operation_date", "manufacturing_date"):
        m = re.search(r'(\d{4})', str(item.get(k) or ""))
        if m:
            rok = int(m.group(1))
            break
    if rok and rok < C.MIN_ROK:
        return False, f"rok {rok} < {C.MIN_ROK} (mimo profil)"

    # nájezd
    km = item.get("tachometer")
    if isinstance(km, (int, float)) and km > C.MAX_NAJEZD:
        return False, f"nájezd {int(km)} km > {C.MAX_NAJEZD} (mimo profil)"

    return True, "OK – atmosféra 1.6 + klima"


# ---------- VIN registr (oficiální API Ministerstva dopravy) ----------
def _prazdny_report(odo_str="", source="none"):
    """Report bez dat – auto se přidá, ale bez ověření z registru."""
    return {"owners": None, "stk_do": None, "prvni_reg": None,
            "odo": [], "odo_str": odo_str, "tampered": False, "ok": False,
            "found": False, "source": source}


def dov_vehicle(vin):
    """Detail vozidla z oficiálního API dataovozidlech.cz. Vrací (stav, data):

      ("ok",     Data)   – vozidlo nalezeno (dict s technickými údaji)
      ("gone",   None)   – klíč OK, ale vozidlo v registru není
      ("error",  None)   – nepodařilo se zeptat (chybí klíč, limit, síť, 401…)

    Rozdíl "gone" vs "error" je zásadní stejně jako u sauto_check: na "error"
    se nesmí tvrdit, že auto v registru není."""
    if not C.DOV_API_KEY:
        print("  ! dataovozidlech: chybí AUTA_DOV_API_KEY (.env) – VIN se neověří")
        return ("error", None)
    req = urllib.request.Request(
        f"{C.DOV_API_URL}?vin={vin}",
        headers={"API_KEY": C.DOV_API_KEY, "Accept": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=25).read()
    except urllib.error.HTTPError as e:
        # 429/limit i 401 = nejisté, NE "auto není"; 404 by taky bylo nejisté
        print(f"  ! dataovozidlech({vin}) HTTP {e.code}")
        return ("error", None)
    except Exception as e:
        print(f"  ! dataovozidlech({vin}) síť: {e}")
        return ("error", None)
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"  ! dataovozidlech({vin}) JSON: {e}")
        return ("error", None)
    vozidlo = data.get("Data")
    if not vozidlo:
        return ("gone", None)          # klíč platný, ale VIN v registru není
    return ("ok", vozidlo)


def report_z_dov(vozidlo):
    """Sestaví VIN report z odpovědi API (čistá funkce, bez sítě).

    API dává počet vlastníků, platnost STK a 1. registraci – NE historii
    tachometru, takže stáčení tudy ověřit nejde (ok/tampered zůstávají False)."""
    rep = _prazdny_report(source="api")
    rep["found"] = True
    ov = vozidlo.get("PocetVlastniku")
    rep["owners"] = int(ov) if isinstance(ov, (int, float)) else None
    stk = vozidlo.get("PravidelnaTechnickaProhlidkaDo")
    rep["stk_do"] = str(stk)[:10] if stk else None
    reg = (vozidlo.get("DatumPrvniRegistraceVCr")
           or vozidlo.get("DatumPrvniRegistrace"))
    rep["prvni_reg"] = str(reg)[:10] if reg else None
    return rep


def vin_report(vin):
    """Ověří VIN přes oficiální registr (dataovozidlech.cz).

    Vrací report se stejnými klíči jako dřív (owners/odo/odo_str/tampered/ok)
    plus stk_do, prvni_reg, found, source – aby na něj navazující kód i testy
    nemusely měnit tvar. Historii tachometru API nemá, takže odo je vždy [].
    """
    stav, vozidlo = dov_vehicle(vin)
    if stav == "ok":
        return report_z_dov(vozidlo)
    if stav == "gone":
        return _prazdny_report(odo_str="není v registru MD", source="api")
    return _prazdny_report(odo_str="VIN nelze ověřit (API nedostupné)", source="error")


# ---------- historie tachometru (jen ruční doplnění z prohlížeče) ----------
# kontrola-vin.cz je za Cloudflare, z Pi ji automat nenačte (403). Tyhle dvě
# funkce zůstávají pro občasné ruční doověření stáčení přes prohlížeč (extension).
def _vin_text(vin):
    html = _get(f"https://www.kontrola-vin.cz/{vin}").decode("utf-8", "replace")
    html = re.sub(r'<script.*?</script>', '', html, flags=re.S)
    html = re.sub(r'<style.*?</style>', '', html, flags=re.S)
    return re.sub(r'\s+', ' ', ihtml.unescape(re.sub(r'<[^>]+>', ' ', html)))


def parse_vin(t):
    """Vyparsuje data z textu stránky kontrola-vin.cz (čistá funkce, bez sítě).

    Vrací dict: {owners, odo:[(datum,km)], odo_str, tampered, ok}.
    Tolerance poklesu 500 km (stejnodenní STK vs emise se může lišit o pár km).
    """
    out = {"owners": None, "odo": [], "odo_str": "", "tampered": False, "ok": False}
    m = re.search(r'Počet vlastníků:\s*(\d+)', t)
    if m:
        out["owners"] = int(m.group(1))

    j = t.lower().find('průběh odometru')
    seg = t[j + 15: j + 400] if j >= 0 else ""
    pairs = re.findall(r'(\d{1,2}\.\d{1,2}\.\d{4})\s+(\d{4,7})', seg)
    seen, last = [], None
    for d, km in pairs:
        if (d, km) != last:
            seen.append((d, int(km)))
        last = (d, km)
    out["odo"] = seen
    out["odo_str"] = "; ".join(f"{d}:{km}" for d, km in seen)
    kms = [k for _, k in seen]
    out["tampered"] = any(kms[i] - kms[i + 1] > 500 for i in range(len(kms) - 1))
    out["ok"] = bool(seen) and not out["tampered"]
    return out


# ---------- pomocné ----------
def parse_date(s):
    s = str(s).strip()
    for f in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.search(r'(\d{1,2})\.(\d{4})', s)
    if m:
        return dt.date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r'(\d{4})', s)
    if m:
        return dt.date(int(m.group(1)), 1, 1)
    return None


def id_from_url(u):
    m = re.search(r'(\d+)$', str(u))
    return int(m.group(1)) if m else None


# ---------- scoring ----------
def _retrofit_kc(row):
    c = 0
    if not str(row["park_senzory"]).startswith("✅"):
        c += C.RETROFIT["senzory"]
    if not str(row["tempomat"]).startswith("✅"):
        c += C.RETROFIT["tempomat"]
    return c


def _retrofit_co(row):
    p = []
    if not str(row["park_senzory"]).startswith("✅"):
        p.append("senzory")
    if not str(row["tempomat"]).startswith("✅"):
        p.append("tempomat")
    return " + ".join(p) if p else "—"


def _norm(col, ref, higher_better):
    """Min-max normalizace 0–100. Odolná vůči nečíselným / chybějícím hodnotám:
    '?', None apod. se převedou na NaN a doplní nejhorším koncem škály (aby
    neověřený údaj auto nezvýhodnil)."""
    col = pd.to_numeric(col, errors="coerce")
    ref = pd.to_numeric(ref, errors="coerce").dropna()
    if ref.empty:
        return pd.Series(50.0, index=col.index)      # není proti čemu normalizovat
    lo, hi = ref.min(), ref.max()
    worst = lo if higher_better else hi               # chybějící -> nejhorší
    col = col.fillna(worst)
    if hi == lo:
        return pd.Series(100.0, index=col.index)
    z = ((col - lo) / (hi - lo)).clip(0, 1)
    return (z if higher_better else 1 - z) * 100


def prepocti(df, dnes=None):
    """Dopočítá retrofit/efektivní cenu/skóre a seřadí (aktivní dle skóre, prodané
    na konec). Čistá funkce – nic neukládá. Normalizuje proti aktivním autům."""
    dnes = dnes or dt.date.today()
    df = df.copy()

    df["retrofit_Kc"] = df.apply(_retrofit_kc, axis=1)
    df["retrofit_co"] = df.apply(_retrofit_co, axis=1)
    df["efektivni_cena_Kc"] = df["cena_Kc"] + df["retrofit_Kc"]
    df["klima_skore"] = df["klima"].map(lambda k: C.KLIMA_SKORE[klima_tier(k)])

    df["_stk"] = df["STK_do"].map(
        lambda s: (parse_date(s) - dnes).days if parse_date(s) else 0)
    df["_rok"] = pd.to_numeric(
        df["rok"].astype(str).str.extract(r'(\d{4})')[0], errors="coerce")

    live = df[df["stav"] == "aktivní"]
    w = C.VAHY
    df["skore"] = (
        _norm(df["najezd_km"], live["najezd_km"], False) * w["najezd"]
        + _norm(df["efektivni_cena_Kc"], live["efektivni_cena_Kc"], False) * w["efektivni_cena"]
        + _norm(df["_rok"], live["_rok"], True) * w["rok"]
        + _norm(df["zmen_vlastnika"], live["zmen_vlastnika"], False) * w["majitele"]
        + _norm(df["_stk"], live["_stk"], True) * w["stk"]
        + df["klima_skore"] * w["klima"]
    ).round(1)

    # vše, co není aktivní (PRODÁNO, VYŘAZENO…), spadne pod aktivní auta
    df["_off"] = (df["stav"] != "aktivní").astype(int)
    df = df.sort_values(["_off", "skore"], ascending=[True, False]).reset_index(drop=True)
    df["poradi"] = range(1, len(df) + 1)

    df = df.drop(columns=["_stk", "_rok", "_off"])
    for c in C.SLOUPCE:
        if c not in df.columns:
            df[c] = float("nan")
    return df[C.SLOUPCE]


def prepocti_a_uloz(df, dnes=None):
    """Jako prepocti(), ale výsledek navíc uloží do DATA_FILE."""
    df = prepocti(df, dnes)
    df.to_excel(C.DATA_FILE, index=False)
    return df


def _sauto_url(item):
    """Sestaví detail URL z seo jmen značky/modelu (s id na konci)."""
    def seo(cb):
        return cb.get("seo_name") if isinstance(cb, dict) else None
    znacka = seo(item.get("manufacturer_cb")) or "auto"
    model = seo(item.get("model_cb")) or "x"
    return f"https://www.sauto.cz/osobni/detail/{znacka}/{model}/{item.get('id')}"


def _verdikt(vin_rep):
    """Text verdiktu podle zdroje reportu.

    Historii tachometru (stáčení) dává jen ruční doplnění z prohlížeče
    (klíče tampered/odo). Oficiální API tacho nemá – jen potvrdí, že auto je
    v registru a s kolika vlastníky. Neznámé/nedostupné = neutvrzujeme nic."""
    if vin_rep.get("tampered"):
        v = "⚠️ PODEZŘENÍ NA STÁČENÍ – ověřit"
    elif vin_rep.get("odo"):
        v = "OK – bez stáčení (monotónní)" if vin_rep.get("ok") \
            else "nelze ověřit (bez záznamů odometru)"
    elif vin_rep.get("source") == "api":
        v = "registr MD ✓ (tacho neověřeno)" if vin_rep.get("found") \
            else "není v registru MD (neověřeno)"
    elif vin_rep.get("source") == "error":
        v = "nelze ověřit (registr MD nedostupný)"
    else:
        v = "nelze ověřit (bez záznamů odometru)"
    if vin_rep.get("owners") and vin_rep["owners"] >= 4:
        v += f" ({vin_rep['owners']} majitelé)"
    return v


def nove_auto_row(item, vin_rep, dnes=None):
    """Sestaví řádek do žebříčku z sauto detailu + VIN reportu.

    pridano_dne = den prvního zařazení do žebříčku (default dnešek)."""
    dnes = dnes or dt.date.today()
    eq = _eq_names(item)
    znacka = ((item.get("manufacturer_cb") or {}).get("name")
              if isinstance(item.get("manufacturer_cb"), dict) else None) or ""
    has_tempo = any("empoma" in x.lower() or "cruise" in x.lower() for x in eq)
    park = [x for x in eq if "arkov" in x.lower() or "amera" in x.lower()]
    ac = _ac_name(item) or "?"          # neznámou klimu nehlásíme jako manuální

    in_op = item.get("in_operation_date")
    manuf = item.get("manufacturing_date")
    rok = (str(in_op)[:4] if in_op else (str(manuf)[:4] if manuf else ""))
    prvni_reg = ""
    if in_op:
        d = parse_date(in_op)
        prvni_reg = f"{d.day}.{d.month}.{d.year}" if d else str(in_op)
    elif manuf:
        prvni_reg = str(manuf)[:4]

    # STK a 1. registrace z registru MD jsou autoritativnější než ze sauto,
    # když je máme z API; jinak padáme na hodnoty z inzerátu.
    stk = item.get("stk_date")
    stk_do = vin_rep.get("stk_do") or (str(stk)[:10] if stk else "")
    prvni_reg = vin_rep.get("prvni_reg") or prvni_reg

    verdikt = _verdikt(vin_rep)

    return {
        "stav": "aktivní",
        "pridano_dne": dnes.isoformat(),
        "vuz": (item.get("name") or "").strip(),
        "znacka": znacka,
        "prodejce": prodejce_name(item),
        "cena_Kc": item.get("price"),
        "najezd_km": item.get("tachometer"),
        "vykon_kW": item.get("engine_power"),
        "rok": rok,
        "zmen_vlastnika": vin_rep["owners"] if vin_rep["owners"] is not None else "?",
        "STK_do": stk_do,
        "tempomat": "✅" if has_tempo else "—",
        "park_senzory": ("✅ " + ", ".join(p.replace("Parkovací senzory ", "")
                                            for p in park)) if park else "—",
        "klima": ac,
        "motor_kod": C.motor_kod(znacka, item.get("engine_volume"), item.get("name")),
        "turbo": "NE",
        "rozvod": "řetěz",
        "udrzba": "jednoduchá – bez turba",
        "prvni_registrace": prvni_reg,
        "pojistovna": float("nan"),
        "odometr_historie": vin_rep["odo_str"],
        "verdikt": verdikt,
        "vin": item.get("vin"),
        "url": _sauto_url(item),
        "vybava_vse": "; ".join(eq),
    }
