"""Univers de titres suivis par le scanner.

Pour ajouter un titre : une ligne dans UNIVERSE. Rien d'autre à modifier.
  ticker Yahoo -> (nom, secteur, groupe)

Le nom et le secteur sont des métadonnées saisies à la main (pas des données de
marché). Les prix et volumes viennent exclusivement de yfinance.
"""

SECTORS = [
    "Semiconductors",
    "Semi Equipment",
    "Memory",
    "AI/Data Center",
    "Networking",
    "Cloud",
    "Cybersecurity",
    "Software",
    "Quantum",
    "Power/Energy",
    "Other",
]

# groupe : "core" = univers swing tech US, "pere" = portefeuille long terme
UNIVERSE = {
    # Semiconductors
    "NVDA": ("NVIDIA", "Semiconductors", "core"),
    "AMD": ("Advanced Micro Devices", "Semiconductors", "core"),
    "AVGO": ("Broadcom", "Semiconductors", "core"),
    "MRVL": ("Marvell Technology", "Semiconductors", "core"),
    "ARM": ("Arm Holdings", "Semiconductors", "core"),
    "TSM": ("Taiwan Semiconductor", "Semiconductors", "core"),
    "ON": ("ON Semiconductor", "Semiconductors", "core"),
    "INTC": ("Intel", "Semiconductors", "core"),
    "QCOM": ("Qualcomm", "Semiconductors", "core"),
    "TXN": ("Texas Instruments", "Semiconductors", "core"),
    "ADI": ("Analog Devices", "Semiconductors", "core"),
    # Semi Equipment
    "ASML": ("ASML Holding", "Semi Equipment", "core"),
    "AMAT": ("Applied Materials", "Semi Equipment", "core"),
    "LRCX": ("Lam Research", "Semi Equipment", "core"),
    "KLAC": ("KLA Corp", "Semi Equipment", "core"),
    # Memory
    "MU": ("Micron Technology", "Memory", "core"),
    "SNDK": ("Sandisk", "Memory", "core"),
    # AI / Data Center
    "SMCI": ("Super Micro Computer", "AI/Data Center", "core"),
    "VRT": ("Vertiv Holdings", "AI/Data Center", "core"),
    # Networking
    "ANET": ("Arista Networks", "Networking", "core"),
    "CRDO": ("Credo Technology", "Networking", "core"),
    "ALAB": ("Astera Labs", "Networking", "core"),
    "LITE": ("Lumentum", "Networking", "core"),
    "COHR": ("Coherent", "Networking", "core"),
    # Cloud
    "SNOW": ("Snowflake", "Cloud", "core"),
    "DDOG": ("Datadog", "Cloud", "core"),
    "NET": ("Cloudflare", "Cloud", "core"),
    "MDB": ("MongoDB", "Cloud", "core"),
    "MSFT": ("Microsoft", "Cloud", "core"),
    "AMZN": ("Amazon", "Cloud", "core"),
    "GOOGL": ("Alphabet", "Cloud", "core"),
    # Cybersecurity
    "CRWD": ("CrowdStrike", "Cybersecurity", "core"),
    "PANW": ("Palo Alto Networks", "Cybersecurity", "core"),
    # Software
    "PLTR": ("Palantir", "Software", "core"),
    "APP": ("AppLovin", "Software", "core"),
    # Quantum
    "IONQ": ("IonQ", "Quantum", "core"),
    # Power / Energy
    "BE": ("Bloom Energy", "Power/Energy", "core"),
    "SU.PA": ("Schneider Electric", "Power/Energy", "pere"),
    # Other
    "META": ("Meta Platforms", "Other", "core"),
    "AAPL": ("Apple", "Other", "core"),
    "TSLA": ("Tesla", "Other", "core"),
    "SAF.PA": ("Safran", "Other", "pere"),
    "LR.PA": ("Air Liquide", "Other", "pere"),
    "HO.PA": ("Thales", "Other", "pere"),
    "V": ("Visa", "Other", "pere"),
    "MA": ("Mastercard", "Other", "pere"),
}

# Indices de référence. QQQ = benchmark principal pour tous les titres.
# ^FCHI (CAC 40) = benchmark local pour les titres cotés à Paris.
BENCHMARK = "QQQ"
LOCAL_BENCHMARKS = {"EU": "^FCHI"}


def market_of(ticker: str) -> str:
    """US ou EU, déduit du suffixe Yahoo."""
    return "EU" if ticker.endswith(".PA") else "US"


def currency_of(ticker: str) -> str:
    return "EUR" if market_of(ticker) == "EU" else "USD"


def all_download_tickers() -> list[str]:
    """Titres + benchmarks, sans doublon."""
    out = list(UNIVERSE.keys())
    for b in [BENCHMARK, *LOCAL_BENCHMARKS.values()]:
        if b not in out:
            out.append(b)
    return out


def meta(ticker: str) -> dict:
    name, sector, group = UNIVERSE[ticker]
    if sector not in SECTORS:
        sector = "Other"
    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "group": group,
        "market": market_of(ticker),
        "currency": currency_of(ticker),
    }
