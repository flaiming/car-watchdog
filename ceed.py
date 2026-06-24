#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI žebříček – Kia Ceed kombi, atmosférický benzin (bez turba) + skóre + VIN.

Stáhne sauto filtr, protřídí turbo vs. atmosféra, u atmosfér dohledá VIN
(kontrola-vin.cz: počet majitelů + stáčení odometru), spočítá skóre a seřadí.
Žádný export – jen výpis do konzole.

Filtr (stejný jako v prohlížeči):
  Kia Ceed (39:1334,9377), kombi, benzin, objem od 1500, cena do 300k, 2017–2026.

Výpis na sauto je JS/SSR – API /items/search ignoruje filtr, ale stránka má
výsledky vyrenderované přímo v HTML (klíč items/search?{...} -> body.results).
Odtud bereme ID, detaily pak přes /api/v1/items/{id}.

VIN a parsování odometru přebíráme z lib.py (sdíleno se stávajícím žebříčkem).
"""
import json, re, sys, datetime as dt, urllib.request, urllib.parse
import lib

FILTR_URL = ("https://www.sauto.cz/inzerce/osobni?znacky-modely=39%3A1334%2C9377"
             "&cena-do=300000&vyrobeno-od=2017&vyrobeno-do=2026"
             "&objem-od=1500&palivo=benzin&typ=kombi")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "cs-CZ,cs;q=0.9"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _unescape_js(s):
    """Odescapuje JS-string uvozovky a lomítka, UTF-8 nechá být."""
    return s.replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')


def filtr_ids(url):
    """Vytáhne ID inzerátů z SSR JSONu vloženého ve filtrovací stránce.

    Hledá všechny embedded odpovědi items/search a vrátí results z té, která
    má neprázdné pole results (limit > 0). Robustní vůči pořadí fragmentů.
    """
    h = _get(url).decode("utf-8", "replace")
    u = _unescape_js(h)
    dec = json.JSONDecoder()
    best = []
    pos = 0
    while True:
        m = u.find('"body":', pos)
        if m < 0:
            break
        brace = u.find('{', m)
        pos = brace + 1
        try:
            body, _ = dec.raw_decode(u, brace)
        except Exception:
            continue
        res = body.get("results") if isinstance(body, dict) else None
        if isinstance(res, list) and res and "id" in res[0]:
            # vezmeme nejbohatší blok (nejvíc položek)
            if len(res) > len(best):
                best = res
    return [r["id"] for r in best]


# ---------- detail a klasifikace ----------
def sauto_item(item_id):
    try:
        data = json.loads(_get(f"https://www.sauto.cz/api/v1/items/{item_id}"))
        return data.get("result")
    except Exception as e:
        print(f"  ! sauto_item({item_id}) chyba: {e}", file=sys.stderr)
        return None


# atmosférický (bez turba) benzinový Ceed: 1.4 MPI (G4LC) a 1.6 GDI (G4FG/G4FD).
# Turbo: 1.0 / 1.4 / 1.5 T-GDI a 1.6 T-GDI (GT). Rozlišujeme dle názvu + výkonu.
TURBO_RE = re.compile(r"\bt[\s\-]?gdi\b|\bturbo\b|\bt-?gdi\b|\bgt\b|\bt\b(?=\s|$)", re.I)


def je_turbo(item):
    """Vrátí (turbo: bool, duvod: str). Atmosféra = bez turba."""
    txt = " ".join(str(item.get(k) or "") for k in
                   ("name", "additional_model_name")).lower()
    vol = item.get("engine_volume") or 0
    kw = item.get("engine_power") or 0

    # explicitní turbo značky v názvu
    if re.search(r"t[\s\-]?gdi|turbo", txt):
        return True, "turbo (T-GDI/turbo v názvu)"
    # Ceed GT / 1.6 T-GDI 150 kW (204 PS) – stejný objem 1591 jako atmosféra
    if "gt" in re.findall(r"[a-z]+", txt) and kw and kw >= 150:
        return True, f"turbo (GT, {kw} kW)"
    # bezpečnostní pojistka dle výkonu: atmosféra 1.6 GDI má ~99–103 kW
    if vol and vol <= 1620 and kw and kw >= 130:
        return True, f"podezření na turbo (vysoký výkon {kw} kW)"
    return False, "atmosféra (bez turba)"


# ---------- skóre ----------
# Auta jsou motoricky shodná (1.6 GDI 99 kW), takže rozhoduje cena, nájezd,
# stáří, počet majitelů a STK. Nižší = lepší u ceny/nájezdu/majitelů.
VAHY = {
    "najezd": 0.34,      # nižší = lepší
    "cena": 0.26,        # nižší = lepší
    "majitele": 0.18,    # méně = lepší (z VINu; neznámé = nejhorší)
    "stk": 0.12,         # delší platnost = lepší
    "rok": 0.10,         # vyšší = lepší
}
STACENI_PENALIZACE = 1000.0   # podezření na stáčení = na konec žebříčku


def _norm(vals, higher_better):
    """Min-max 0–100 přes kandidáty. None/neznámé -> nejhorší konec škály."""
    cisla = [v for v in vals if isinstance(v, (int, float))]
    if not cisla:
        return [50.0] * len(vals)
    lo, hi = min(cisla), max(cisla)
    worst = lo if higher_better else hi
    out = []
    for v in vals:
        x = v if isinstance(v, (int, float)) else worst
        if hi == lo:
            out.append(100.0)
            continue
        z = max(0.0, min(1.0, (x - lo) / (hi - lo)))
        out.append((z if higher_better else 1 - z) * 100)
    return out


def _stk_dnu(stk_do, dnes):
    d = lib.parse_date(stk_do) if stk_do else None
    return (d - dnes).days if d else None


def spocti_skore(atmo, dnes):
    najezd = _norm([r["km"] for r in atmo], False)
    cena = _norm([r["cena"] for r in atmo], False)
    majitele = _norm([r["majitele"] for r in atmo], False)
    stk = _norm([r["stk_dnu"] for r in atmo], True)
    rok = _norm([int(r["rok"]) if r["rok"].isdigit() else None for r in atmo], True)
    for i, r in enumerate(atmo):
        s = (najezd[i] * VAHY["najezd"] + cena[i] * VAHY["cena"]
             + majitele[i] * VAHY["majitele"] + stk[i] * VAHY["stk"]
             + rok[i] * VAHY["rok"])
        if r["tampered"]:
            s -= STACENI_PENALIZACE
        r["skore"] = round(s, 1)
    atmo.sort(key=lambda r: r["skore"], reverse=True)


def main():
    dnes = dt.date.today()
    print(f"Stahuji filtr…  {FILTR_URL}\n")
    ids = filtr_ids(FILTR_URL)
    print(f"Nalezeno {len(ids)} inzerátů ve filtru. Stahuji detaily + VIN…\n")

    atmo, turbo, chyby = [], [], []
    for i in ids:
        it = sauto_item(i)
        if not it:
            chyby.append(i)
            continue
        t, duvod = je_turbo(it)
        rec = {
            "id": i,
            "vuz": (it.get("name") or "").strip(),
            "var": it.get("additional_model_name") or "",
            "cena": it.get("price"),
            "km": it.get("tachometer"),
            "kw": it.get("engine_power"),
            "ccm": it.get("engine_volume"),
            "rok": str(it.get("in_operation_date") or it.get("manufacturing_date") or "")[:4],
            "stk_do": str(it.get("stk_date") or "")[:10],
            "vin": it.get("vin"),
            "duvod": duvod,
            "url": f"https://www.sauto.cz/osobni/detail/kia/ceed/{i}",
        }
        if t:
            turbo.append(rec)
            continue
        # VIN kontrola jen pro atmosféry (kandidáti do žebříčku)
        vin = lib.vin_report(rec["vin"]) if rec["vin"] else {
            "owners": None, "odo_str": "bez VIN", "tampered": False, "ok": False}
        rec["majitele"] = vin["owners"]
        rec["tampered"] = vin["tampered"]
        rec["odo"] = vin["odo_str"]
        if vin["tampered"]:
            rec["verdikt"] = "⚠️ STÁČENÍ?"
        elif vin["ok"]:
            rec["verdikt"] = "OK"
        else:
            rec["verdikt"] = "VIN?"
        rec["stk_dnu"] = _stk_dnu(rec["stk_do"], dnes)
        atmo.append(rec)

    spocti_skore(atmo, dnes)

    print("=" * 86)
    print(f"  ŽEBŘÍČEK – Kia Ceed 1.6 GDI atmosféra ({len(atmo)} aut), řazeno dle skóre")
    print("=" * 86)
    for n, r in enumerate(atmo, 1):
        cenas = f"{r['cena']:,}".replace(",", " ") if isinstance(r['cena'], (int, float)) else "?"
        kms = f"{r['km']:,}".replace(",", " ") if isinstance(r['km'], (int, float)) else "?"
        maj = r["majitele"] if r["majitele"] is not None else "?"
        stk = r["stk_do"] or "?"
        print(f"{n:>2}. [{r['skore']:>6.1f}] {cenas:>9} Kč | {kms:>9} km | "
              f"{maj} maj. | STK {stk} | {r['rok']} | {r['verdikt']}")
        print(f"     {r['var'] or r['vuz']}")
        print(f"     odometr: {r['odo'] or '—'}")
        print(f"     {r['url']}")

    if turbo:
        print("\n" + "-" * 86)
        print(f"  VYŘAZENO – turbo ({len(turbo)})")
        print("-" * 86)
        for r in turbo:
            cenas = f"{r['cena']:,} Kč".replace(",", " ") if isinstance(r['cena'], (int, float)) else "?"
            print(f"  {r['id']} | {cenas:>12} | {r['kw'] or '?'} kW | {r['var']}  →  {r['duvod']}")

    if chyby:
        print(f"\n! Nepodařilo se načíst detail u {len(chyby)} ID: {chyby}")


if __name__ == "__main__":
    main()
