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
    # ── liste élargie (valeurs tech et croissance US) ──
    "MCHP": ("Microchip Technology", "Semiconductors", "core"),
    "NXPI": ("NXP Semiconductors", "Semiconductors", "core"),
    "MPWR": ("Monolithic Power Systems", "Semiconductors", "core"),
    "SWKS": ("Skyworks Solutions", "Semiconductors", "core"),
    "QRVO": ("Qorvo", "Semiconductors", "core"),
    "SLAB": ("Silicon Labs", "Semiconductors", "core"),
    "LSCC": ("Lattice Semiconductor", "Semiconductors", "core"),
    "SYNA": ("Synaptics", "Semiconductors", "core"),
    "RMBS": ("Rambus", "Semiconductors", "core"),
    "POWI": ("Power Integrations", "Semiconductors", "core"),
    "SITM": ("SiTime", "Semiconductors", "core"),
    "AMBA": ("Ambarella", "Semiconductors", "core"),
    "SMTC": ("Semtech", "Semiconductors", "core"),
    "CRUS": ("Cirrus Logic", "Semiconductors", "core"),
    "ALGM": ("Allegro MicroSystems", "Semiconductors", "core"),
    "NVTS": ("Navitas Semiconductor", "Semiconductors", "core"),
    "AOSL": ("Alpha and Omega Semiconductor", "Semiconductors", "core"),
    "MTSI": ("MACOM Technology", "Semiconductors", "core"),
    "GFS": ("GlobalFoundries", "Semiconductors", "core"),
    "STM": ("STMicroelectronics", "Semiconductors", "core"),
    "UMC": ("United Microelectronics", "Semiconductors", "core"),
    "AMKR": ("Amkor Technology", "Semiconductors", "core"),
    "CEVA": ("CEVA", "Semiconductors", "core"),
    "DIOD": ("Diodes", "Semiconductors", "core"),
    "TER": ("Teradyne", "Semi Equipment", "core"),
    "ENTG": ("Entegris", "Semi Equipment", "core"),
    "ONTO": ("Onto Innovation", "Semi Equipment", "core"),
    "CAMT": ("Camtek", "Semi Equipment", "core"),
    "NVMI": ("Nova", "Semi Equipment", "core"),
    "ACLS": ("Axcelis Technologies", "Semi Equipment", "core"),
    "FORM": ("FormFactor", "Semi Equipment", "core"),
    "KLIC": ("Kulicke and Soffa", "Semi Equipment", "core"),
    "UCTT": ("Ultra Clean Holdings", "Semi Equipment", "core"),
    "ICHR": ("Ichor Holdings", "Semi Equipment", "core"),
    "COHU": ("Cohu", "Semi Equipment", "core"),
    "AEHR": ("Aehr Test Systems", "Semi Equipment", "core"),
    "MKSI": ("MKS Instruments", "Semi Equipment", "core"),
    "VECO": ("Veeco Instruments", "Semi Equipment", "core"),
    "PLAB": ("Photronics", "Semi Equipment", "core"),
    "WDC": ("Western Digital", "Memory", "core"),
    "STX": ("Seagate Technology", "Memory", "core"),
    "SIMO": ("Silicon Motion", "Memory", "core"),
    "NTAP": ("NetApp", "Memory", "core"),
    "DELL": ("Dell Technologies", "AI/Data Center", "core"),
    "HPE": ("Hewlett Packard Enterprise", "AI/Data Center", "core"),
    "NBIS": ("Nebius Group", "AI/Data Center", "core"),
    "CRWV": ("CoreWeave", "AI/Data Center", "core"),
    "IREN": ("IREN", "AI/Data Center", "core"),
    "APLD": ("Applied Digital", "AI/Data Center", "core"),
    "WULF": ("TeraWulf", "AI/Data Center", "core"),
    "CIFR": ("Cipher Mining", "AI/Data Center", "core"),
    "CLS": ("Celestica", "AI/Data Center", "core"),
    "JBL": ("Jabil", "AI/Data Center", "core"),
    "FLEX": ("Flex", "AI/Data Center", "core"),
    "SOUN": ("SoundHound AI", "AI/Data Center", "core"),
    "AI": ("C3.ai", "AI/Data Center", "core"),
    "BBAI": ("BigBear.ai", "AI/Data Center", "core"),
    "CSCO": ("Cisco Systems", "Networking", "core"),
    "CIEN": ("Ciena", "Networking", "core"),
    "AAOI": ("Applied Optoelectronics", "Networking", "core"),
    "FN": ("Fabrinet", "Networking", "core"),
    "NOK": ("Nokia", "Networking", "core"),
    "VIAV": ("Viavi Solutions", "Networking", "core"),
    "EXTR": ("Extreme Networks", "Networking", "core"),
    "ORCL": ("Oracle", "Cloud", "core"),
    "CRM": ("Salesforce", "Cloud", "core"),
    "NOW": ("ServiceNow", "Cloud", "core"),
    "WDAY": ("Workday", "Cloud", "core"),
    "TEAM": ("Atlassian", "Cloud", "core"),
    "HUBS": ("HubSpot", "Cloud", "core"),
    "ESTC": ("Elastic", "Cloud", "core"),
    "GTLB": ("GitLab", "Cloud", "core"),
    "DOCN": ("DigitalOcean", "Cloud", "core"),
    "TWLO": ("Twilio", "Cloud", "core"),
    "IOT": ("Samsara", "Cloud", "core"),
    "ZM": ("Zoom Communications", "Cloud", "core"),
    "DOCU": ("DocuSign", "Cloud", "core"),
    "BILL": ("BILL Holdings", "Cloud", "core"),
    "VEEV": ("Veeva Systems", "Cloud", "core"),
    "IBM": ("IBM", "Cloud", "core"),
    "ZS": ("Zscaler", "Cybersecurity", "core"),
    "OKTA": ("Okta", "Cybersecurity", "core"),
    "S": ("SentinelOne", "Cybersecurity", "core"),
    "FTNT": ("Fortinet", "Cybersecurity", "core"),
    "TENB": ("Tenable", "Cybersecurity", "core"),
    "RBRK": ("Rubrik", "Cybersecurity", "core"),
    "QLYS": ("Qualys", "Cybersecurity", "core"),
    "VRNS": ("Varonis", "Cybersecurity", "core"),
    "ADBE": ("Adobe", "Software", "core"),
    "INTU": ("Intuit", "Software", "core"),
    "SNPS": ("Synopsys", "Software", "core"),
    "CDNS": ("Cadence Design Systems", "Software", "core"),
    "ADSK": ("Autodesk", "Software", "core"),
    "PATH": ("UiPath", "Software", "core"),
    "U": ("Unity Software", "Software", "core"),
    "TTD": ("The Trade Desk", "Software", "core"),
    "SHOP": ("Shopify", "Software", "core"),
    "DUOL": ("Duolingo", "Software", "core"),
    "RBLX": ("Roblox", "Software", "core"),
    "MSTR": ("Strategy", "Software", "core"),
    "APPF": ("AppFolio", "Software", "core"),
    "PAYC": ("Paycom", "Software", "core"),
    "RGTI": ("Rigetti Computing", "Quantum", "core"),
    "QBTS": ("D-Wave Quantum", "Quantum", "core"),
    "QUBT": ("Quantum Computing Inc", "Quantum", "core"),
    "VST": ("Vistra", "Power/Energy", "core"),
    "CEG": ("Constellation Energy", "Power/Energy", "core"),
    "GEV": ("GE Vernova", "Power/Energy", "core"),
    "TLN": ("Talen Energy", "Power/Energy", "core"),
    "OKLO": ("Oklo", "Power/Energy", "core"),
    "SMR": ("NuScale Power", "Power/Energy", "core"),
    "FSLR": ("First Solar", "Power/Energy", "core"),
    "ENPH": ("Enphase Energy", "Power/Energy", "core"),
    "FLNC": ("Fluence Energy", "Power/Energy", "core"),
    "EOSE": ("Eos Energy", "Power/Energy", "core"),
    "ETN": ("Eaton", "Power/Energy", "core"),
    "NFLX": ("Netflix", "Other", "core"),
    "UBER": ("Uber", "Other", "core"),
    "ABNB": ("Airbnb", "Other", "core"),
    "DASH": ("DoorDash", "Other", "core"),
    "SPOT": ("Spotify", "Other", "core"),
    "COIN": ("Coinbase", "Other", "core"),
    "HOOD": ("Robinhood", "Other", "core"),
    "SOFI": ("SoFi Technologies", "Other", "core"),
    "AFRM": ("Affirm", "Other", "core"),
    "MELI": ("MercadoLibre", "Other", "core"),
    "SE": ("Sea Limited", "Other", "core"),
    "PDD": ("PDD Holdings", "Other", "core"),
    "BABA": ("Alibaba", "Other", "core"),
    "RDDT": ("Reddit", "Other", "core"),
    "PINS": ("Pinterest", "Other", "core"),
    "SNAP": ("Snap", "Other", "core"),
    "ASTS": ("AST SpaceMobile", "Other", "core"),
    "RKLB": ("Rocket Lab", "Other", "core"),
    "LUNR": ("Intuitive Machines", "Other", "core"),
    "JOBY": ("Joby Aviation", "Other", "core"),
    "ACHR": ("Archer Aviation", "Other", "core"),
    "GRAB": ("Grab", "Other", "core"),
    "TOST": ("Toast", "Other", "core"),
    "CPNG": ("Coupang", "Other", "core"),
    "NU": ("Nu Holdings", "Other", "core"),
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
