# -*- coding: utf-8 -*-
"""Sdílené funkce: sauto API, registr MD + historie tachometru, scoring.

Žádný stav se tu nedrží – jen čisté funkce, které volá aktualizace.py.
"""
import re, json, datetime as dt, urllib.request, urllib.error
import pandas as pd
import config as C

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Tolerance poklesu km, než to označíme za stáčení:
TOL_STK = 500        # mezi dvěma záznamy STK (STK vs SME týž den se liší o pár km)
TOL_INZERAT = 2000   # inzerát vs poslední STK (prodejci nájezd zaokrouhlují dolů)


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


def cena_uver(item):
    """Akční cena při financování (price_leasing), když je nižší než běžná.

    Bazary (AAA, Auto ESA…) inzerují nižší cenu podmíněnou úvěrem – na sautu je
    v price_leasing, na vlastním webu bazaru jako "akční cena". Do skóre se
    nepočítá (je podmíněná), ale je vidět v žebříčku i v mailu."""
    cena, uver = item.get("price"), item.get("price_leasing")
    if isinstance(cena, (int, float)) and isinstance(uver, (int, float)) \
            and 0 < uver < cena:
        return int(uver)
    return None


# Slugy modelů se na webech bazarů liší od sauto seo_name (Kia "cee-d" vs "ceed").
BAZAR_MODEL = {"cee-d": "ceed"}


def _seo(cb, nahrada=""):
    return (cb.get("seo_name") or nahrada) if isinstance(cb, dict) else nahrada


def bazar_url(item):
    """Odkaz na inzerát na webu bazaru (AAA AUTO / Auto ESA), jinak None.

    Proč to nejde jednotně: Auto ESA má v sauto custom_id přímo své ID vozu,
    takže se dá složit rovnou detail. AAA AUTO svoje ID v sauto nemá (custom_id
    je jiné číslo než v URL), zato jde odkázat na výpis filtrovaný na model
    a nájezd ±200 km – to vrátí prakticky vždy právě to jedno auto.
    """
    prodejce = prodejce_name(item).lower()
    znacka = _seo(item.get("manufacturer_cb"))
    model = _seo(item.get("model_cb"))
    if not znacka or not model:
        return None
    model = BAZAR_MODEL.get(model, model)

    if "aaa auto" in prodejce:
        km = item.get("tachometer")
        if not isinstance(km, (int, float)):
            return None
        return (f"https://www.aaaauto.cz/ojete-vozy/{znacka}/{model}"
                f"?mileageFrom={int(km) - 200}&mileageTo={int(km) + 200}")

    if "auto esa" in prodejce:
        cid = str(item.get("custom_id") or "").strip()
        karoserie = _seo(item.get("vehicle_body_cb"))
        palivo = _seo(item.get("fuel_cb"))
        if not (cid.isdigit() and karoserie and palivo):
            return None
        return f"https://www.autoesa.cz/{znacka}/{model}/{karoserie}/{palivo}/{cid}"

    return None


