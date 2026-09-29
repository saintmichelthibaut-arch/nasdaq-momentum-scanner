"""Notifications push vers la page installée sur l'écran d'accueil (iPhone, Android, ordinateur).

Protocole standard Web Push (RFC 8030 / 8291 / 8292), sans service tiers :
GitHub Actions envoie directement aux serveurs de notification d'Apple / Google / Mozilla.

Secrets GitHub (Settings → Secrets and variables → Actions) :
  PUSH_SEED            une phrase secrète quelconque, longue (sert à fabriquer la clé d'envoi)
  PUSH_SUBSCRIPTIONS   un code d'abonnement par ligne (copié depuis la page, bouton 🔔)

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


def vapid_key():
    """Clé d'envoi dérivée de la phrase secrète PUSH_SEED (toujours la même pour une même phrase)."""
    seed = os.environ.get("PUSH_SEED", "")
    if not seed:
        return None
    d = int.from_bytes(hashlib.sha256(("momentum-scanner-vapid:" + seed).encode()).digest(), "big") % (CURVE_ORDER - 1) + 1
    return ec.derive_private_key(d, ec.SECP256R1())


def page_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return "https://github.com"


def subscriptions() -> list[dict]:
    out = []
    for line in os.environ.get("PUSH_SUBSCRIPTIONS", "").splitlines():
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
        key = vapid_key()
        print(b64u(raw_public(key)) if key else "")
        return 0
    if cmd == "test":
        send("✅ Momentum Scanner", "Les notifications fonctionnent sur cet appareil.", tag="test")
        return 0
    if cmd == "daily":
        path = ROOT / "data" / "alerts.json"
        alerts = json.loads(path.read_text(encoding="utf-8")).get("alerts", []) if path.exists() else []
        if not alerts:
            print("aucune alerte ce soir : pas de notification")
            return 0
        title, body = alerts_message(alerts, live=False)
        send(title, body, tag="soir")
    return 0


if __name__ == "__main__":
    sys.exit(main((sys.argv[1:] or ["daily"])[0]))
