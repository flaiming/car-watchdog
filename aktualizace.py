#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Denní aktualizace žebříčku ojetých aut.

Co dělá:
  1) Projde inzeráty v žebříčku a označí nově prodané (status != active).
     Pokud se prodaný inzerát vrátí jako aktivní, vrátí ho mezi aktivní.
  2) Stáhne aktuální výsledky sledovaných filtrů, najde nová ID.
  3) Nová auta protřídí (atmosféra 1.6 + klima), u relevantních dohledá VIN,
     proklepne stáčení a přidá je do žebříčku.
  4) Přepočítá skóre, seřadí a uloží zpět do zebricek.xlsx.

Spuštění:
    python aktualizace.py            # ostrá aktualizace
    python aktualizace.py --dry-run  # jen ukáže změny, nic neuloží
"""
import sys
import datetime as dt
import pandas as pd
import config as C
import lib
import mailer


def main(dry_run=False, send_email=True):
    df = pd.read_excel(C.DATA_FILE)
    df["_id"] = df["url"].map(lib.id_from_url)
    znama_id = set(df["_id"].dropna().astype(int))

    nove_prodano, znovu_aktivni, nejiste, vyrazeno = [], [], [], []

    # 1) kontrola stavu existujících inzerátů
    print("=== 1) Kontrola stavu inzerátů ===")
    if "prodejce" not in df.columns:
        df["prodejce"] = pd.Series(pd.NA, index=df.index, dtype="object")
    if "prodano_dne" not in df.columns:
        df["prodano_dne"] = pd.Series(pd.NA, index=df.index, dtype="object")
    for idx, r in df.iterrows():
        stav_api, item = lib.sauto_check(r["_id"])
        # doplníme prodejce u starších řádků (sloupec přibyl později)
        if item and (pd.isna(r.get("prodejce")) or not str(r.get("prodejce")).strip()):
            df.at[idx, "prodejce"] = lib.prodejce_name(item)
        # osvěžíme klimu z živého API (starší řádky mívají zastaralý/špatný údaj –
        # mj. ručně naseedované auta měly "Manuální" i bez klimatizace)
        bez_klimy = False
        if item:
            ac = lib._ac_name(item)
            if ac:
                df.at[idx, "klima"] = ac
                bez_klimy = lib.nema_klimu(ac)
        if stav_api == "error":
            # nepodařilo se zeptat – stav NECHÁVÁME být (neoznačit jako prodané!)
            nejiste.append(r["vuz"])
            continue
        je_pryc = stav_api == "gone"
        bylo_prodano = r["stav"] == "PRODÁNO"
        if je_pryc and not bylo_prodano:
            df.at[idx, "stav"] = "PRODÁNO"
            df.at[idx, "prodano_dne"] = dt.date.today().isoformat()
            # Uložíme celý řádek tak, jak auto vypadalo ve včerejším žebříčku
            # (pořadí, skóre, cena, nájezd…) – po přepočtu se prodaná řadí na
            # konec, takže poradi i skóre by už nesedělo. Mail pak ukáže plné info.
            snap = {k: r[k] for k in ("poradi", "skore", "vuz", "prodejce", "cena_Kc",
                                      "najezd_km", "rok", "tempomat",
                                      "park_senzory", "klima", "url")}
            snap["poradi"] = int(snap["poradi"]) if pd.notna(snap["poradi"]) else None
            nove_prodano.append(snap)
            print(f"  🔴 NOVĚ PRODÁNO (#{snap['poradi']}): {r['vuz']}")
        elif not je_pryc and bylo_prodano and not bez_klimy:
            df.at[idx, "stav"] = "aktivní"
            df.at[idx, "prodano_dne"] = pd.NA   # vrátilo se do prodeje – datum už neplatí
            znovu_aktivni.append(r["vuz"])
            print(f"  🟢 ZNOVU AKTIVNÍ: {r['vuz']}")

        # KLIMA_POVINNA platí i pro auta, co se do žebříčku dostala dřív (ručně
        # naseedované) nebo se vracejí z PRODÁNO. Rozhodujeme na FINÁLNÍM stavu:
        # když inzerát hlásí "bez klimatizace", auto z aktivního pořadí vyřadíme.
        if C.KLIMA_POVINNA and bez_klimy and df.at[idx, "stav"] == "aktivní":
            df.at[idx, "stav"] = "VYŘAZENO – bez klimy"
            vyrazeno.append(r["vuz"])
            print(f"  🚫 VYŘAZENO (bez klimatizace): {r['vuz']}")
    if not nove_prodano and not znovu_aktivni and not vyrazeno:
        print("  beze změny – vše jako dřív")
    if nejiste:
        print(f"  ⚠️ {len(nejiste)} inzerátů se nepodařilo ověřit (síť) – stav ponechán beze změny")

    # 2) nová ID ve filtrech
    print("\n=== 2) Kontrola filtrů na nové inzeráty ===")
    nova_id = set()
    for popis, url in C.FILTRY.items():
        try:
            ids = lib.sauto_filter_ids(url)
        except Exception as e:
            print(f"  ! filtr '{popis}' nešel načíst: {e}")
            continue
        chybi = [i for i in ids if i not in znama_id]
        print(f"  {popis}: {len(ids)} inzerátů, {len(chybi)} nových")
        nova_id.update(chybi)

    # 3) protřídit a přidat relevantní
    print("\n=== 3) Třídění nových inzerátů ===")
    pridano = []
    for i in sorted(nova_id):
        item = lib.sauto_item(i)
        if not item:
            continue
        relevant, duvod = lib.classify(item)
        nazev = (item.get("name") or "")[:40]
        if not relevant:
            print(f"  ⏭️  {i} {nazev} → {duvod}")
            continue
        vin = item.get("vin")
        if not vin or "XXXX" in vin:
            print(f"  ⚠️  {i} {nazev} → relevantní, ale VIN chybí/maskován – přidávám bez ověření")
            vrep = {"owners": None, "odo": [], "odo_str": "VIN maskován",
                    "tampered": False, "ok": False}
        else:
            vrep = lib.vin_report(vin)          # oficiální registr MD (dataovozidlech)
            if vrep.get("source") == "error":
                stav = "❓ registr nedostupný"
            elif not vrep.get("found"):
                stav = "❓ není v registru MD"
            else:
                stk = vrep.get("stk_do") or "?"
                stav = f"✅ registr MD ({vrep['owners']} vlast., STK do {stk})"
            print(f"  ➕ {i} {nazev} → {duvod} | {stav}")
        row = lib.nove_auto_row(item, vrep)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        pridano.append(row["vuz"])
    if not pridano:
        print("  žádné nové relevantní auto")

    # 4) přepočet a uložení
    df = df.drop(columns=["_id"])
    if dry_run:
        print("\n[DRY-RUN] nic se neukládá.")
    else:
        df = lib.prepocti_a_uloz(df)
        print(f"\n=== Uloženo do {C.DATA_FILE.name} ===")

    # souhrn
    akt = (df["stav"] == "aktivní").sum()
    pro = (df["stav"] == "PRODÁNO").sum()
    print(f"\nSOUHRN: {akt} aktivních + {pro} prodaných")
    print(f"  nově prodáno: {len(nove_prodano)} | znovu aktivní: {len(znovu_aktivni)} "
          f"| přidáno nových: {len(pridano)} | vyřazeno (bez klimy): {len(vyrazeno)}")
    if not dry_run:
        print("\nTOP 5:")
        for _, r in df[df["stav"] == "aktivní"].head(5).iterrows():
            print(f"  #{r['poradi']:2} {r['skore']:>5} | {r['vuz'][:40]:40} "
                  f"| {r['cena_Kc']} Kč | {r['najezd_km']} km")

    # 5) e-mail
    changes = {"prodano": nove_prodano, "aktivni": znovu_aktivni, "pridano": pridano}
    ma_zmenu = bool(nove_prodano or znovu_aktivni or pridano or vyrazeno)
    if not dry_run and send_email and C.EMAIL.get("enabled"):
        if C.EMAIL.get("only_on_change") and not ma_zmenu:
            print("  📭 beze změny – e-mail se neposílá")
        else:
            subject, text, html = mailer.build_summary(df, changes, dt.date.today().isoformat())
            mailer.send(subject, text, html)
    return df


def main_email_preview():
    """Sestaví souhrn z aktuálních dat bez aktualizace (pro test odeslání)."""
    df = pd.read_excel(C.DATA_FILE)
    changes = {"prodano": [], "aktivni": [], "pridano": []}
    return mailer.build_summary(df, changes, dt.date.today().isoformat())


if __name__ == "__main__":
    if "--email-test" in sys.argv:
        # Pošle souhrn z aktuálních dat (ověření SMTP), bez aktualizace.
        subj, text, html = main_email_preview()
        print(f"Subject: {subj}\n\n{text}\n")
        mailer.send(subj, text, html)
    else:
        main(dry_run="--dry-run" in sys.argv,
             send_email="--no-email" not in sys.argv)
