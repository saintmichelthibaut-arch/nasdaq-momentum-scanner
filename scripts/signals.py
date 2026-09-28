"""Signaux lisibles tirés des indicateurs du jour : breakouts, anomalies,
explications en clair, similarité MU / SNDK, WHAT CHANGED, supports/résistances.

Chaque phrase est construite à partir des valeurs réelles du titre.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from indicators import BREAKOUT_WINDOWS, SETUP_LABELS


# ── petits outils ───────────────────────────────────────────────────────────

def num(v, nd: int = 4):
    """float JSON-safe : None si NaN/inf/absent."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def pct(v, nd: int = 1, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "N/A"
    return f"{v * 100:+.{nd}f} %" if sign else f"{v * 100:.{nd}f} %"


def fmt_price(v, cur: str) -> str:
    sym = "€" if cur == "EUR" else "$"
    return f"{sym}{v:,.2f}" if cur == "USD" else f"{v:,.2f} {sym}"


def lin1(x, lo, hi) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def g(row: pd.Series, k: str):
    v = row.get(k)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


# ── breakouts ───────────────────────────────────────────────────────────────

BRK_LABEL = {"20d": "plus haut 20 séances", "50d": "plus haut 50 séances",
             "100d": "plus haut 100 séances", "52w": "plus haut 52 semaines"}


def breakout_list(row: pd.Series) -> list[dict]:
    out = []
    for k in BREAKOUT_WINDOWS:
        if not bool(g(row, f"brk_{k}_active")):
            continue
        lvl = g(row, f"brk_{k}_cross_level")
        out.append({
            "level_type": k,
            "label": BRK_LABEL[k],
            "level": num(lvl),
            "days_ago": int(g(row, f"brk_{k}_days") or 0),
            "confirm_sessions": int(g(row, f"brk_{k}_confirm") or 0),
            "dist_pct": num(row["close"] / lvl - 1) if lvl else None,
            "strength": num(g(row, f"brk_{k}_strength"), 1),
        })
    if bool(g(row, "consolidation_exit")):
        out.append({"level_type": "consolidation", "label": "sortie de consolidation (base serrée 20 séances)",
                    "level": num(g(row, "brk_20d_cross_level")), "days_ago": 0, "confirm_sessions": 1,
                    "dist_pct": None, "strength": num(g(row, "brk_20d_strength"), 1)})
    if bool(g(row, "range_exit")):
        out.append({"level_type": "range", "label": "sortie de range 50 séances",
                    "level": num(g(row, "brk_50d_cross_level")), "days_ago": 0, "confirm_sessions": 1,
                    "dist_pct": None, "strength": num(g(row, "brk_50d_strength"), 1)})
    out.sort(key=lambda b: -(b["strength"] or 0))
    return out


# ── anomalies (z-score sur l'historique du titre) ───────────────────────────