def classify(item):
    """Rozhodne, jestli auto patří do žebříčku (atmosféra 1.5/1.6 + klima).

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
        return False, f"není atmosféra 1.5/1.6 ({vol} ccm – turbo/diesel/jiné)"
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

    return True, "OK – atmosféra 1.5/1.6 + klima"


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
    """Ověří VIN přes oficiální registr (dataovozidlech.cz) + historii tachometru.

    Vrací report se stejnými klíči jako dřív (owners/odo/odo_str/tampered/ok)
    plus stk_do, prvni_reg, found, source – aby na něj navazující kód i testy
    nemusely měnit tvar. Registr MD dává vlastníky/STK/1. registraci, historii
    tachometru (stáčení) doplní kontrola-vin.cz – viz km_historie().
    """
    stav, vozidlo = dov_vehicle(vin)
    if stav == "ok":
        rep = report_z_dov(vozidlo)
    elif stav == "gone":
        rep = _prazdny_report(odo_str="není v registru MD", source="api")
    else:
        rep = _prazdny_report(odo_str="VIN nelze ověřit (API nedostupné)", source="error")

    km = km_historie(vin)
    if km and km["odo"]:
        rep.update(km)          # odo, odo_str, tampered, ok
    return rep


# ---------- historie tachometru (kontrola-vin.cz) ----------
# Registr MD stavy tachometru z STK nepublikuje, kontrola-vin.cz ano.
# 22.6.2026 byla stránka za Cloudflare (403 z Pi) a jelo se bez ní; 21.7.2026
# už se načítá i z Pi, takže se stáčení zase ověřuje automaticky. Kdyby se
# Cloudflare vrátil, km_historie jen vrátí None a report zůstane bez odo.
def _get_kv(vin):
    return _get(f"https://www.kontrola-vin.cz/{vin}").decode("utf-8", "replace")


def parse_km_historie(html):
    """Vyparsuje tabulku 'Historie STK a SME' (sloupec Stav km) z kontrola-vin.cz.

    Čistá funkce, bez sítě. Vrací {odo:[(datum,km)], odo_str, tampered, ok}.
    Pokles nad TOL_STK mezi záznamy = stáčení.
    """
    out = {"odo": [], "odo_str": "", "tampered": False, "ok": False}
    m = re.search(r'id="box-km".*?</table>', html, flags=re.S)
    if not m:
        return out

    zaznamy = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(0), flags=re.S):
        d = re.search(r'<th[^>]*>\s*(\d{1,2}\.\d{1,2}\.\d{4})', tr)
        bunky = [re.sub(r'<[^>]+>', ' ', c).strip()
                 for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)]
        # sloupce: 0 = Kontrola/Druh, 1 = Výsledek/Protokol, 2 = Stav km
        if not d or len(bunky) < 3 or not re.fullmatch(r'\d{3,7}', bunky[2]):
            continue
        zaznamy.append((parse_date(d.group(1)), d.group(1), int(bunky[2])))

    # stránka řadí od nejstarší, ale nespoléháme na to – řadíme podle data
    zaznamy.sort(key=lambda z: z[0] or dt.date.min)
    # STK a SME týž den = dva řádky se stejným stavem km, do historie stačí jeden
    odo, videno = [], set()
    for _, datum, km in zaznamy:
        if (datum, km) not in videno:
            videno.add((datum, km))
            odo.append((datum, km))
    out["odo"] = odo
    out["odo_str"] = "; ".join(f"{datum}:{km}" for datum, km in out["odo"])
    kms = [km for _, km in out["odo"]]
    out["tampered"] = any(kms[i] - kms[i + 1] > TOL_STK for i in range(len(kms) - 1))
    out["ok"] = bool(kms) and not out["tampered"]
    return out


def km_historie(vin):
    """Historie tachometru z kontrola-vin.cz. None = stránku se nepodařilo načíst."""
    try:
        return parse_km_historie(_get_kv(vin))
    except Exception as e:
        print(f"  ! kontrola-vin({vin}): {e} – tacho se neověří")
        return None


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
    # akční cena při financování je jen informativní – do efektivní ceny (a tedy
    # do skóre) nevstupuje, protože je podmíněná sjednáním úvěru
    if "cena_uver_Kc" not in df.columns:
        df["cena_uver_Kc"] = float("nan")
    df["sleva_uver_Kc"] = pd.to_numeric(df["cena_Kc"], errors="coerce") \
        - pd.to_numeric(df["cena_uver_Kc"], errors="coerce")
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


def _verdikt(vin_rep, najezd=None):
    """Text verdiktu podle zdroje reportu.

    Historii tachometru (stáčení) dává kontrola-vin.cz (klíče tampered/odo),
    oficiální API MD tacho nemá – jen potvrdí, že auto je v registru a s kolika
    vlastníky. Neznámé/nedostupné = neutvrzujeme nic.

    najezd = km z inzerátu; když je nižší než poslední stav na STK, sedí to
    ještě hůř než nemonotónní historie – auto od té doby jezdit nepřestalo.
    Tolerance je tu volnější (TOL_INZERAT) než mezi záznamy STK: inzeráty
    nájezd běžně zaokrouhlují dolů ("72 000 km" místo 72 901)."""
    odo = vin_rep.get("odo") or []
    posledni = odo[-1][1] if odo else None
    if isinstance(najezd, (int, float)) and posledni and najezd < posledni - TOL_INZERAT:
        return (f"⚠️ PODEZŘENÍ NA STÁČENÍ – inzerát {int(najezd)} km "
                f"< STK {posledni} km")
    if vin_rep.get("tampered"):
        v = "⚠️ PODEZŘENÍ NA STÁČENÍ – ověřit"
    elif odo:
        if not vin_rep.get("ok"):
            v = "nelze ověřit (bez záznamů odometru)"
        elif len(odo) == 1:
            v = f"OK – 1 záznam tacha ({odo[0][0]}: {odo[0][1]} km)"
        else:
            v = "OK – bez stáčení (monotónní)"
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

    verdikt = _verdikt(vin_rep, item.get("tachometer"))

    return {
        "stav": "aktivní",
        "pridano_dne": dnes.isoformat(),
        "vuz": (item.get("name") or "").strip(),
        "znacka": znacka,
        "prodejce": prodejce_name(item),
        "cena_Kc": item.get("price"),
        "cena_uver_Kc": cena_uver(item),
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
        "url_bazar": bazar_url(item),
        "vybava_vse": "; ".join(eq),
    }
