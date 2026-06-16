# Žebříček ojetých aut (Citroën Berlingo / Peugeot Partner / Dacia Lodgy+Dokker)

Skripty pro denní aktualizaci scoreboardu kandidátů na koupi. Vše na jednom místě:
data, skripty i venv.

## Co to dělá
1. **Kontrola stavu** – projde inzeráty v `zebricek.xlsx` přes sauto API a označí
   nově prodané (`stav = PRODÁNO`). Když se prodaný inzerát vrátí, vrátí ho mezi aktivní.
2. **Nové ve filtrech** – stáhne sledované sauto filtry a najde ID, která zatím nesledujeme.
3. **Třídění + ověření** – nová auta protřídí (jen **atmosféra 1.6 + klima**, bez turba,
   bez LPG), u relevantních dohledá VIN na kontrola-vin.cz, **proklepne stáčení**
   (monotónní odometr, tolerance 500 km) a přidá je.
4. **Přepočet** – spočítá skóre, seřadí (aktivní dle skóre, prodaná na konec) a uloží.

## Soubory
| soubor | obsah |
|---|---|
| `zebricek.xlsx` | **data + výstup** (jediný export) |
| `config.py` | filtry, váhy skóre, kritéria, mapování motorů – **co se ladí** |
| `lib.py` | funkce (sauto, VIN, scoring) |
| `mailer.py` | sestavení + odeslání denního e-mailu |
| `aktualizace.py` | hlavní denní běh |
| `aktualizovat.sh` | spouštěč (aktivuje venv) |
| `test_lib.py` | offline pytesty (bez sítě) |
| `requirements.txt` | závislosti |
| `.env.example` | šablona pro SMTP údaje (commituje se) |
| `.env` | **reálné SMTP údaje – NEcommituje se** (v .gitignore) |
| `.venv/` | virtuální prostředí |

`config.py` neobsahuje žádná tajemství, takže ho lze klidně commitnout – citlivé údaje jsou v `.env`.

## Spuštění
```bash
cd ~/auta
./aktualizovat.sh --dry-run   # náhled: co by se změnilo, nic neuloží
./aktualizovat.sh             # ostrá aktualizace zebricek.xlsx
```

## Testy
```bash
.venv/bin/pytest -q
```
Testy jsou offline (parsování VINu, scoring, třídění) – nesahají na síť ani na `zebricek.xlsx`.

## E-mailový souhrn
Údaje jsou v `.env` (nikoli v configu). Nastavení:
```bash
cp .env.example .env       # už hotovo
nano .env                  # vyplň AUTA_SMTP_USER / AUTA_SMTP_PASS a dej AUTA_MAIL_ENABLED=true
.venv/bin/python aktualizace.py --email-test   # pošle souhrn TEĎ (ověření SMTP)
```
- **Gmail:** `AUTA_SMTP_PASS` = App password (16 znaků), ne hlavní heslo. Vyžaduje zapnuté 2FA.
- Proměnná z prostředí má přednost před `.env` (hodí se pro cron/CI).
- `./aktualizovat.sh --no-email` = aktualizace bez odeslání.

## Denní spouštění (cron)
Každý den v 9:00:
```cron
0 9 * * *  /home/flaim/auta/aktualizovat.sh >> /home/flaim/auta/aktualizace.log 2>&1
```

## Scoring (váhy)
nájezd 28 % · efektivní cena 24 % · rok 19 % · majitelé 14 % · STK 10 % · klima 5 %

**Efektivní cena** = cena + retrofit (senzory ~3 000 Kč, tempomat ~3 000 Kč) – auto se
nepenalizuje za chybějící výbavu, kterou lze levně domontovat. Automatická klima má
malý bonus. Filtry a váhy se mění v `config.py`.

## Poznámky / limity
- VIN check funguje jen pro auta registrovaná v ČR (dovozy a maskované VINy nelze ověřit).
- Auto se zamaskovaným VINem (`…XXXX`) se přidá, ale s poznámkou „nelze ověřit".
- Objem 1560 ccm = diesel/1.6 HDi → bereme se jako neatmosférické a vyřadí se.