def anomaly_list(x: pd.DataFrame, zthr: float) -> list[dict]:
    row = x.iloc[-1]
    out = []

    def add(kind, label, z=None, detail=""):
        out.append({"kind": kind, "label": label, "z": num(z, 2), "detail": detail})

    zv, zv5, zr, zg = (g(row, k) for k in ("z_volume", "z_volume5", "z_return", "z_range"))
    if zv is not None and zv >= zthr:
        add("volume", "Volume inhabituel", zv, f"volume {row['volume']/1e6:.1f}M, RVOL {row['rvol']:.1f}")
    elif zv5 is not None and zv5 >= zthr:
        add("volume", "Volume inhabituel sur 5 séances", zv5, f"moyenne 5j {x['volume'].iloc[-5:].mean()/1e6:.1f}M")
    if zr is not None and abs(zr) >= zthr:
        add("return", "Variation inhabituelle", zr, f"séance {pct(row['ret_1d'])}")
    if zg is not None and zg >= zthr:
        add("volatility", "Volatilité inhabituelle", zg, f"amplitude {row['high']/row['low']*100-100:.1f} % dans la séance")

    above50 = x["close"] > x["sma50"]
    run_before = 0
    for v in above50.iloc[-2::-1]:
        if bool(v) == bool(above50.iloc[-2]):
            run_before += 1
        else:
            break
    if pd.notna(row["sma50"]) and bool(above50.iloc[-1]) != bool(above50.iloc[-2]) and run_before >= 20:
        if above50.iloc[-1]:
            add("trend_break", "Rupture de tendance haussière", None,
                f"repasse au-dessus de la SMA50 après {run_before} séances en dessous")
        else:
            add("trend_break", "Rupture de tendance baissière", None,
                f"casse la SMA50 après {run_before} séances au-dessus")

    c20max = x["close"].rolling(20).max()
    c20min = x["close"].rolling(20).min()
    rsi_prev_max = x["rsi14"].rolling(20).max().shift(1)
    rsi_prev_min = x["rsi14"].rolling(20).min().shift(1)
    if pd.notna(row["rsi14"]) and row["close"] >= c20max.iloc[-1] and row["rsi14"] < rsi_prev_max.iloc[-1] - 5:
        add("rsi_divergence", "Divergence RSI/prix baissière", None,
            f"nouveau plus haut 20j mais RSI {row['rsi14']:.0f} < {rsi_prev_max.iloc[-1]:.0f} précédent")
    if pd.notna(row["rsi14"]) and row["close"] <= c20min.iloc[-1] and row["rsi14"] > rsi_prev_min.iloc[-1] + 5:
        add("rsi_divergence", "Divergence RSI/prix haussière", None,
            f"nouveau plus bas 20j mais RSI {row['rsi14']:.0f} > {rsi_prev_min.iloc[-1]:.0f} précédent")

    r5, vr = g(row, "ret_5d"), g(row, "vratio5")
    if r5 is not None and vr is not None:
        if r5 > 0.03 and vr < 0.8:
            add("volume_divergence", "Divergence volume/prix", None,
                f"hausse de {pct(r5)} sur 5j avec un volume à {vr:.2f}× la normale")
        elif abs(r5) < 0.02 and vr >= 1.8:
            add("volume_divergence", "Divergence volume/prix", None,
                f"prix stable ({pct(r5)} sur 5j) mais volume à {vr:.1f}× la normale")

    if g(row, "sma200_reclaim_days") == 0:
        add("ma_reclaim", "Retour au-dessus de la SMA200", None, f"clôture {row['close']:.2f} > SMA200 {row['sma200']:.2f}")
    elif g(row, "sma50_reclaim_days") == 0:
        add("ma_reclaim", "Retour au-dessus de la SMA50", None, f"clôture {row['close']:.2f} > SMA50 {row['sma50']:.2f}")
    return out


# ── explications en clair ───────────────────────────────────────────────────

def reasons(row: pd.Series, sc: pd.Series, cur: str) -> list[str]:
    """Liste de faits chiffrés, du plus parlant au moins parlant."""
    items: list[tuple[float, str]] = []
    streak_v = int(g(row, "vol_up_streak") or 0)
    vacc = g(row, "vacc_score")
    if vacc is not None and vacc >= 40:
        items.append((vacc, f"volume en rampe : pente {row['vacc_slope5']:+.0f} %/séance sur 5j (R² {row['vacc_r2_5']:.2f})"
                            + (f", {streak_v} hausses de volume d'affilée" if streak_v >= 3 else "")))
    elif streak_v >= 3:
        items.append((40, f"volume en hausse {streak_v} séances d'affilée"))
    rv = g(row, "rvol")
    if rv is not None and rv >= 1.5:
        items.append((min(100, rv * 30), f"RVOL {rv:.1f} (volume du jour vs moyenne 20j)"))
    setup = row.get("setup", "none")
    if setup and setup != "none":
        items.append((90 if setup == "vol_leads_price" else 55, SETUP_LABELS[setup]))
    for b in breakout_list(row)[:1]:
        items.append((b["strength"] or 50, f"breakout du {b['label']} ({fmt_price(b['level'], cur)}) il y a {b['days_ago']} séance(s), force {b['strength']:.0f}/100"))
    if bool(g(row, "before_breakout")):
        items.append((85, f"avant breakout : {int(row['bb_count'])}/8 conditions, résistance à {pct(row['dist_resistance'], sign=False)} ({fmt_price(row['resistance'], cur)})"))
    rs20 = g(row, "rs_20d")
    if rs20 is not None and abs(rs20) >= 0.03:
        items.append((lin1(abs(rs20), 0.03, 0.15) * 80 + 20, f"RS vs QQQ {rs20 * 100:+.1f} pts sur 20j"))
    if bool(sc.get("c_early_rs")):
        items.append((75, f"surperformance du Nasdaq qui démarre avant les plus hauts ({pct(row['dist_high_52w'])} du plus haut 52s)"))
    comp = g(row, "compression")
    if comp is not None and comp < 0.8:
        items.append((60, f"volatilité comprimée : ATR à {comp:.2f}× sa moyenne 60j"))
    rsi14 = g(row, "rsi14")
    if rsi14 is not None and rsi14 >= 75:
        items.append((70, f"RSI14 {rsi14:.0f} : surachat, score pénalisé ×{sc['penalty']:.2f}"))
    r1y = g(row, "ret_1y")
    if r1y is not None and r1y >= 1.0:
        items.append((65, f"déjà {pct(r1y, 0)} sur 1 an : entrée tardive"))
    gc = g(row, "golden_cross_days")
    if gc is not None and gc <= 20:
        items.append((50, f"golden cross SMA50/SMA200 il y a {int(gc)} séance(s)"))
    items.sort(key=lambda t: -t[0])
    return [t[1] for t in items[:5]]


