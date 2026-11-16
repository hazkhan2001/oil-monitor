"""
universe.py: the list of everything the morning brief tracks.

LESSON: keeping configuration (WHAT to fetch) apart from logic (HOW to fetch)
means adding a new index or city later is a one-line change here, and the
fetching code never has to be touched.

Each market entry is (group, display name, Yahoo Finance symbol).
"""

MARKETS = [
    # ---- US cash indices and the ETF Hamza follows -----------------------
    ("US", "Dow Jones", "^DJI"),
    ("US", "S&P 500", "^GSPC"),
    ("US", "SPY", "SPY"),
    ("US", "Nasdaq Comp", "^IXIC"),
    ("US", "Russell 2000", "^RUT"),
    ("US", "VIX", "^VIX"),
    # ---- US futures: at 7 AM these matter more than yesterday's close ----
    ("Futures", "S&P 500 fut", "ES=F"),
    ("Futures", "Nasdaq 100 fut", "NQ=F"),
    ("Futures", "Dow fut", "YM=F"),
    # ---- Europe ----------------------------------------------------------
    ("Europe", "FTSE 100", "^FTSE"),
    ("Europe", "DAX", "^GDAXI"),
    ("Europe", "CAC 40", "^FCHI"),
    ("Europe", "Euro Stoxx 50", "^STOXX50E"),
    # ---- Asia ------------------------------------------------------------
    ("Asia", "Nikkei 225", "^N225"),
    ("Asia", "Shanghai Comp", "000001.SS"),
    ("Asia", "Hang Seng", "^HSI"),
    ("Asia", "KOSPI", "^KS11"),
    # ---- Energy and other commodities (front-month futures) -------------
    ("Energy", "Brent", "BZ=F"),
    ("Energy", "WTI", "CL=F"),
    ("Energy", "RBOB gasoline", "RB=F"),
    ("Energy", "Heating oil", "HO=F"),
    ("Energy", "Henry Hub gas", "NG=F"),
    ("Energy", "Dutch TTF gas", "TTF=F"),
    ("Energy", "Coal (Rotterdam API2)", "MTF=F"),
    ("Metals", "Gold", "GC=F"),
    ("Metals", "Copper", "HG=F"),
    # ---- FX: majors plus the currencies Hamza holds assets in -----------
    ("FX", "Dollar index", "DX-Y.NYB"),
    ("FX", "EUR/USD", "EURUSD=X"),
    ("FX", "GBP/USD", "GBPUSD=X"),
    ("FX", "USD/JPY", "JPY=X"),
    ("FX", "USD/CNY", "CNY=X"),
    ("FX", "USD/KRW", "KRW=X"),
    ("FX", "USD/PKR", "PKR=X"),
    ("FX", "USD/PHP", "PHP=X"),
    ("FX", "USD/MYR", "MYR=X"),
    ("FX", "USD/TRY", "TRY=X"),
]

# Ten-year (and a few other) government bond yields, quoted by CNBC.
# Each country lists candidate symbols; the first one that answers wins.
# LESSON: a list of fallbacks is a cheap way to survive a data vendor
# renaming a symbol. Whatever still fails is reported in brief_status and
# filled in by the morning write-up from the web, labelled as such.
INTL_YIELDS = [
    ("UK", "10Y", ["UK10Y-GB", "GB10Y"]),
    ("UK", "2Y", ["UK2Y-GB", "GB2Y"]),
    ("Germany", "10Y", ["DE10Y-DE", "DE10Y"]),
    ("Germany", "2Y", ["DE2Y-DE", "DE2Y"]),
    ("France", "10Y", ["FR10Y-FR", "FR10Y"]),
    ("Japan", "10Y", ["JP10Y-JP", "JP10Y"]),
    ("Japan", "2Y", ["JP2Y-JP", "JP2Y"]),
    ("China", "10Y", ["CN10Y-CN", "CN10Y"]),
    ("Australia", "10Y", ["AU10Y-AU", "AU10Y"]),
    ("South Korea", "10Y", ["KR10Y-KR", "KR10Y"]),
    ("Saudi Arabia", "10Y", ["SA10Y-SA", "SA10Y"]),
    ("UAE", "10Y", ["AE10Y-AE", "AE10Y"]),
    ("Pakistan", "10Y", ["PK10Y-PK", "PK10Y"]),
    ("Turkey", "10Y", ["TR10Y-TR", "TR10Y"]),
    ("Indonesia", "10Y", ["ID10Y-ID", "ID10Y"]),
]

# Weather: (label, latitude, longitude). Home first, then the capitals of
# the markets and regions the brief covers.
CITIES = [
    ("Robbinsville, NJ", 40.2148, -74.6193),
    ("New York (Midtown)", 40.7549, -73.9840),
    ("Washington", 38.9072, -77.0369),
    ("London", 51.5074, -0.1278),
    ("Paris", 48.8566, 2.3522),
    ("Berlin", 52.5200, 13.4050),
    ("Moscow", 55.7558, 37.6173),
    ("Kyiv", 50.4501, 30.5234),
    ("Riyadh", 24.7136, 46.6753),
    ("Abu Dhabi", 24.4539, 54.3773),
    ("Ankara", 39.9334, 32.8597),
    ("Islamabad", 33.6844, 73.0479),
    ("Beijing", 39.9042, 116.4074),
    ("Seoul", 37.5665, 126.9780),
    ("Tokyo", 35.6762, 139.6503),
    ("Jakarta", -6.2088, 106.8456),
    ("Canberra", -35.2809, 149.1300),
]

# Sheet tab names. The "Watchlist" tab is the one Hamza edits by hand.
TAB_WATCHLIST_INPUT = "Watchlist"
TAB_MARKETS = "brief_markets"
TAB_YIELDS = "brief_yields"
TAB_WEATHER = "brief_weather"
TAB_WATCH = "brief_watchlist"
TAB_STATUS = "brief_status"

# The Z-score window for pairs. 20 trading days matches the window found when
# the PM's spreadsheet was calibrated.
PAIR_Z_WINDOW = 20
