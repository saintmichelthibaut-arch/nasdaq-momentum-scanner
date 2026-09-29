"""Suivi en séance (toutes les 5 minutes pendant les heures de bourse US).

Ne recalcule PAS les scores : en pleine séance le volume est partiel et
fausserait tout. Il ajoute une couche « en direct » à partir des vraies
cotations minute de Yahoo :
  - dernier prix et variation depuis la clôture précédente
  - volume échangé depuis l'ouverture et RVOL ESTIMÉ (projeté sur la séance)
  - franchissement en séance du plafond 60 jours ou du plus haut 1 an
et envoie un e-mail pour chaque nouveau signal (une seule fois par jour et par
titre et type de signal).

Écrit data/live.json (non committé : publié directement avec la page).
État anti-doublon : data/live_state.json (conservé dans le cache GitHub).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify  # noqa: E402
import push  # noqa: E402
from signals import num  # noqa: E402
from universe import BENCHMARK, UNIVERSE, market_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SESSIONS = {"US": ("America/New_York", dtime(9, 30), dtime(16, 0)),
            "EU": ("Europe/Paris", dtime(9, 0), dtime(17, 30))}
# seuils des alertes en séance
MIN_MINUTES = 30          # pas d'alerte de volume dans la première demi-heure (trop bruité)
RVOL_ALERT = 2.5          # volume estimé ≥ 2,5 fois la normale
BREAKOUT_RVOL = 1.3       # cassure en séance confirmée par un volume estimé ≥ 1,3
MOVE_ALERT = 0.06         # variation de ±6 % dans la séance


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def session_info(market: str, now: datetime) -> dict:
    tz, o, c = SESSIONS[market]
    local = now.astimezone(ZoneInfo(tz))
    start = local.replace(hour=o.hour, minute=o.minute, second=0, microsecond=0)
    end = local.replace(hour=c.hour, minute=c.minute, second=0, microsecond=0)
    total = (end - start).total_seconds() / 60
    elapsed = min(max((local - start).total_seconds() / 60, 0), total)
    return {"date": local.date(), "open": local.weekday() < 5 and start <= local <= end,
            "elapsed": elapsed, "total": total}


def main() -> int:
    import yfinance as yf

    now = datetime.now(timezone.utc).replace(microsecond=0)
    us = session_info("US", now)
    if not us["open"]:
        log("bourse US fermée : rien à faire")
        return 0
    latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    stocks = {s["ticker"]: s for s in latest["stocks"]}
    tickers = list(UNIVERSE) + [BENCHMARK]
    raw = yf.download(tickers, period="1d", interval="1m", group_by="ticker", auto_adjust=False,
                      prepost=False, threads=True, progress=False)
    if raw is None or raw.empty:
        log("aucune cotation reçue (jour férié ou Yahoo indisponible)")
        return 0

    out, alerts = {}, []
    for t in tickers:
        try:
            sub = raw[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if sub.empty:
            continue
        mk = market_of(t) if t in UNIVERSE else "US"
        info = session_info(mk, now)
        idx = sub.index.tz_convert(SESSIONS[mk][0]) if sub.index.tz is not None else sub.index
        if idx[-1].date() != info["date"]:
            continue  # pas encore de séance aujourd'hui sur ce marché
        last_time = idx[-1]
        price = float(sub["Close"].iloc[-1])
        vol_so_far = float(sub["Volume"].fillna(0).sum())
        st = stocks.get(t)
        if t == BENCHMARK:
            prev = (latest["meta"]["benchmarks"].get(BENCHMARK) or {}).get("close")
            prev_date = (latest["meta"]["benchmarks"].get(BENCHMARK) or {}).get("date")
        else:
            prev = st["price"]["close"] if st else None
            prev_date = st["last_bar_date"] if st else None
        if prev is None or prev_date == str(info["date"]):
            continue  # la clôture de référence doit être celle de la veille
        chg = price / prev - 1
        frac = max(info["elapsed"] / info["total"], 0.08)
        avg20 = st["volume"]["avg20"] if st else None
        rvol_proj = (vol_so_far / frac) / avg20 if avg20 else None
        rec = {"price": num(price), "chg": num(chg), "time": last_time.strftime("%H:%M"),
               "market_open": info["open"], "volume": num(vol_so_far, 0), "rvol_est": num(rvol_proj, 2)}
        if st:
            res = st["setup"]["resistance"]
            hi52 = st["price"]["high_52w"]
            rec["above_resistance"] = bool(res and price > res)
            rec["above_52w"] = bool(hi52 and price > hi52)
            rec["dist_resistance"] = num(res / price - 1) if res else None
            cur = "€" if st["currency"] == "EUR" else "$"
            base = {"ticker": t, "name": st["name"], "currency": st["currency"], "price": num(price),
                    "chg_1d": num(chg), "rvol": num(rvol_proj, 1), "time": rec["time"],
                    "score": st["score"]["profiles"]["swing"]["total"]}
            enough = info["elapsed"] >= MIN_MINUTES
            if enough and rvol_proj and rvol_proj >= RVOL_ALERT:
                alerts.append({**base, "kind": "volume", "reason": f"volume estimé {notify_fr(rvol_proj, 1)} fois la normale sur la séance ({notify_fr(vol_so_far / 1e6, 1)} M déjà échangés à {rec['time']})"})
            if rec["above_52w"] and (not enough or (rvol_proj or 0) >= BREAKOUT_RVOL):
                alerts.append({**base, "kind": "high52", "breakout": True, "reason": f"passe au-dessus de son plus haut d'un an ({notify_fr(hi52, 2)} {cur}) en séance"})
            elif rec["above_resistance"] and enough and (rvol_proj or 0) >= BREAKOUT_RVOL:
                alerts.append({**base, "kind": "resistance", "breakout": True, "reason": f"franchit son plafond des 3 derniers mois ({notify_fr(res, 2)} {cur}) avec un volume estimé {notify_fr(rvol_proj, 1)} fois la normale"
                               + (" ; elle était classée « prête à décoller »" if st["setup"]["before_breakout"] else "")})
            if abs(chg) >= MOVE_ALERT:
                alerts.append({**base, "kind": "move_up" if chg > 0 else "move_down", "reason": f"{'hausse' if chg > 0 else 'baisse'} de {notify_fr(abs(chg) * 100, 1)} % depuis la clôture d'hier"})
        out[t] = rec

    # anti-doublon : un même signal n'est envoyé qu'une fois par jour
    state_path = DATA / "live_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    today = str(us["date"])
    sent = set(state.get(today, []))
    new = [a for a in alerts if f"{a['ticker']}:{a['kind']}" not in sent]
    if new:
        text, body = notify.render(f"En séance : {len(new)} nouveau(x) signal(aux)",
                                   f"Relevé de {now.astimezone(ZoneInfo('Europe/Paris')):%H:%M} (heure de Paris). Signaux en direct, à confirmer à la clôture.", new)
        mailed = notify.send("⚡ En séance : " + ", ".join(sorted({a["ticker"] for a in new})), text, body)
        ptitle, pbody = push.alerts_message(new, live=True)
        pushed = push.send(ptitle, pbody, tag="seance")
        if mailed or pushed or not (notify.configured() or push.configured()):
            sent |= {f"{a['ticker']}:{a['kind']}" for a in new}
    state = {today: sorted(sent)}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    live = {"generated_at_utc": now.isoformat(), "session_date": today,
            "source": "Yahoo Finance, cotations minute (peuvent avoir quelques minutes de retard)",
            "note": "Prix et volume en séance. Les scores restent ceux de la dernière clôture.",
            "us_minutes_elapsed": round(us["elapsed"]), "stocks": out,
            "alerts": sorted(alerts, key=lambda a: a["ticker"])}
    (DATA / "live.json").write_text(json.dumps(live, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    log(f"live : {len(out)} cotations, {len(alerts)} signaux dont {len(new)} nouveaux")
    return 0


def notify_fr(v, nd):
    return f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")


if __name__ == "__main__":
    sys.exit(main())