# ── profils Next MU / Next SNDK ─────────────────────────────────────────────

def similarity(row: pd.Series, sc: pd.Series, score_hist: list[float], cur: str) -> dict:
    """Profil A (Early MU) : amélioration progressive. Profil B (Early SNDK) : accélération confirmée."""
    hist = [h for h in score_hist if h is not None]
    d10 = hist[-1] - hist[0] if len(hist) >= 2 else None
    steps = [b - a for a, b in zip(hist[-6:-1], hist[-5:])] if len(hist) >= 6 else []
    steady = sum(1 for s in steps if s > 0)

    rs20 = g(row, "rs_20d") or 0.0
    dres = g(row, "dist_resistance")
    rsi14 = g(row, "rsi14") or 0.0
    rvol5 = g(row, "rvol5_50") or 0.0
    vacc = g(row, "vacc_score") or 0.0
    r1m = g(row, "ret_1m") or 0.0

    # Profil A — Early MU
    a_parts = {
        "score_trend": 0.5 * lin1(d10, 0, 20) + 0.5 * (steady / 5 if steps else 0),
        "vol_gradual": 0.6 * vacc / 100 + 0.4 * lin1(rvol5, 1.0, 1.6) * (1.0 if (g(row, "rvol") or 0) < 3 else 0.5),
        "rs_improving": 0.6 * lin1(rs20, 0, 0.08) + 0.4 * (1.0 if (g(row, "rs_line_chg10") or 0) > 0.01 else 0.0),
        "near_breakout": max(lin1(dres, 0.08, 0.0) if dres is not None and dres >= 0 else 0.0, (g(row, "bb_count") or 0) / 8),
        "not_extended": 1.0 if 50 <= rsi14 <= 70 else max(0.0, 1 - abs(rsi14 - 60) / 25),
    }
    a_w = {"score_trend": 25, "vol_gradual": 25, "rs_improving": 20, "near_breakout": 20, "not_extended": 10}
    a_score = sum(a_parts[k] * w for k, w in a_w.items())

    # Profil B — Early SNDK
    brks = breakout_list(row)
    big_brk = [b for b in brks if b["level_type"] in ("100d", "52w", "50d")]
    b_parts = {
        "price_accel": 0.6 * lin1(r1m, 0.05, 0.30) + 0.4 * (sc.get("c_acceleration", 0) or 0) / 100,
        "vol_exceptional": 0.5 * lin1(rvol5, 1.2, 2.5) + 0.5 * lin1(g(row, "z_volume5") or 0, 0.5, 3.0),
        "breakout_confirmed": (lin1(max((b["confirm_sessions"] for b in big_brk), default=0), 0, 3) if big_brk else 0.0),
        "rs_very_high": lin1(rs20, 0.03, 0.20),
        "continuation": (1.0 if (g(row, "ret_5d") or 0) > 0 and (g(row, "close") or 0) > (g(row, "sma20") or math.inf) else 0.0),
    }
    b_w = {"price_accel": 25, "vol_exceptional": 20, "breakout_confirmed": 25, "rs_very_high": 20, "continuation": 10}
    b_score = sum(b_parts[k] * w for k, w in b_w.items())

    # Phrases construites à partir des valeurs réelles
    a_facts = []
    if d10 is not None:
        a_facts.append(f"score {hist[0]:.0f}→{hist[-1]:.0f} sur {len(hist) - 1} séances ({steady}/5 hausses récentes)")
    a_facts.append(f"volume accel {vacc:.0f}/100, moyenne 5j à {rvol5:.2f}× la moyenne 50j")
    a_facts.append(f"RS 20j {rs20 * 100:+.1f} pts vs QQQ")
    if dres is not None and dres >= 0:
        a_facts.append(f"résistance à {dres * 100:.1f} %")
    a_facts.append(f"RSI {rsi14:.0f}")

    b_facts = [f"{pct(r1m)} sur 1 mois", f"volume 5j à {rvol5:.2f}× la moyenne 50j"]
    if big_brk:
        b0 = big_brk[0]
        b_facts.append(f"breakout {b0['label']} confirmé {b0['confirm_sessions']} séance(s)")
    else:
        b_facts.append("pas de breakout 50j/100j/52s actif")
    b_facts.append(f"RS 20j {rs20 * 100:+.1f} pts vs QQQ")

    return {
        "A": {"score": round(a_score, 1), "parts": {k: round(v * 100) for k, v in a_parts.items()},
              "why": ", ".join(a_facts) + "."},
        "B": {"score": round(b_score, 1), "parts": {k: round(v * 100) for k, v in b_parts.items()},
              "why": ", ".join(b_facts) + "."},
    }


