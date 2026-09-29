"""Envoi des alertes par e-mail (Gmail ou tout serveur SMTP).

Les identifiants ne sont JAMAIS dans le code : ils viennent des secrets GitHub
(Settings → Secrets and variables → Actions) :
  ALERT_EMAIL_USER      adresse Gmail qui envoie
  ALERT_EMAIL_PASSWORD  mot de passe d'application Gmail (16 lettres)
  ALERT_EMAIL_TO        destinataire(s), séparés par des virgules
  ALERT_SMTP_HOST       optionnel (défaut smtp.gmail.com)
Si ces secrets manquent, rien n'est envoyé et le script le dit simplement.

  python scripts/notify.py daily     résumé des alertes de la collecte du soir
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def page_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


def configured() -> bool:
    return all(os.environ.get(k) for k in ("ALERT_EMAIL_USER", "ALERT_EMAIL_PASSWORD", "ALERT_EMAIL_TO"))


def send(subject: str, text: str, html_body: str) -> bool:
    if not configured():
        print("e-mail non configuré (secrets ALERT_EMAIL_* absents) : aucun envoi")
        return False
    user, pwd = os.environ["ALERT_EMAIL_USER"], os.environ["ALERT_EMAIL_PASSWORD"]
    to = [a.strip() for a in os.environ["ALERT_EMAIL_TO"].split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    host = os.environ.get("ALERT_SMTP_HOST") or "smtp.gmail.com"
    try:
        with smtplib.SMTP_SSL(host, 465, timeout=30) as s:
            s.login(user, pwd)
            s.sendmail(user, to, msg.as_string())
        print(f"e-mail envoyé à {len(to)} destinataire(s) : {subject}")
        return True
    except Exception as e:  # un échec d'envoi ne doit pas casser la collecte
        print(f"échec de l'envoi de l'e-mail : {type(e).__name__}: {e}")
        return False


def fmt_line(a: dict) -> str:
    """Format demandé : TICKER | $prix | jour +X% | RVOL X.X | score XX/100 | breakout oui/non"""
    cur = "€" if a.get("currency") == "EUR" else "$"
    f = lambda v, nd: "N/A" if v is None else f"{v:.{nd}f}"  # noqa: E731
    chg = "N/A" if a.get("chg_1d") is None else f"{a['chg_1d'] * 100:+.1f}%"
    parts = [a["ticker"], f"{cur}{f(a.get('price'), 2)}", f"jour {chg}", f"RVOL {f(a.get('rvol'), 1)}"]
    if "score" in a:
        parts.append(f"score {f(a.get('score'), 0)}/100")
    if "breakout" in a:
        parts.append(f"breakout {'oui' if a.get('breakout') else 'non'}")
    return " | ".join(parts)


def render(title: str, intro: str, alerts: list[dict]) -> tuple[str, str]:
    url = page_url()
    text = [title, intro, ""]
    rows = []
    for a in alerts:
        line, why = fmt_line(a), a.get("reason", "")
        text += [line, f"  → {why}", ""]
        rows.append(f"<tr><td style='padding:10px 0;border-bottom:1px solid #e5e9ef'>"
                    f"<div style='font-family:monospace;font-size:13px'><b>{html.escape(line)}</b></div>"
                    f"<div style='color:#4a5666;font-size:14px;margin-top:3px'>→ {html.escape(why)}</div></td></tr>")
    if url:
        text.append(f"Voir le scanner : {url}")
    text.append("Données Yahoo Finance. Un signal n'est pas une garantie de hausse.")
    body = (f"<div style='font-family:Arial,sans-serif;max-width:640px;color:#101720'>"
            f"<h2 style='margin:0 0 6px'>{html.escape(title)}</h2><p style='color:#4a5666;margin:0 0 12px'>{html.escape(intro)}</p>"
            f"<table style='width:100%;border-collapse:collapse'>{''.join(rows)}</table>"
            + (f"<p style='margin-top:16px'><a href='{url}' style='background:#2c6ad8;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none'>Ouvrir le scanner</a></p>" if url else "")
            + "<p style='color:#7f8a98;font-size:12px'>Données Yahoo Finance. Un signal n'est pas une garantie de hausse.</p></div>")
    return "\n".join(text), body


def daily() -> int:
    path = ROOT / "data" / "alerts.json"
    if not path.exists():
        print("pas de fichier d'alertes")
        return 0
    d = json.loads(path.read_text(encoding="utf-8"))
    alerts = d.get("alerts", [])
    if not alerts:
        print("aucune alerte ce soir : pas d'e-mail")
        return 0
    day = (d.get("market_date") or {}).get("US") or ""
    text, body = render(f"Momentum Scanner : {len(alerts)} alerte(s) du {day}",
                        "Résultat de l'analyse du soir, après la clôture de Wall Street.", alerts)
    send(f"📈 {len(alerts)} alerte(s) : " + ", ".join(a["ticker"] for a in alerts[:6]), text, body)
    return 0


if __name__ == "__main__":
    sys.exit(daily() if (sys.argv[1:] or ["daily"])[0] == "daily" else 0)
