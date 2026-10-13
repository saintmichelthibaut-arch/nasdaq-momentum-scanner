"""Stratégies A « Avant explosion » et B « Rebond après correction ».

Tout est calculé à partir des cours réels (yfinance) et, quand Yahoo les fournit,
des fondamentaux / catalyseurs relevés par fundamentals.py. Une information
absente n'est jamais devinée : le critère concerné rapporte 0 point et il est
marqué « N/A » avec la raison.

Les calculs de prix sont « point-in-time » : à la séance i, seules les séances
<= i sont utilisées (le score B de chaque séance passée est donc reproductible).

Note : le « plus haut historique » (ATH) est le plus haut de l'historique
téléchargé (5 ans). Pour une action dont le record date de plus de 5 ans, c'est
donc le plus haut sur 5 ans ; la fiche l'indique.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

# ── seuils ──────────────────────────────────────────────────────────────────
MIN_CORRECTION = -0.20        # stratégie B : correction d'au moins 20 % depuis l'ATH
RECOVERED = -0.08             # à moins de 8 % de l'ATH : la correction est effacée
MIN_PRICE = 5.0               # pas de penny stocks
MIN_DOLLAR_VOL = 20e6         # liquidité : 20 M$ échangés par jour en moyenne (20 séances)
MIN_MCAP = 1e9                # capitalisation > 1 Md$

PHASES = {
    "reversal": "🟢 Retournement",
    "stabilizing": "🟡 Stabilisation",
    "knife": "🔴 Couteau qui tombe",
    "recovered": "Déjà revenue près de son plus haut",
    "none": "Pas de correction de 20 % ou plus",
}

B_MAX = {"correction": 15, "stabilisation": 15, "sequence": 20, "volume": 15,
         "momentum": 15, "breakout": 10, "fundamentals": 10}
B_LABELS = {"correction": "Correction depuis le plus haut", "stabilisation": "Stabilisation (fin de la baisse)",
            "sequence": "Séquence de hausse", "volume": "Volume acheteur", "momentum": "Momentum technique",
            "breakout": "Cassure de résistance", "fundamentals": "Fondamentaux / catalyseur"}
A_MAX = {"catalyst": 25, "volume": 15, "relative_strength": 15, "technical": 15,
         "positioning": 10, "rotation": 10, "quality": 10}
A_LABELS = {"catalyst": "Catalyseur", "volume": "Volume", "relative_strength": "Force relative",
            "technical": "Technique", "positioning": "Positionnement (vendeurs à découvert)",
            "rotation": "Rotation sectorielle", "quality": "Qualité / risque"}


def _n(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) or np.isinf(f) else f


def _r(v, nd=4):
    f = _n(v)
    return None if f is None else round(f, nd)


def fmt_pct(v, nd=0):
    return f"{v * 100:+.{nd}f} %".replace(".", ",").replace("+-", "-")


# ── stratégie B : mesure point-in-time d'une séance ─────────────────────────

def rebound_at(x: pd.DataFrame, i: int) -> dict | None:
    """Toutes les mesures « rebond » à la séance i (aucune donnée postérieure)."""
    if i < 30:
        return None
    h = x["high"].to_numpy(float)[: i + 1]
    lo = x["low"].to_numpy(float)[: i + 1]
    c = x["close"].to_numpy(float)[: i + 1]
    o = x["open"].to_numpy(float)[: i + 1]
    v = x["volume"].to_numpy(float)[: i + 1]
    col = lambda k: x[k].to_numpy(float)[: i + 1]  # noqa: E731
    rsi, mh, macd, msig = col("rsi14"), col("macd_hist"), col("macd"), col("macd_signal")
    s20, s50, s200, atrp, rvol = col("sma20"), col("sma50"), col("sma200"), col("atr_pct"), col("rvol")
    avg20 = pd.Series(v).rolling(20, min_periods=20).mean().shift(1).to_numpy()

    peak = np.nanmax(h)
    peak_i = int(np.flatnonzero(h == peak)[-1])
    seg = lo[peak_i:]
    trough = float(np.nanmin(seg))
    trough_i = peak_i + int(np.flatnonzero(seg == trough)[-1])
    since_trough, since_peak = i - trough_i, i - peak_i
    max_dd = trough / peak - 1
    dd_now = c[i] / peak - 1

    up = np.r_[False, c[1:] > c[:-1]]
    streak = 0
    k = i
    while k > trough_i and up[k]:
        streak += 1
        k -= 1
    last5 = slice(max(trough_i + 1, i - 4), i + 1)  # les séances après le creux seulement
    ups5 = int(up[last5].sum())
    rng = np.where(h - lo > 0, h - lo, np.nan)
    clv3 = np.nanmean(((c - lo) / rng)[max(0, i - 2): i + 1])
    upvol5 = int(sum(1 for k in range(last5.start, i + 1) if up[k] and avg20[k] > 0 and v[k] > avg20[k]))
    w10 = slice(max(0, i - 9), i + 1)
    upv, dnv = v[w10][up[w10]].sum(), v[w10][~up[w10]].sum()
    updown10 = upv / dnv if dnv > 0 else (None if upv == 0 else 9.9)

    # fin de la baisse
    no_new_low = since_trough >= 5
    higher_low = bool(since_trough >= 6 and np.nanmin(lo[trough_i + 3: i + 1]) > trough * 1.015)
    double_bottom = False
    for j in range(peak_i, i + 1):
        if abs(j - trough_i) >= 5 and lo[j] <= trough * 1.03:
            a, b = sorted((j, trough_i))
            if np.nanmax(h[a: b + 1]) >= trough * 1.05:
                double_bottom = True
                break
    vol_contract = bool(_n(atrp[trough_i]) and _n(atrp[i]) and atrp[i] < 0.85 * atrp[trough_i])
    dn_now = v[w10][~up[w10]]
    w_t = slice(max(0, trough_i - 9), trough_i + 1)
    dn_then = v[w_t][~up[w_t]]
    seller_dry = bool(len(dn_then) and (len(dn_now) == 0 or dn_now.mean() < 0.8 * dn_then.mean()))

    # reprise du momentum
    rsi_up = bool(i >= 5 and _n(rsi[i]) is not None and _n(rsi[i - 5]) is not None and rsi[i] > rsi[i - 5] + 3)
    macd_pos = bool(_n(mh[i]) is not None and mh[i] > 0)
    macd_turn = bool(i >= 2 and all(_n(mh[k]) is not None for k in (i - 2, i - 1, i)) and mh[i] > mh[i - 1] > mh[i - 2])
    cross = (macd > msig) & np.r_[False, macd[:-1] <= msig[:-1]]
    macd_cross = bool(cross[max(0, i - 9): i + 1].any())
    a20 = bool(_n(s20[i]) and c[i] > s20[i])
    a50 = bool(_n(s50[i]) and c[i] > s50[i])
    a200 = bool(_n(s200[i]) and c[i] > s200[i])
    s20_up = bool(i >= 5 and _n(s20[i]) and _n(s20[i - 5]) and s20[i] > s20[i - 5])
    reclaim20 = a20 and bool((c[max(0, i - 15): i] < s20[max(0, i - 15): i]).any())
    reclaim50 = a50 and bool((c[max(0, i - 30): i] < s50[max(0, i - 30): i]).any())

    # confirmation : franchissement des plus hauts récents (aujourd'hui ou ces 3 dernières séances)
    def brk(n):
        hit, vol_ok = False, False
        for k in range(max(n, i - 2), i + 1):
            lvl = np.nanmax(h[k - n: k])
            if c[k] > lvl and c[i] >= lvl * 0.99:
                hit = True
                vol_ok = vol_ok or bool(_n(rvol[k]) and rvol[k] >= 1.3)
        return hit, vol_ok
    b5, b5v = brk(5)
    b10, b10v = brk(10)
    b20, b20v = brk(20)
    hi20 = float(np.nanmax(h[i - 19: i + 1])) if i >= 19 else None

    return {
        "i": i, "peak": peak, "peak_i": peak_i, "trough": trough, "trough_i": trough_i,
        "since_peak": since_peak, "since_trough": since_trough, "corr_sessions": trough_i - peak_i,
        "max_dd": max_dd, "dd_now": dd_now, "close": c[i], "rebound": c[i] / trough - 1,
        "retrace": (c[i] - trough) / (peak - trough) if peak > trough else None,
        "streak": streak, "ups5": ups5, "clv3": clv3, "upvol5": upvol5, "updown10": updown10,
        "vol5_avg20": (v[max(0, i - 4): i + 1].mean() / avg20[i]) if avg20[i] > 0 else None,
        "no_new_low": no_new_low, "higher_low": higher_low, "double_bottom": double_bottom,
        "vol_contract": vol_contract, "seller_dry": seller_dry,
        "rsi": _n(rsi[i]), "rsi_up": rsi_up, "macd_pos": macd_pos, "macd_turn": macd_turn, "macd_cross": macd_cross,
        "above20": a20, "above50": a50, "above200": a200, "sma20_up": s20_up,
        "reclaim20": reclaim20, "reclaim50": reclaim50,
        "brk5": b5, "brk10": b10, "brk20": b20, "brk_vol": b5v or b10v or b20v,
        "hi20": hi20, "dist_hi20": (hi20 / c[i] - 1) if hi20 else None,
        "open_gap": o[i] / c[i - 1] - 1 if i >= 1 else None,
    }


def phase_of(m: dict) -> str:
    if m["max_dd"] > MIN_CORRECTION:
        return "none"
    if m["dd_now"] > RECOVERED:
        return "recovered"
    if m["since_trough"] <= 2 or (m["since_trough"] < 5 and not m["above20"]):
        return "knife"
    confirmed = (m["streak"] >= 3 or m["ups5"] >= 3) and m["above20"] and (m["brk5"] or m["brk10"] or m["brk20"]) \
        and (m["upvol5"] >= 1 or (m["vol5_avg20"] or 0) >= 1.0)
    if confirmed and m["since_trough"] >= 3:
        return "reversal"
    return "stabilizing"


def fund_flags(f: dict | None) -> dict:
    """Lecture des fondamentaux. None = information absente (jamais devinée)."""
    if not f or f.get("market_cap") is None and f.get("revenue_growth") is None:
        return {"available": False}
    rg, eg = f.get("revenue_growth"), f.get("earnings_growth")
    cash, debt, de = f.get("total_cash"), f.get("total_debt"), f.get("debt_to_equity")
    deteriorating = None
    if rg is not None:
        deteriorating = rg < 0 or (eg is not None and eg < -0.30)
    balance_ok = None
    if cash is not None and debt is not None:
        balance_ok = cash >= 0.5 * debt
    elif de is not None:
        balance_ok = de < 150
    return {"available": True, "rev_growth": rg, "earn_growth": eg, "deteriorating": deteriorating,
            "balance_ok": balance_ok, "rec_mean": f.get("recommendation_mean"),
            "target": f.get("target_mean"), "fcf": f.get("free_cashflow"), "op_margin": f.get("operating_margin")}


def recent_analyst(f: dict | None, asof: date, days: int = 30) -> dict:
    up, init_buy, pt_up, pt_down, down = [], [], [], [], []
    for a in (f or {}).get("analyst_actions") or []:
        try:
            d = date.fromisoformat(a["date"])
        except Exception:
            continue
        if (asof - d).days > days or d > asof:
            continue
        act = (a.get("action") or "").lower()
        to = (a.get("to") or "").lower()
        if act == "up":
            up.append(a)
        elif act == "down":
            down.append(a)
        elif act == "init" and any(w in to for w in ("buy", "outperform", "overweight", "positive")):
            init_buy.append(a)
        pa = (a.get("pt_action") or "").lower()
        if pa.startswith("raise") or (a.get("pt") and a.get("pt_prior") and a["pt"] > a["pt_prior"]):
            pt_up.append(a)
        elif pa.startswith("lower") or (a.get("pt") and a.get("pt_prior") and a["pt"] < a["pt_prior"]):
            pt_down.append(a)
    return {"upgrades": up, "init_buy": init_buy, "pt_up": pt_up, "pt_down": pt_down, "downgrades": down}


def last_earnings(f: dict | None) -> dict | None:
    e = (f or {}).get("earnings") or []
    return e[0] if e else None


def rebound_score(m: dict, f: dict | None, asof: date) -> dict:
    ff = fund_flags(f)
    pts, notes = {}, {}
    dd = m["max_dd"]
    a = 15 if dd <= -0.40 else 12 if dd <= -0.30 else 8 if dd <= -0.25 else 5 if dd <= -0.20 else 0
    if dd <= -0.40 and ff.get("deteriorating"):
        a = 8
        notes["correction"] = "plafonné : correction > 40 % avec des fondamentaux en dégradation"
    pts["correction"] = a
    pts["stabilisation"] = (5 if m["no_new_low"] else 2 if m["since_trough"] >= 3 else 0) \
        + (4 if (m["higher_low"] or m["double_bottom"]) else 0) + (3 if m["vol_contract"] else 0) + (3 if m["seller_dry"] else 0)
    st, u5 = m["streak"], m["ups5"]
    pts["sequence"] = max({0: 0, 1: 3, 2: 8, 3: 13, 4: 17}.get(st, 20), {0: 0, 1: 0, 2: 5, 3: 10, 4: 15}.get(u5, 20))
    ud = m["updown10"]
    pts["volume"] = min(15, {0: 0, 1: 4, 2: 8}.get(m["upvol5"], 11) + (4 if ud is not None and ud >= 1.3 else 2 if ud is not None and ud >= 1.0 else 0))
    rsi = m["rsi"] or 0
    pts["momentum"] = (4 if m["rsi_up"] and rsi >= 50 else 2 if m["rsi_up"] and rsi >= 40 else 0) \
        + (3 if m["macd_pos"] else 2 if m["macd_turn"] else 0) + (1 if m["macd_cross"] else 0) \
        + (4 if m["above20"] else 0) + (2 if m["above50"] else 0) + (1 if m["sma20_up"] else 0)
    pts["breakout"] = min(10, (3 if m["brk5"] else 0) + (2 if m["brk10"] else 0) + (3 if m["brk20"] else 0)
                          + (2 if m["brk_vol"] and (m["brk5"] or m["brk10"] or m["brk20"]) else 0))
    if ff["available"]:
        g = 0
        rg = ff["rev_growth"]
        g += 3 if rg is not None and rg > 0.15 else 2 if rg is not None and rg > 0 else 0
        le = last_earnings(f)
        if le and le.get("surprise") is not None and le["surprise"] > 0:
            g += 2
        rm = ff["rec_mean"]
        g += 2 if rm is not None and rm <= 2.2 else 1 if rm is not None and rm <= 2.7 else 0
        if ff["target"] and m["close"] and ff["target"] > m["close"] * 1.10:
            g += 1
        ra = recent_analyst(f, asof)
        if ra["upgrades"] or ra["pt_up"] or ra["init_buy"]:
            g += 2
        pts["fundamentals"] = min(10, g)
    else:
        pts["fundamentals"] = 0
        notes["fundamentals"] = "N/A : fondamentaux non disponibles chez Yahoo pour ce titre"
    phase = phase_of(m)
    total = float(sum(pts.values()))
    if phase == "knife":
        total = min(total, 25.0)
        notes["total"] = "couteau qui tombe : pas de signal (score plafonné à 25)"
    if phase in ("none", "recovered"):
        total = None
    return {"phase": phase, "phase_label": PHASES[phase], "total": total, "points": pts, "notes": notes}


def crdo_checks(m: dict, f: dict | None, sector_strong: bool | None) -> list[dict]:
    ff = fund_flags(f)
    det = ff.get("deteriorating") if ff["available"] else None
    rows = [
        ("recent_ath", "Plus haut récent (moins de 6 mois)", m["peak_i"] >= m["i"] - 126),
        ("fast_drop", "Forte correction en 3 à 8 semaines", 10 <= m["corr_sessions"] <= 45),
        ("dd25", "Correction d'au moins 25 %", m["max_dd"] <= -0.25),
        ("stabilisation", "Phase de stabilisation (variations qui se calment)", m["since_trough"] >= 5 and (m["vol_contract"] or m["seller_dry"])),
        ("no_new_low", "Plus de nouveaux plus-bas (5 séances ou plus)", m["no_new_low"]),
        ("higher_low", "Creux plus haut que le précédent (ou double creux)", m["higher_low"] or m["double_bottom"]),
        ("up_days", "Au moins 2 à 3 séances de hausse", m["streak"] >= 2 or m["ups5"] >= 3),
        ("volume", "Volume supérieur à la moyenne", m["upvol5"] >= 2 or (m["vol5_avg20"] or 0) >= 1.1),
        ("sma20", "Repasse au-dessus de sa moyenne 20 jours", m["above20"]),
        ("near_res", "Résistance court terme proche (≤ 5 %) ou cassée", m["brk10"] or (m["dist_hi20"] is not None and m["dist_hi20"] <= 0.05)),
        ("sector", "Secteur toujours fort (mieux que le Nasdaq sur 6 mois)", sector_strong),
        ("fundamentals", "Pas de dégradation fondamentale majeure", None if det is None else not det),
    ]
    return [{"key": k, "label": l, "ok": (None if v is None else bool(v))} for k, l, v in rows]


def quality_dip(m: dict, phase: str, f: dict | None) -> dict:
    ff = fund_flags(f)
    if not ff["available"]:
        return {"flag": None, "checks": [], "why": "N/A : fondamentaux non disponibles"}
    rg = ff["rev_growth"]
    checks = [
        ("Correction d'au moins 20 % depuis le plus haut", m["max_dd"] <= -0.20),
        ("Croissance du chiffre d'affaires ≥ 10 % sur un an", None if rg is None else rg >= 0.10),
        ("Pas de dégradation fondamentale majeure", None if ff["deteriorating"] is None else not ff["deteriorating"]),
        ("Bilan solide (trésorerie vs dette)", ff["balance_ok"]),
        ("Analystes toujours positifs (recommandation ≤ 2,5 sur 5)", None if ff["rec_mean"] is None else ff["rec_mean"] <= 2.5),
        ("Ce n'est pas un couteau qui tombe", phase in ("stabilizing", "reversal")),
        ("Commence à rebondir (2 séances de hausse ou plus, ou au-dessus de la moyenne 20 j)", m["streak"] >= 2 or m["ups5"] >= 3 or m["above20"]),
    ]
    rows = [{"label": l, "ok": (None if v is None else bool(v))} for l, v in checks]
    flag = all(r["ok"] is True for r in rows)
    return {"flag": flag, "checks": rows}


def strategy_b(x: pd.DataFrame, f: dict | None, sector_strong: bool | None, history: int = 20) -> dict | None:
    n = len(x)
    m = rebound_at(x, n - 1)
    if m is None:
        return None
    asof = x.index[-1].date()
    sc = rebound_score(m, f, asof)
    hist, hist_dates = [], []
    for i in range(max(30, n - history), n):
        mi = m if i == n - 1 else rebound_at(x, i)
        if mi is None:
            continue
        s = sc if i == n - 1 else rebound_score(mi, f, x.index[i].date())
        hist.append(s["total"])
        hist_dates.append(f"{x.index[i]:%Y-%m-%d}")
    crdo = crdo_checks(m, f, sector_strong)
    nok = sum(1 for r in crdo if r["ok"])
    crdo_flag = bool(crdo[0]["ok"] and crdo[1]["ok"] and crdo[2]["ok"] and nok >= 9 and sc["phase"] in ("stabilizing", "reversal"))
    qd = quality_dip(m, sc["phase"], f)
    idx = x.index
    return {
        "ath": _r(m["peak"]), "ath_date": f"{idx[m['peak_i']]:%Y-%m-%d}", "ath_window": f"{idx[0]:%Y-%m-%d}",
        "low": _r(m["trough"]), "low_date": f"{idx[m['trough_i']]:%Y-%m-%d}",
        "close": _r(m["close"]), "drawdown": _r(m["dd_now"]), "max_drawdown": _r(m["max_dd"]),
        "rebound_from_low": _r(m["rebound"]), "retracement": _r(m["retrace"], 3),
        "correction_sessions": m["corr_sessions"], "sessions_since_low": m["since_trough"],
        "sessions_since_ath": m["since_peak"],
        "up_streak": m["streak"], "up_sessions5": m["ups5"], "upvol_sessions5": m["upvol5"],
        "signals": {
            "end_of_decline": {"no_new_low": m["no_new_low"], "higher_low": m["higher_low"], "double_bottom": m["double_bottom"],
                               "volatility_down": m["vol_contract"], "seller_volume_down": m["seller_dry"]},
            "momentum": {"up_streak2": m["streak"] >= 2, "closes_near_high": bool(m["clv3"] is not None and m["clv3"] >= 0.6),
                         "volume_rising": m["upvol5"] >= 1 or (m["vol5_avg20"] or 0) >= 1.0, "rsi_rising": m["rsi_up"],
                         "macd_turning": m["macd_turn"] or m["macd_pos"], "macd_cross": m["macd_cross"], "above_sma20": m["above20"]},
            "confirmation": {"break_5d_high": m["brk5"], "break_10d_high": m["brk10"], "break_20d_high": m["brk20"],
                             "reclaim_sma20": m["reclaim20"], "reclaim_sma50": m["reclaim50"], "breakout_with_volume": m["brk_vol"]},
        },
        "phase": sc["phase"], "phase_label": sc["phase_label"],
        "score": sc["total"], "points": sc["points"], "max": B_MAX, "notes": sc["notes"],
        "hist": hist, "hist_dates": hist_dates,
        "crdo": {"checks": crdo, "count": nok, "flag": crdo_flag},
        "quality_dip": qd,
    }


# ── stratégie A : avant explosion ───────────────────────────────────────────

def strategy_a(x: pd.DataFrame, srow: pd.Series, f: dict | None, ctx: dict) -> dict:
    row = x.iloc[-1]
    asof = x.index[-1].date()
    c = float(row["close"])
    pts, notes, catalysts = {}, {}, []

    # catalyseur (25) — uniquement des événements réels et datés
    g = 0
    le = last_earnings(f)
    if le:
        try:
            d = date.fromisoformat(le["date"])
        except Exception:
            d = None
        if d and 0 <= (asof - d).days <= 20 and le.get("surprise") is not None:
            if le["surprise"] > 0:
                g += 8
                react = x.loc[x.index >= pd.Timestamp(d)].head(2)
                ok = any((_n(r["ret_1d"]) or 0) > 0 and (_n(r["rvol"]) or 0) >= 1.5 for _, r in react.iterrows())
                g += 4 if ok else 0
                catalysts.append(f"Résultats du {d:%d/%m} meilleurs que prévu (surprise {le['surprise']:+.1f} %)".replace(".", ",")
                                 + (", salués par le marché (hausse sur gros volume)" if ok else ""))
            else:
                catalysts.append(f"Résultats du {d:%d/%m} sous les attentes (surprise {le['surprise']:+.1f} %)".replace(".", ","))
    ra = recent_analyst(f, asof)
    n_up = len(ra["upgrades"]) + len(ra["init_buy"])
    g += 8 if n_up >= 2 else 5 if n_up == 1 else 0
    g += 5 if len(ra["pt_up"]) >= 3 else 3 if ra["pt_up"] else 0
    for a in (ra["upgrades"] + ra["init_buy"])[:3]:
        catalysts.append(f"{a['firm']} : {'relève' if a in ra['upgrades'] else 'initie'} à « {a.get('to') or '?'} » le {a['date'][8:10]}/{a['date'][5:7]}")
    if ra["pt_up"]:
        a = ra["pt_up"][0]
        catalysts.append(f"{len(ra['pt_up'])} objectif(s) de cours relevé(s) en 30 jours"
                         + (f" (dernier : {a['firm']}, {a['pt_prior']:.0f} → {a['pt']:.0f})" if a.get("pt") and a.get("pt_prior") else ""))
    for a in ra["downgrades"][:2]:
        catalysts.append(f"⚠ {a['firm']} abaisse à « {a.get('to') or '?'} » le {a['date'][8:10]}/{a['date'][5:7]}")
    gap = (x["open"] / x["close"].shift(1) - 1).iloc[-10:]
    gaps = gap[(gap >= 0.03) & (x["rvol"].iloc[-10:] >= 2)]
    if len(gaps):
        g += 5
        dd = gaps.index[-1]
        catalysts.append(f"Saut de cours à l'ouverture sur très gros volume le {dd:%d/%m} (souvent signe d'une nouvelle)")
    news = (f or {}).get("news") or []
    recent_news = [n for n in news if n.get("date") and n["date"][:10] >= f"{asof - timedelta(days=3)}"]
    if len(recent_news) >= 3:
        g += 2
    pts["catalyst"] = min(25, g)
    if not f:
        notes["catalyst"] = "fondamentaux et actualités non disponibles : seul le saut de cours sur volume est vérifiable"

    # volume (15)
    rv = max((_n(v) or 0) for v in x["rvol"].iloc[-3:])
    v50 = x["vol_avg50"].shift(1).iloc[-1]
    r50 = float(row["volume"]) / v50 if _n(v50) else None
    vacc = _n(row["vacc_score"]) or 0
    pts["volume"] = min(15, (8 if rv >= 2 else 5 if rv >= 1.5 else 2 if rv >= 1.2 else 0)
                        + (4 if r50 and r50 >= 2 else 2 if r50 and r50 >= 1.5 else 0) + (3 if vacc >= 50 else 0))

    # force relative (15)
    rs = 0
    rs += 3 if (_n(row["rs_20d"]) or 0) > 0 else 0
    rs += 2 if (_n(row["rs_60d"]) or 0) > 0 else 0
    r20 = _n(row["ret_1m"])
    rs_spy = rs_etf = None
    spy = ctx.get("spy_ret20")
    if r20 is not None and spy is not None:
        rs_spy = r20 - spy
        rs += 3 if rs_spy > 0 else 0
    etf = ctx.get("etf_ret20")
    if r20 is not None and etf is not None:
        rs_etf = r20 - etf
        rs += 3 if rs_etf > 0 else 0
    pct = ctx.get("peer_pct")
    if pct is not None:
        rs += 4 if pct >= 0.7 else 2 if pct >= 0.5 else 0
    pts["relative_strength"] = min(15, rs)

    # technique (15)
    rsi = _n(row["rsi14"]) or 0
    t = 0
    t += 3 if (_n(row["sma20"]) and c > row["sma20"] and _n(row["sma50"]) and row["sma20"] > row["sma50"]) else 0
    t += 2 if (_n(row["sma200"]) and c > row["sma200"]) else 0
    t += 3 if 50 <= rsi <= 70 else 1 if 70 < rsi <= 78 else 0
    dres = _n(row["dist_resistance"])
    t += 3 if (bool(row["brk_20d_active"]) or bool(row["in_breakout"]) or (dres is not None and 0 <= dres <= 0.05)) else 0
    t += 2 if bool(row["brk_52w_active"]) else 0
    t += 2 if (bool(row["before_breakout"]) or bool(row["consolidation_exit"])) else 0
    pts["technical"] = min(15, t)

    # positionnement (10) : vendeurs à découvert. Options : non collectées.
    if f and (f.get("short_pct_float") is not None or f.get("short_ratio") is not None):
        sp, sr = f.get("short_pct_float"), f.get("short_ratio")
        p = (5 if sp is not None and sp >= 0.15 else 3 if sp is not None and sp >= 0.08 else 1 if sp is not None and sp >= 0.04 else 0)
        p += 3 if sr is not None and sr >= 5 else 2 if sr is not None and sr >= 3 else 0
        if f.get("shares_short") and f.get("shares_short_prior") and f["shares_short"] > 1.1 * f["shares_short_prior"] and (_n(row["ret_5d"]) or 0) > 0:
            p += 2
        pts["positioning"] = min(10, p)
        notes["positioning"] = "options (activité inhabituelle, calls/puts) : non collectées"
    else:
        pts["positioning"] = 0
        notes["positioning"] = "N/A : données de vente à découvert non fournies par Yahoo ; options non collectées"

    # rotation sectorielle (10)
    br = ctx.get("sector_breadth")
    pts["rotation"] = round(float(np.clip((br - 0.25) / 0.40, 0, 1) * 10), 1) if br is not None else 0

    # qualité / risque (10)
    ff = fund_flags(f)
    if ff["available"]:
        q = 0
        rg = ff["rev_growth"]
        q += 3 if rg is not None and rg >= 0.20 else 2 if rg is not None and rg >= 0.08 else 1 if rg is not None and rg > 0 else 0
        q += 2 if ff["op_margin"] is not None and ff["op_margin"] > 0 else 0
        q += 2 if ff["balance_ok"] else 0
        q += 2 if ff["fcf"] is not None and ff["fcf"] > 0 else 0
        fpe, ps = f.get("forward_pe"), f.get("price_to_sales")
        q += 1 if ((fpe is not None and 0 < fpe < 50) or (ps is not None and ps < 15)) else 0
        pts["quality"] = min(10, q)
    else:
        pts["quality"] = 0
        notes["quality"] = "N/A : fondamentaux non disponibles"

    raw = float(sum(pts.values()))
    pen = _n(srow.get("penalty")) or 1.0
    total = round(raw * pen, 1)
    if pen < 0.95:
        notes["total"] = f"déjà bien avancée : score multiplié par {pen:.2f}".replace(".", ",")
    return {"score": total, "raw": raw, "penalty": round(pen, 3), "points": pts, "max": A_MAX, "notes": notes,
            "catalysts": catalysts, "rs_spy_20d": _r(rs_spy), "rs_etf_20d": _r(rs_etf), "etf": ctx.get("etf"),
            "peer_percentile": _r(pct, 2), "sector_breadth": _r(br, 2), "rvol_max3": _r(rv, 2), "vol_vs_avg50": _r(r50, 2)}


def liquidity(x: pd.DataFrame, f: dict | None) -> dict:
    row = x.iloc[-1]
    c = float(row["close"])
    dv = _n(row["vol_avg20"])
    dv = dv * c if dv is not None else None
    mcap = (f or {}).get("market_cap")
    reasons = []
    if c < MIN_PRICE:
        reasons.append(f"cours sous {MIN_PRICE:.0f} (penny stock)")
    if dv is not None and dv < MIN_DOLLAR_VOL:
        reasons.append("trop peu liquide (moins de 20 M échangés par jour)")
    if mcap is not None and mcap < MIN_MCAP:
        reasons.append("capitalisation sous 1 milliard")
    return {"eligible": not reasons, "reasons": reasons, "dollar_volume20": _r(dv, 0), "market_cap": mcap}