# ── WHAT CHANGED : 5 dernières séances vs 20 précédentes ────────────────────

def what_changed(x: pd.DataFrame, score: pd.Series) -> dict | None:
    if len(x) < 26:
        return None
    last5 = x.iloc[-5:]
    prev20 = x.iloc[-25:-5]
    r = np.log(x["close"] / x["close"].shift(1))

    def row(label, a, b, unit, fmt):
        return {"label": label, "last5": num(a), "prev20": num(b), "unit": unit, "fmt": fmt,
                "change": num(a - b) if a is not None and b is not None else None}

    sc5 = score.iloc[-5:].mean() if score.notna().iloc[-5:].any() else None
    sc20 = score.iloc[-25:-5].mean() if score.notna().iloc[-25:-5].any() else None
    rs_now = x["rs_5d"].iloc[-1]
    rs_prev = x["rs_20d"].iloc[-6] / 4 if pd.notna(x["rs_20d"].iloc[-6]) else None
    return {"rows": [
        row("Rendement moyen / séance", r.iloc[-5:].mean(), r.iloc[-25:-5].mean(), "%", "pct"),
        row("Volume moyen", last5["volume"].mean(), prev20["volume"].mean(), "M", "vol"),
        row("ATR % moyen", last5["atr_pct"].mean(), prev20["atr_pct"].mean(), "%", "raw"),
        row("RSI14 moyen", last5["rsi14"].mean(), prev20["rsi14"].mean(), "", "raw"),
        row("RS vs QQQ (pts de % / 5 séances)", rs_now, rs_prev, "pts", "pts"),
        row("Score moyen", sc5, sc20, "", "raw"),
    ]}


# ── supports / résistances (pivots regroupés) ───────────────────────────────

def sr_levels(x: pd.DataFrame, lookback: int = 250, w: int = 5, tol: float = 0.015) -> dict:
    d = x.iloc[-lookback:]
    h, l = d["high"].to_numpy(), d["low"].to_numpy()
    piv = []
    for i in range(w, len(d) - w):
        if h[i] == h[i - w:i + w + 1].max():
            piv.append(h[i])
        if l[i] == l[i - w:i + w + 1].min():
            piv.append(l[i])
    piv.sort()
    clusters: list[list[float]] = []
    for p in piv:
        if clusters and p <= clusters[-1][-1] * (1 + tol):
            clusters[-1].append(p)
        else:
            clusters.append([p])
    close = float(d["close"].iloc[-1])
    lv = [(float(np.mean(cl)), len(cl)) for cl in clusters if len(cl) >= 2]
    res = sorted([(p, n) for p, n in lv if p > close], key=lambda t: t[0])[:3]
    sup = sorted([(p, n) for p, n in lv if p <= close], key=lambda t: -t[0])[:3]
    return {"resistance": [{"price": round(p, 4), "touches": n} for p, n in res],
            "support": [{"price": round(p, 4), "touches": n} for p, n in sup]}
