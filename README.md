# Žebříček ojetých aut (Kia Ceed SW / Hyundai i30 kombi)

Skripty pro denní aktualizaci scoreboardu kandidátů na koupi. Vše na jednom místě:
data, skripty i venv.

## Co to dělá
1. **Kontrola stavu** – projde inzeráty v `zebricek.xlsx` přes sauto API a označí
   nově prodané (`stav = PRODÁNO`). Když se prodaný inzerát vrátí, vrátí ho mezi aktivní.
2. **Nové ve filtrech** – stáhne sledované sauto filtry a najde ID, která zatím nesledujeme.
3. **Třídění + ověření** – nová auta protřídí (jen **atmosféra 1.5/1.6 + klima**, bez turba,
   bez LPG), u relevantních ověří VIN přes **oficiální Registr silničních vozidel**
   (dataovozidlech.cz API): počet vlastníků, platnost STK, 1. registrace, a doplní
   **historii tachometru** z kontrola-vin.cz (stáčení). Přidá je.
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

## Nasazení (produkce)
Běží na Raspberry Pi: **`pi@Doma:~/auta`**. Kód se tam dostává z GitHubu
(`git@github.com:flaiming/car-watchdog.git`); `zebricek.xlsx` na Pi jsou **živá
data** (cron je průběžně aktualizuje) – při deployi se nesmí přepsat verzí z repa.

Cron na Pi spouští aktualizaci každou hodinu od 7 do 22:
```cron
5 7-22 * * * /home/pi/auta/aktualizovat.sh >> /home/pi/auta/aktualizace.log 2>&1
```

Deploy nové verze (z vývojového stroje):
```bash
git push
ssh pi@Doma 'cd ~/auta && git fetch origin \
  && git reset origin/main && git checkout -- . ":(exclude)zebricek.xlsx"'
```
`git status` na Pi bude `zebricek.xlsx` hlásit jako modified – to je v pořádku
(živá data se od commitnutého snapshotu liší). `.env` je v `.gitignore`, deploy
se ho nedotkne.

## Scoring (váhy)
nájezd 28 % · efektivní cena 24 % · rok 19 % · majitelé 14 % · STK 10 % · klima 5 %

**Efektivní cena** = cena + retrofit (senzory ~3 000 Kč, tempomat ~3 000 Kč) – auto se
nepenalizuje za chybějící výbavu, kterou lze levně domontovat. Automatická klima má
malý bonus. Filtry a váhy se mění v `config.py`.

## Ověření VIN (registr MD)
Ověření běží přes oficiální API Registru silničních vozidel
([dataovozidlech.cz](https://dataovozidlech.cz)). Potřebuje **free API klíč**
(registrace: <https://dataovozidlech.cz/registraceapi>, limit 27 dotazů/min),
který patří do `.env` jako `AUTA_DOV_API_KEY` (viz `.env.example`).
- Dává **počet vlastníků, platnost STK a 1. registraci** – NE historii tachometru.
- **Stáčení** se ověřuje zvlášť z [kontrola-vin.cz](https://www.kontrola-vin.cz)
  (`km_historie()` / `parse_km_historie()`) – parsuje tabulku „Historie STK a SME",
  tedy stavy km zapsané při technických. Stránka byla 22.6.2026 za Cloudflare
  (403 z Pi), od 21.7.2026 se načítá zase i z Pi. Kdyby se blokace vrátila,
  `km_historie()` vrátí `None` a auto se přidá bez ověření tacha.
- Verdikt hlásí stáčení i tehdy, když inzerát uvádí **míň km než poslední STK**.
- Bez klíče (nebo při výpadku API) se auto přidá, ale bez ověření z registru.

## Poznámky / limity
- VIN check funguje jen pro auta registrovaná v ČR (dovozy a maskované VINy nelze ověřit).
- Auto se zamaskovaným VINem (`…XXXX`) se přidá, ale s poznámkou „nelze ověřit".
- Objem 1560 ccm = diesel/1.6 HDi → bereme se jako neatmosférické a vyřadí se.
