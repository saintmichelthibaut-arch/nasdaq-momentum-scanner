"""Fondamentaux, catalyseurs et positionnement (Yahoo Finance via yfinance).

Une fois par jour, pour chaque action US :
  - fondamentaux : capitalisation, croissance du CA et des bénéfices, marges,
    trésorerie, dette, flux de trésorerie libre, valorisation
  - analystes : recommandation moyenne, objectif de cours moyen, et l'historique
    des relèvements / abaissements (avec les objectifs de cours relevés)
  - résultats : dernières publications (date, BPA attendu, BPA publié, surprise)
    et prochaine date de publication
  - positionnement : vendeurs à découvert (short interest, jours pour couvrir)
  - actualités : titres des dernières dépêches (affichés tels quels, non interprétés)

Tout est conservé dans data/fundamentals.json avec la date de relevé de chaque
titre. Si Yahoo ne répond pas pour un titre, on garde le dernier relevé réussi
(avec sa date) ; s'il n'y en a jamais eu, les champs restent vides (N/A) et la
raison est notée. Rien n'est jamais estimé ni complété.

  python scripts/fundamentals.py           relevé (titres de plus de 18 h seulement)
  python scripts/fundamentals.py --force   relevé complet
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from universe import UNIVERSE, market_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "fundamentals.json"
MAX_AGE_H = 18          # un relevé plus récent que ça n'est pas refait
TIME_BUDGET_S = 420     # au-delà, on s'arrête : les titres restants gardent leur relevé précédent
WORKERS = 4
MODULES = ["price", "summaryDetail", "defaultKeyStatistics", "financialData",
           "calendarEvents", "earningsHistory", "upgradeDowngradeHistory"]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def _f(v):
    """Valeur numérique brute de Yahoo, ou None."""
    if isinstance(v, dict):
        v = v.get("raw")
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _date(v) -> str | None:
    f = _f(v)
    if f is None or f <= 0:
        return None
    return datetime.fromtimestamp(f, tz=timezone.utc).strftime("%Y-%m-%d")


def _summary(tk) -> dict:
    res = tk._quote._fetch(MODULES)  # un seul appel « quoteSummary » pour tous les modules
    r = (((res or {}).get("quoteSummary") or {}).get("result") or [None])[0]
    if not r:
        raise RuntimeError("réponse vide de Yahoo")
    pr, sd, ks, fd = (r.get(k) or {} for k in ("price", "summaryDetail", "defaultKeyStatistics", "financialData"))
    ce = (r.get("calendarEvents") or {}).get("earnings") or {}
    out = {
        "market_cap": _f(pr.get("marketCap")) or _f(sd.get("marketCap")),
        "revenue_growth": _f(fd.get("revenueGrowth")),
        "earnings_growth": _f(fd.get("earningsGrowth")),
        "gross_margin": _f(fd.get("grossMargins")),
        "operating_margin": _f(fd.get("operatingMargins")),
        "profit_margin": _f(fd.get("profitMargins")),
        "total_cash": _f(fd.get("totalCash")),
        "total_debt": _f(fd.get("totalDebt")),
        "debt_to_equity": _f(fd.get("debtToEquity")),
        "free_cashflow": _f(fd.get("freeCashflow")),
        "total_revenue": _f(fd.get("totalRevenue")),
        "recommendation_mean": _f(fd.get("recommendationMean")),
        "recommendation_key": fd.get("recommendationKey") if isinstance(fd.get("recommendationKey"), str) else None,
        "analyst_count": _f(fd.get("numberOfAnalystOpinions")),
        "target_mean": _f(fd.get("targetMeanPrice")),
        "target_high": _f(fd.get("targetHighPrice")),
        "trailing_pe": _f(sd.get("trailingPE")),
        "forward_pe": _f(sd.get("forwardPE")) or _f(ks.get("forwardPE")),
        "price_to_sales": _f(sd.get("priceToSalesTrailing12Months")),
        "short_pct_float": _f(ks.get("shortPercentOfFloat")),
        "short_ratio": _f(ks.get("shortRatio")),
        "shares_short": _f(ks.get("sharesShort")),
        "shares_short_prior": _f(ks.get("sharesShortPriorMonth")),
        "short_date": _date(ks.get("dateShortInterest")),
        "next_earnings": sorted(d for d in (_date(x) for x in (ce.get("earningsDate") or [])) if d)[:1] or None,
    }
    if out["next_earnings"]:
        out["next_earnings"] = out["next_earnings"][0]
    hist = []
    for h in ((r.get("earningsHistory") or {}).get("history") or []):
        hist.append({"quarter": _date(h.get("quarter")), "eps_actual": _f(h.get("epsActual")),
                     "eps_estimate": _f(h.get("epsEstimate")), "surprise": _f(h.get("surprisePercent"))})
    out["eps_history"] = [h for h in hist if h["quarter"]][-4:]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
    acts = []
    for a in ((r.get("upgradeDowngradeHistory") or {}).get("history") or []):
        ts = _f(a.get("epochGradeDate"))
        if ts is None or ts < cutoff:
            continue
        acts.append({"date": _date(ts), "firm": a.get("firm"), "action": a.get("action"),
                     "from": a.get("fromGrade") or None, "to": a.get("toGrade") or None,
                     "pt_action": a.get("priceTargetAction") or None,
                     "pt": _f(a.get("currentPriceTarget")), "pt_prior": _f(a.get("priorPriceTarget"))})
    acts.sort(key=lambda a: a["date"] or "", reverse=True)
    out["analyst_actions"] = acts[:15]
    return out


def _earnings_dates(tk) -> list[dict]:
    """Dates réelles de publication des résultats (page calendrier de Yahoo)."""
    df = tk.get_earnings_dates(limit=8)
    out = []
    if df is None or df.empty:
        return out
    for idx, row in df.iterrows():
        rep = _f(row.get("Reported EPS"))
        if rep is None:
            continue  # publication à venir
        out.append({"date": f"{idx:%Y-%m-%d}", "eps_estimate": _f(row.get("EPS Estimate")),
                    "eps_actual": rep, "surprise": _f(row.get("Surprise(%)"))})
    out.sort(key=lambda e: e["date"], reverse=True)
    return out[:4]


def _news(tk) -> list[dict]:
    out = []
    for n in tk.get_news(count=8) or []:
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = c.get("title")
        if not title:
            continue
        when = c.get("pubDate") or c.get("displayTime")
        if not when and _f(c.get("providerPublishTime")):
            when = datetime.fromtimestamp(_f(c["providerPublishTime"]), tz=timezone.utc).isoformat()
        url = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) \
            or ((c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"), dict) else None) \
            or c.get("link")
        prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")
        out.append({"title": str(title)[:220], "date": str(when)[:25] if when else None,
                    "source": prov, "url": url if isinstance(url, str) and url.startswith("http") else None})
    return out[:8]


def fetch_one(t: str) -> dict:
    import yfinance as yf
    tk = yf.Ticker(t)
    rec: dict = {"errors": {}}
    try:
        rec.update(_summary(tk))
    except Exception as e:
        rec["errors"]["summary"] = f"{type(e).__name__}: {e}"[:200]
    try:
        rec["earnings"] = _earnings_dates(tk)
    except Exception as e:
        rec["earnings"] = None
        rec["errors"]["earnings"] = f"{type(e).__name__}: {e}"[:200]
    try:
        rec["news"] = _news(tk)
    except Exception as e:
        rec["news"] = None
        rec["errors"]["news"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at_utc": None, "stocks": {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db = load()
    stocks = db.setdefault("stocks", {})
    for t in list(stocks):
        if t not in UNIVERSE:
            stocks.pop(t)

    def stale(t):
        f = (stocks.get(t) or {}).get("fetched_at_utc")
        return args.force or not f or (now - datetime.fromisoformat(f)).total_seconds() > MAX_AGE_H * 3600
    todo = [t for t in UNIVERSE if market_of(t) == "US" and stale(t)]
    log(f"fondamentaux : {len(todo)} titre(s) à relever")
    start, ok, ko = time.time(), 0, 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {}
        for t in todo:
            futs[ex.submit(fetch_one, t)] = t
        for fu in as_completed(futs):
            t = futs[fu]
            try:
                rec = fu.result()
            except Exception as e:
                rec = {"errors": {"summary": f"{type(e).__name__}: {e}"[:200]}}
            prev = stocks.get(t) or {}
            if "market_cap" in rec or "revenue_growth" in rec:  # le relevé principal a réussi
                # une partie annexe ratée (résultats, actualités) garde son relevé précédent
                for k in ("earnings", "news"):
                    if rec.get(k) is None and prev.get(k) is not None:
                        rec[k] = prev[k]
                rec["fetched_at_utc"] = now.isoformat()
                stocks[t] = rec
                ok += 1
            else:
                prev = dict(prev)
                prev["last_error"] = rec["errors"].get("summary")
                prev["last_error_utc"] = now.isoformat()
                stocks[t] = prev
                ko += 1
            if time.time() - start > TIME_BUDGET_S:
                log("temps imparti écoulé : les titres restants gardent leur relevé précédent")
                for f2 in futs:
                    f2.cancel()
                break
    db["generated_at_utc"] = now.isoformat()
    db["source"] = "Yahoo Finance (quoteSummary, calendrier des résultats, actualités) via yfinance"
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(db, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    log(f"fondamentaux : {ok} relevés réussis, {ko} échecs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
