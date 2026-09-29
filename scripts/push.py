"""Notifications push vers la page installée sur l'écran d'accueil (iPhone, Android, ordinateur).

Protocole standard Web Push (RFC 8030 / 8291 / 8292), sans service tiers :
GitHub Actions envoie directement aux serveurs de notification d'Apple / Google / Mozilla.

Secrets GitHub (Settings → Secrets and variables → Actions) :
  PUSH_SEED            (optionnel) phrase secrète ; sinon la clé est générée et gardée en cache
  PUSH_SUBSCRIPTIONS   (optionnel) codes d'abonnement ; ils peuvent aussi être dans
                       config/push_subscriptions.txt (un code par ligne)

  python scripts/push.py pubkey          écrit la clé publique (lue par la page)
  python scripts/push.py daily           notifications des alertes du soir
  python scripts/push.py test            notification de test
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parent.parent
CURVE_ORDER = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


SUBS_FILE = ROOT / "config" / "push_subscriptions.txt"
KEY_FILE = ROOT / ".vapid" / "key.pem"   # conservée dans le cache GitHub Actions, jamais committée


def vapid_key(create: bool = False):
    """Clé d'envoi. Priorité : secret PUSH_SEED s'il existe ; sinon clé générée une fois
    par le robot et conservée dans le cache GitHub (aucune manipulation nécessaire)."""
    seed = os.environ.get("PUSH_SEED", "")
    if seed:
        d = int.from_bytes(hashlib.sha256(("momentum-scanner-vapid:" + seed).encode()).digest(), "big") % (CURVE_ORDER - 1) + 1
        return ec.derive_private_key(d, ec.SECP256R1())
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    if not create:
        return None
    key = ec.generate_private_key(ec.SECP256R1())
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    print("nouvelle clé d'envoi créée")
    return key


def page_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "https://github.com"


def subscriptions() -> list[dict]:
    out = []
    lines = os.environ.get("PUSH_SUBSCRIPTIONS", "").splitlines()
    if SUBS_FILE.exists():  # codes copiés depuis la page (bouton 🔔), un par ligne
        lines += [ln for ln in SUBS_FILE.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            sub = json.loads(line)
            if sub.get("endpoint") and sub.get("keys", {}).get("p256dh") and sub["keys"].get("auth"):
                out.append(sub)
        except json.JSONDecodeError:
            print("un code d'abonnement est mal copié (ignoré)")
    return out


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """Chiffrement aes128gcm (RFC 8291)."""
    ua_public = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_public = raw_public(as_key)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + body


def vapid_header(endpoint: str, key) -> str:
    u = urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": page_url()}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={header}.{claims}.{b64u(sig)}, k={b64u(raw_public(key))}"


def send_one(sub: dict, message: dict, key) -> int:
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub["keys"]["p256dh"], sub["keys"]["auth"])
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Authorization": vapid_header(sub["endpoint"], key), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": "21600", "Urgency": "high"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def configured() -> bool:
    return bool(vapid_key()) and bool(subscriptions())


def send(title: str, body: str, tag: str = "alertes", path: str = "") -> bool:
    key = vapid_key()
    subs = subscriptions()
    if not key or not subs:
        print("notifications non configurées (secrets PUSH_SEED / PUSH_SUBSCRIPTIONS absents) : aucun envoi")
        return False
    msg = {"title": title, "body": body, "tag": tag, "url": page_url() + path}
    ok = 0
    for i, sub in enumerate(subs, 1):
        code = send_one(sub, msg, key)
        if 200 <= code < 300:
            ok += 1
        elif code in (404, 410):
            print(f"appareil n°{i} : abonnement expiré, il faut réactiver les notifications sur cet appareil")
        else:
            print(f"appareil n°{i} : refus du serveur de notification (HTTP {code})")
    print(f"notification envoyée à {ok}/{len(subs)} appareil(s) : {title}")
    return ok > 0


def alerts_message(alerts: list[dict], live: bool) -> tuple[str, str]:
    tick = ", ".join(a["ticker"] for a in alerts[:4]) + (f" +{len(alerts) - 4}" if len(alerts) > 4 else "")
    title = ("⚡ En séance : " if live else "📈 Alertes du soir : ") + tick
    lines = []
    for a in alerts[:4]:
        chg = "" if a.get("chg_1d") is None else f" {a['chg_1d'] * 100:+.1f}%".replace(".", ",")
        lines.append(f"{a['ticker']}{chg} · {a.get('reason', '')}")
    return title, "\n".join(lines)


def main(cmd: str) -> int:
    if cmd == "pubkey":
        key = vapid_key(create=True)
        print(b64u(raw_public(key)) if key else "")
        return 0
    if cmd == "test":
        send("✅ Momentum Scanner", "Les notifications fonctionnent sur cet appareil.", tag="test")
        return 0
    if cmd == "daily":
        msgs = evening_digest()
        if not msgs:
            print("rien de notable ce soir : pas de notification")
        for title, body, tag in msgs:
            send(title, body, tag=tag)
    return 0


def _pct(v) -> str:
    return "" if v is None else f"{v * 100:+.1f} %".replace(".", ",")


def evening_digest() -> list[tuple[str, str, str]]:
    """Tout ce qui s'est passé à la séance, une notification par type d'événement."""
    latest_p = ROOT / "data" / "latest.json"
    if not latest_p.exists():
        return []
    d = json.loads(latest_p.read_text(encoding="utf-8"))
    stocks = d["stocks"]
    day = (d["meta"].get("market_date") or {}).get("US")
    prev_bb = set()
    hist = sorted((ROOT / "data" / "history").glob("*.json"))
    prev_files = [h for h in hist if h.stem < (day or "")]
    if prev_files:
        snap = json.loads(prev_files[-1].read_text(encoding="utf-8")).get("stocks", {})
        prev_bb = {t for t, v in snap.items() if v.get("bb")}
    out = []

    def add(title, items, tag):
        if items:
            more = f"\n+{len(items) - 5} autre(s) dans l'application" if len(items) > 5 else ""
            out.append((f"{title} ({len(items)})", "\n".join(items[:5]) + more, tag))

    alerts = d.get("alerts", [])
    add("📈 Alertes du soir", [f"{a['ticker']} {_pct(a.get('chg_1d'))} · {a.get('reason', '')}" for a in alerts], "soir-alertes")
    add("🚀 Cassures du jour", [f"{s['ticker']} {_pct(s['price']['chg_1d'])} · {b['label']} ({b['level']:.2f}), force {b['strength']:.0f}/100"
                               for s in stocks for b in s["breakouts"][:1] if b["days_ago"] == 0], "soir-cassures")
    add("🟢 Nouvelles prêtes à décoller", [f"{s['ticker']} · {s['setup']['bb_count']}/8 signes, plafond à {(s['setup']['dist_resistance'] or 0) * 100:.1f} %".replace(".", ",")
                                         for s in stocks if s["setup"]["before_breakout"] and s["ticker"] not in prev_bb], "soir-bb")
    acc = [(s, s["score"]["profiles"]["swing"]) for s in stocks]
    add("⚡ Score qui accélère", [f"{s['ticker']} · score {p['hist'][-2]:.0f} → {p['total']:.0f}" if len(p["hist"]) >= 2 else s["ticker"]
                                for s, p in sorted(acc, key=lambda x: -(x[1]["delta"]["d1"] or 0))
                                if (p["delta"]["d1"] or 0) >= 8 or (p["delta"]["d5"] or 0) >= 15], "soir-accel")
    add("🔊 Volume anormal", [f"{s['ticker']} {_pct(s['price']['chg_1d'])} · volume {s['volume']['rvol']:.1f} fois la normale".replace(".", ",", 1)
                             for s in stocks if (s["volume"]["rvol"] or 0) >= 2], "soir-volume")
    add("🆕 Nouvelles venues dans le top", [f"{r['ticker']} · rang {r['rank']}" for r in d["sections"]["swing"].get("new_names", []) if r["entered_days_ago"] == 0], "soir-new")
    add("⚠️ Inhabituel", [f"{s['ticker']} · {a['label']}" for s in stocks for a in s["anomalies"] if a["kind"] != "volume"], "soir-anomalies")
    mu = [f"{s['ticker']} · ressemble à {'MU' if k == 'A' else 'SNDK'} à {s['similarity'][k]['score']:.0f}/100"
          for s in stocks for k in ("A", "B") if s["similarity"][k]["score"] >= 70]
    add("🔮 Prochain MU / SNDK", mu, "soir-mu")
    return out


if __name__ == "__main__":
    sys.exit(main((sys.argv[1:] or ["daily"])[0]))
