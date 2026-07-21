# -*- coding: utf-8 -*-
"""Sestavení a odeslání denního souhrnu e-mailem."""
import smtplib
import ssl
from email.message import EmailMessage

import pandas as pd

import config as C


def build_summary(df, changes, datum):
    """Sestaví (subject, text, html) z dataframu a slovníku změn.

    changes = {"prodano": [...], "aktivni": [...], "pridano": [...]}  (seznamy názvů)
    Čistá funkce – nic neposílá, jde testovat offline.
    """
    akt = int((df["stav"] == "aktivní").sum())
    pro = int((df["stav"] == "PRODÁNO").sum())
    np_, za, pr = changes["prodano"], changes["aktivni"], changes["pridano"]
    zmen = len(np_) + len(za) + len(pr)

    def _as_row(it):
        """Snese dict (plný řádek), (poradi, název) i holý název."""
        if isinstance(it, dict):
            return it
        if isinstance(it, (tuple, list)):
            return {"poradi": it[0], "vuz": it[1]}
        return {"vuz": it}

    def _misto(p):
        return f"#{int(p)}" if p is not None and pd.notna(p) else "#?"

    def _ck(v):
        """Číslo s mezerami po tisících, jinak placeholder."""
        return f"{v:,}".replace(",", " ") if isinstance(v, (int, float)) and pd.notna(v) else "?"

    def _cena(r):
        """Cena, u akčních (při financování) i ta nižší: '260 000 / úvěr 210 000'."""
        c = _ck(r.get("cena_Kc"))
        u = r.get("cena_uver_Kc")
        if isinstance(u, (int, float)) and pd.notna(u):
            return f"{c} / úvěr {_ck(int(u))}"
        return c

    def _prodejce(r):
        p = r.get("prodejce")
        return str(p) if p is not None and pd.notna(p) else "?"

    def _radek_text(r):
        return (f"  {_misto(r.get('poradi')):>3}  {_ck(r.get('skore')):>5}  "
                f"{str(r.get('vuz', ''))[:42]:42}  {_prodejce(r)[:18]:18}  "
                f"{_cena(r)} Kč  "
                f"{_ck(r.get('najezd_km'))} km  {r.get('rok', '?')}")

    def _radek_html(r):
        url = r.get("url")
        vuz = str(r.get("vuz", ""))
        vuz_html = f"<a href='{url}'>{vuz}</a>" if url and pd.notna(url) else vuz
        # u AAA AUTO / Auto ESA ještě odkaz na jejich vlastní web (akční cena, fotky)
        bazar = r.get("url_bazar")
        if bazar and pd.notna(bazar):
            vuz_html += f" <a href='{bazar}' title='inzerát u bazaru'>↗ bazar</a>"
        return (f"<tr><td>{_misto(r.get('poradi'))}</td><td><b>{r.get('skore', '?')}</b></td>"
                f"<td>{vuz_html}</td><td>{_prodejce(r)}</td>"
                f"<td align='right'>{_cena(r)} Kč</td>"
                f"<td align='right'>{_ck(r.get('najezd_km'))} km</td><td>{r.get('rok', '?')}</td>"
                f"<td>{r.get('tempomat', '')}</td><td>{r.get('park_senzory', '')}</td>"
                f"<td>{r.get('klima', '')}</td></tr>")

    def _tabulka(radky):
        return ('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
                '<tr style="background:#f1f3f4"><th>#</th><th>Skóre</th><th>Vůz</th><th>Prodejce</th><th>Cena</th>'
                '<th>Nájezd</th><th>Rok</th><th>Tempo</th><th>Senzory</th><th>Klima</th></tr>'
                f'{radky}</table>')

    np_rows = [_as_row(x) for x in np_]

    subject = (f"🚗 Auta {datum}: "
               + (f"{len(pr)} nové, {len(np_)} prodané" if zmen else "beze změny")
               + f" ({akt} aktivních)")

    # --- text ---
    def _radky_text(rows):
        out = []
        for r in rows:
            out.append(_radek_text(r))
            url = r.get("url")
            if url and pd.notna(url):
                out.append(f"        {url}")
        return out

    L = [f"Denní souhrn žebříčku aut – {datum}", "=" * 40, ""]
    if zmen == 0:
        L.append("Žádné změny – stav i nabídky jako včera.")
    else:
        if pr:
            L.append(f"🆕 Přidáno ({len(pr)}):")
            L += [f"   + {v}" for v in pr]
        if np_:
            L.append(f"🔴 Nově prodáno ({len(np_)}) – pořadí, které měla v žebříčku:")
            L += _radky_text(np_rows)
        if za:
            L.append(f"🟢 Znovu aktivní ({len(za)}):")
            L += [f"   ~ {v}" for v in za]
    L += ["", f"Aktivních: {akt} | prodaných: {pro}", "", f"Žebříček ({akt} aktivních):"]
    top = df[df["stav"] == "aktivní"]
    L += _radky_text(r for _, r in top.iterrows())
    text = "\n".join(L)

    # --- html ---
    def chips(items, color):
        return "".join(f"<li style='color:{color}'>{v}</li>" for v in items)
    zmeny_html = ""
    if zmen == 0:
        zmeny_html = "<p>Žádné změny – stav i nabídky jako včera.</p>"
    else:
        if pr:
            zmeny_html += f"<p><b>🆕 Přidáno ({len(pr)})</b><ul>{chips(pr, '#137333')}</ul></p>"
        if np_:
            zmeny_html += (f"<p><b>🔴 Nově prodáno ({len(np_)})</b> "
                           "<span style='color:#888'>(# = pořadí, které měla v žebříčku)</span></p>"
                           + _tabulka("".join(_radek_html(r) for r in np_rows)))
        if za:
            zmeny_html += f"<p><b>🟢 Znovu aktivní ({len(za)})</b><ul>{chips(za, '#1a73e8')}</ul></p>"

    rows = "".join(_radek_html(r) for _, r in top.iterrows())
    html = f"""<html><body style="font-family:Arial,sans-serif;font-size:14px">
<h2>🚗 Žebříček aut – {datum}</h2>
{zmeny_html}
<p>Aktivních: <b>{akt}</b> &nbsp;|&nbsp; prodaných: {pro}</p>
<h3>Žebříček ({akt} aktivních)</h3>
{_tabulka(rows)}
<p style="color:#888;font-size:12px">Automatický souhrn z ~/www/auta/aktualizace.py</p>
</body></html>"""
    return subject, text, html


def send(subject, text, html):
    """Odešle e-mail dle config.EMAIL. Vrací True/False."""
    cfg = C.EMAIL
    if not cfg.get("enabled"):
        return False
    if not cfg["user"] or not cfg["password"]:
        print("  ! e-mail: chybí SMTP user/password (config nebo AUTA_SMTP_USER/PASS)")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to_addrs"])
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        if cfg.get("use_ssl"):
            with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"],
                                  context=ssl.create_default_context(), timeout=30) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
                if cfg.get("use_tls"):
                    s.starttls(context=ssl.create_default_context())
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        print(f"  📧 e-mail odeslán na {', '.join(cfg['to_addrs'])}")
        return True
    except Exception as e:
        print(f"  ! e-mail se nepodařilo odeslat: {e}")
        return False
