"""
seed_db.py — erstellt database/yf_tickers.db mit Ticker-Daten.

Enthält:
  ^GDAXI      — DAX 40 (Stand: 2026)
  ^MDAXI      — MDAX 50
  ^SDAXI      — SDAX 55
  ^SPX        — S&P 500 (503 Titel)
  ^NDX        — NASDAQ-100 (95 Titel)
  ^DJI        — Dow Jones 30
  ETP         — UCITS-ETF/ETC-Auswahl (Xetra, 19 Titel)
  INDEX       — Index-Instrumente selbst (^GDAXI, ^GSPC …)
  COMMODITIES — Energie- und Agrar-Futures (BZ=F, ZW=F …)
  METALS      — Metall-Futures (GC=F, SI=F …)
  CURRENCIES  — Währungspaare (EURUSD=X …)
  CRYPTO      — Kryptowährungen (BTC-EUR, ETH-EUR)

INDEX, COMMODITIES, METALS, CURRENCIES und CRYPTO sind Nicht-Aktien-Gruppen
(siehe tradinglib.tools.NON_STOCK_GROUPS) — sie werden von der Simulation
und vom Aktien-Download ausgeschlossen.

Aufruf: python seed_db.py [--dry-run]
"""

import sqlite3
import sys
import os
from datetime import datetime
from pathlib import Path
from tradinglib.tools import open_db

DRY_RUN = '--dry-run' in sys.argv

# ---------------------------------------------------------------------------
# Ticker-Listen
# ---------------------------------------------------------------------------

# DAX 40 (^GDAXI) — Stand 2026
GDAXI = [
    'ADS.DE',  'AIR.PA',  'ALV.DE',  'BAS.DE',  'BAYN.DE', 'BEI.DE',
    'BMW.DE',  'BNR.DE',  'CBK.DE',  'CON.DE',  'DB1.DE',  'DBK.DE',
    'DHL.DE',  'DTE.DE',  'DTG.DE',  'ENR.DE',  'EOAN.DE', 'FME.DE',
    'FRE.DE',  'G1A.DE',  'G24.DE',  'HEI.DE',  'HEN3.DE', 'HNR1.DE',
    'IFX.DE',  'MBG.DE',  'MRK.DE',  'MTX.DE',  'MUV2.DE', 'PAH3.DE',
    'QGEN',    'RHM.DE',  'RWE.DE',  'SAP.DE',  'SHL.DE',  'SIE.DE',
    'SY1.DE',  'VNA.DE',  'VOW3.DE', 'ZAL.DE',
]

# MDAX 50 (^MDAXI) — Stand 2026
MDAXI = [
    '8TRA.DE', 'AFX.DE',  'AG1.DE',  'AIXA.DE', 'AMV0.DE', 'AT1.DE',
    'BC8.DE',  'BFSA.DE', 'BOSS.DE', 'DHER.DE', 'DWNI.DE', 'DWS.DE',
    'EVD.DE',  'EVK.DE',  'FNTN.DE', 'FPE3.DE', 'FRA.DE',  'FTK.DE',
    'GBF.DE',  'GXI.DE',  'HAG.DE',  'HLE.DE',  'HOT.DE',  'IOS.DE',
    'JEN.DE',  'JUN3.DE', 'KBX.DE',  'KGX.DE',  'KRN.DE',  'LEG.DE',
    'LHA.DE',  'LXS.DE',  'NDA.DE',  'NDX1.DE', 'NEM.DE',  'PUM.DE',
    'RAA.DE',  'RDC.DE',  'RRTL.DE', 'SAX.DE',  'SDF.DE',  'STM.DE',
    'TEG.DE',  'TKA.DE',  'TKMS.DE', 'TLX.DE',  'TUI1.DE', 'UTDI.DE',
    'WAF.DE',  'WCH.DE',
]

# SDAX 55 (^SDAXI) — Stand 2026
SDAXI = [
    '1U1.DE',  'AAD.DE',  'ACT.DE',  'ADTN',    'ADV.DE',  'AG1.DE',
    'AOF.DE',  'BVB.DE',  'CEC.DE',  'COK.DE',  'CWC.F',   'DEQ.DE',
    'DEZ.DE',  'DMP.DE',  'DOU.F',   'DRW3.DE', 'DUE.DE',  'DWNI.DE',
    'DWS.DE',  'EKT.DE',  'ELG.DE',  'EUZ.DE',  'EVT.DE',  'F3C.DE',
    'FIE.DE',  'GFT.DE',  'GLJ.DE',  'GYC.DE',  'HBH.F',   'HDD.DE',
    'IOS.DE',  'JST.F',   'KCO.DE',  'KSB3.DE', 'KTN.DE',  'MLP.DE',
    'NCH2.F',  'NOEJ.F',  'PAT.DE',  'PNE3.DE', 'PSM.DE',  'S92.DE',
    'SBS.DE',  'SGL.DE',  'SHA0.F',  'SIX2.DE', 'SMHN.DE', 'STO3.F',
    'SZU.DE',  'TPE.DE',  'TTK.DE',  'VBK.DE',  'VOS.DE',  'WAC.DE',
    'WUW.DE',
]

# S&P 500 (^SPX) — 503 Titel, Stand 2026
SPX = [
    'A',      'AAPL',   'ABBV',   'ABNB',   'ABT',    'ACGL',   'ACN',    'ADBE',
    'ADI',    'ADM',    'ADP',    'ADSK',   'AEE',    'AEP',    'AES',    'AFL',
    'AIG',    'AIZ',    'AJG',    'AKAM',   'ALB',    'ALGN',   'ALL',    'ALLE',
    'AMAT',   'AMCR',   'AMD',    'AME',    'AMGN',   'AMP',    'AMT',    'AMZN',
    'ANET',   'AON',    'AOS',    'APA',    'APD',    'APH',    'APO',    'APP',
    'APTV',   'ARE',    'ATO',    'AVB',    'AVGO',   'AVY',    'AWK',    'AXON',
    'AXP',    'AZO',    'BA',     'BAC',    'BALL',   'BAX',    'BBY',    'BDX',
    'BEN',    'BG',     'BIIB',   'BK',     'BKNG',   'BKR',    'BLDR',   'BLK',
    'BMY',    'BR',     'BRO',    'BSX',    'BWA',    'BX',     'BXP',    'C',
    'CAG',    'CAH',    'CARR',   'CAT',    'CB',     'CBOE',   'CBRE',   'CCI',
    'CCL',    'CDNS',   'CDW',    'CE',     'CEG',    'CF',     'CFG',    'CHD',
    'CHRW',   'CHTR',   'CI',     'CINF',   'CL',     'CLX',    'CMCSA',  'CME',
    'CMG',    'CMI',    'CMS',    'CNC',    'CNP',    'COF',    'COIN',   'COO',
    'COP',    'COR',    'COST',   'CPAY',   'CPB',    'CPRT',   'CPT',    'CRL',
    'CRM',    'CRWD',   'CSCO',   'CSGP',   'CSX',    'CTAS',   'CTRA',   'CTSH',
    'CTVA',   'CVS',    'CVX',    'CZR',    'D',      'DAL',    'DASH',   'DD',
    'DDOG',   'DE',     'DECK',   'DELL',   'DG',     'DGX',    'DHI',    'DHR',
    'DIS',    'DLR',    'DLTR',   'DOC',    'DOV',    'DOW',    'DPZ',    'DRI',
    'DTE',    'DUK',    'DVA',    'DVN',    'DXCM',   'EA',     'EBAY',   'ECL',
    'ED',     'EFX',    'EG',     'EIX',    'EL',     'ELV',    'EME',    'EMN',
    'EMR',    'EOG',    'EPAM',   'EQIX',   'EQR',    'EQT',    'ERIE',   'ES',
    'ESS',    'ETN',    'ETR',    'EVRG',   'EW',     'EXC',    'EXE',    'EXPD',
    'EXPE',   'EXR',    'F',      'FANG',   'FAST',   'FCX',    'FDS',    'FDX',
    'FE',     'FFIV',   'FICO',   'FIS',    'FITB',   'FMC',    'FOX',    'FOXA',
    'FRT',    'FSLR',   'FTNT',   'FTV',    'GD',     'GDDY',   'GE',     'GEHC',
    'GEN',    'GEV',    'GILD',   'GIS',    'GL',     'GLW',    'GM',     'GNRC',
    'GOOG',   'GOOGL',  'GPC',    'GPN',    'GRMN',   'GS',     'GWW',    'HAL',
    'HAS',    'HBAN',   'HCA',    'HD',     'HIG',    'HII',    'HLT',    'HOLX',
    'HON',    'HOOD',   'HPE',    'HPQ',    'HRL',    'HSIC',   'HST',    'HSY',
    'HUBB',   'HUM',    'HWM',    'IBKR',   'IBM',    'ICE',    'IDXX',   'IEX',
    'IFF',    'INCY',   'INTC',   'INTU',   'INVH',   'IP',     'IQV',    'IR',
    'IRM',    'ISRG',   'IT',     'ITW',    'IVZ',    'J',      'JBHT',   'JBL',
    'JCI',    'JKHY',   'JNJ',    'JPM',    'KDP',    'KEY',    'KEYS',   'KHC',
    'KIM',    'KKR',    'KLAC',   'KMB',    'KMI',    'KMX',    'KO',     'KR',
    'KVUE',   'L',      'LDOS',   'LEN',    'LH',     'LHX',    'LII',    'LIN',
    'LKQ',    'LLY',    'LMT',    'LNT',    'LOW',    'LRCX',   'LULU',   'LUV',
    'LVS',    'LW',     'LYB',    'LYV',    'MA',     'MAA',    'MAR',    'MAS',
    'MCD',    'MCHP',   'MCK',    'MCO',    'MDLZ',   'MDT',    'MET',    'META',
    'MGM',    'MHK',    'MKC',    'MKTX',   'MLM',    'MMM',    'MNST',   'MO',
    'MOH',    'MOS',    'MPC',    'MPWR',   'MRK',    'MRNA',   'MS',     'MSCI',
    'MSFT',   'MSI',    'MTB',    'MTCH',   'MTD',    'MU',     'NCLH',   'NDAQ',
    'NDSN',   'NEE',    'NEM',    'NFLX',   'NI',     'NKE',    'NOC',    'NOW',
    'NRG',    'NSC',    'NTAP',   'NTRS',   'NUE',    'NVDA',   'NVR',    'NWS',
    'NWSA',   'NXPI',   'O',      'ODFL',   'OKE',    'OMC',    'ON',     'ORCL',
    'ORLY',   'OTIS',   'OXY',    'PANW',   'PAYC',   'PAYX',   'PCAR',   'PCG',
    'PEG',    'PEP',    'PFE',    'PFG',    'PG',     'PGR',    'PH',     'PHM',
    'PKG',    'PLD',    'PLTR',   'PM',     'PNC',    'PNR',    'PNW',    'PODD',
    'POOL',   'PPG',    'PPL',    'PRU',    'PSA',    'PSKY',   'PSX',    'PTC',
    'PWR',    'PYPL',   'QCOM',   'QRVO',   'RCL',    'REG',    'REGN',   'RF',
    'RJF',    'RL',     'RMD',    'ROK',    'ROL',    'ROP',    'ROST',   'RSG',
    'RTX',    'RVTY',   'SBAC',   'SBUX',   'SCHW',   'SHW',    'SJM',    'SLB',
    'SMCI',   'SNA',    'SNPS',   'SO',     'SOLV',   'SPG',    'SPGI',   'SRE',
    'STE',    'STLD',   'STT',    'STX',    'STZ',    'SW',     'SWK',    'SWKS',
    'SYF',    'SYK',    'SYY',    'T',      'TAP',    'TDG',    'TDY',    'TECH',
    'TEL',    'TER',    'TFC',    'TFX',    'TGT',    'TJX',    'TKO',    'TMO',
    'TMUS',   'TPL',    'TPR',    'TRGP',   'TRMB',   'TROW',   'TRV',    'TSCO',
    'TSLA',   'TSN',    'TT',     'TTD',    'TTWO',   'TXN',    'TXT',    'TYL',
    'UAL',    'UBER',   'UDR',    'UHS',    'ULTA',   'UNH',    'UNP',    'UPS',
    'URI',    'USB',    'V',      'VICI',   'VLO',    'VLTO',   'VMC',    'VRSK',
    'VRSN',   'VRTX',   'VST',    'VTR',    'VTRS',   'VZ',     'WAB',    'WAT',
    'WBD',    'WDAY',   'WDC',    'WEC',    'WELL',   'WFC',    'WM',     'WMB',
    'WMT',    'WRB',    'WSM',    'WST',    'WTW',    'WY',     'WYNN',   'XEL',
    'XOM',    'XYL',    'XYZ',    'YUM',    'ZBH',    'ZBRA',   'ZTS',
]

# NASDAQ-100 (^NDX) — 95 Titel, Stand 2026
NDX = [
    'AAPL',   'ABNB',   'ADBE',   'ADI',    'ADP',    'ADSK',   'AEP',    'AMAT',
    'AMD',    'AMGN',   'AMZN',   'APP',    'ARM',    'ASML.AS','AVGO',   'AZN',
    'BIIB',   'BKNG',   'BKR',    'CDNS',   'CDW',    'CEG',    'CHTR',   'CMCSA',
    'COST',   'CPRT',   'CRWD',   'CSCO',   'CSGP',   'CSX',    'CTAS',   'CTSH',
    'DASH',   'DDOG',   'DXCM',   'EA',     'EXC',    'FANG',   'FAST',   'GEHC',
    'GFS',    'GILD',   'GOOG',   'GOOGL',  'HON',    'IDXX',   'ILMN',   'INTC',
    'INTU',   'ISRG',   'KDP',    'KHC',    'KLAC',   'LIN',    'LRCX',   'LULU',
    'MAR',    'MCHP',   'MDLZ',   'MELI',   'META',   'MNST',   'MRNA',   'MRVL',
    'MSFT',   'MU',     'NFLX',   'NVDA',   'NXPI',   'ODFL',   'ON',     'ORLY',
    'PANW',   'PAYX',   'PCAR',   'PDD',    'PEP',    'PYPL',   'QCOM',   'REGN',
    'ROP',    'ROST',   'SBUX',   'SNPS',   'SPCX',   'TEAM',   'TMUS',   'TSLA',
    'TXN',    'VRSK',   'VRTX',   'WBD',    'WDAY',   'XEL',    'ZS',
]

# Dow Jones Industrial Average (^DJI) — 30 Titel, Stand 2026
DJI = [
    'AAPL',  'AMGN',  'AMZN',  'AXP',   'BA',    'CAT',
    'CRM',   'CSCO',  'CVX',   'DIS',   'GS',    'HD',
    'HON',   'IBM',   'JNJ',   'JPM',   'KO',    'MCD',
    'MMM',   'MRK',   'MSFT',  'NKE',   'NVDA',  'PG',
    'SHW',   'TRV',   'UNH',   'V',     'VZ',    'WMT',
]

# UCITS-ETFs/ETCs (Xetra, alle in EUR) — validiert gegen Yahoo Finance 2026-06-11
ETP = [
    # Welt / Regionen (Kern)
    'EUNL.DE',  # iShares Core MSCI World (Acc)
    'VWCE.DE',  # Vanguard FTSE All-World (Acc)
    'SXR8.DE',  # iShares Core S&P 500 (Acc)
    'EXS1.DE',  # iShares Core DAX
    'EXSA.DE',  # iShares STOXX Europe 600 (Dist)
    'LYP6.DE',  # Amundi Core Stoxx Europe 600 (Acc)
    'EUNM.DE',  # iShares MSCI EM (Acc)
    'XDWD.DE',  # Xtrackers MSCI World 1C
    # Sektor / Thema / Nasdaq
    'EXXT.DE',  # iShares NASDAQ-100 (DE)
    'SXRV.DE',  # iShares NASDAQ 100 (Acc)
    'XDWT.DE',  # Xtrackers MSCI World Information Technology
    'EXV1.DE',  # iShares STOXX Europe 600 Banks
    '2B76.DE',  # iShares Automation & Robotics
    # Dividenden / Anleihen / Gold / Geldmarkt
    'ISPA.DE',  # iShares STOXX Global Select Dividend 100
    'EXSB.DE',  # iShares DivDAX
    'EUNA.DE',  # iShares Core Global Aggregate Bond EUR Hedged
    'EXX6.DE',  # iShares eb.rexx Government Germany 10.5+yr
    '4GLD.DE',  # Xetra-Gold
    'XEON.DE',  # Xtrackers II EUR Overnight Rate Swap (Geldmarkt)
]

# Index-Instrumente (werden von der Simulation ausgeschlossen)
INDEX = [
    '^DJI',      # Dow Jones
    '^FCHI',     # CAC 40
    '^GDAXI',    # DAX 40
    '^HSI',      # Hang Seng
    '^IBEX',     # IBEX 35
    '^IXIC',     # NASDAQ Composite
    '^MDAXI',    # MDAX
    '^N225',     # Nikkei 225
    '^NDX',      # NASDAQ-100
    '^NYA',      # NYSE Composite
    '^SDAXI',    # SDAX
    '^SPX',      # S&P 500
    '^SSMI',     # Swiss Market Index
    '^STOXX50E', # Euro Stoxx 50
    '^TNX',      # 10-Year Treasury Yield
    '^VIX',      # CBOE Volatility Index
]

# Energie-, Agrar- und Finanz-Futures (werden von der Simulation ausgeschlossen)
COMMODITIES = [
    'BZ=F',  # Brent Crude Oil
    'CC=F',  # Kakao
    'CT=F',  # Baumwolle
    'HO=F',  # Heating Oil
    'KC=F',  # Kaffee
    'LE=F',  # Live Cattle
    'NG=F',  # Natural Gas
    'RB=F',  # Gasoline (RBOB)
    'SB=F',  # Zucker
    'ZC=F',  # Mais
    'ZN=F',  # 10Y US-Treasury-Note-Future (Makro-Kontext für den Korrelationsindex;
             # als Nicht-Aktien-Future hier im Futures-Bucket, damit ihn die
             # YAHOO_*_DATA_GROUP-Jobs mitladen)
    'ZS=F',  # Sojabohnen
    'ZW=F',  # Weizen
]

# Metall-Futures (werden von der Simulation ausgeschlossen)
METALS = [
    'GC=F',  # Gold
    'HG=F',  # Kupfer
    'PA=F',  # Palladium
    'PL=F',  # Platin
    'SI=F',  # Silber
]

# Währungspaare + US-Dollar-Index (werden von der Simulation ausgeschlossen)
CURRENCIES = [
    'EURUSD=X',
    'GBPUSD=X',
    'HKDEUR=X',
    'JPYEUR=X',
    'DX-Y.NYB',  # ICE US-Dollar-Index (Spot) — Makro-Kontext für den Korrelationsindex.
                 # NICHT 'DX' verwenden (das ist die Aktie Dynex Capital)!
]

# Kryptowährungen (werden von der Simulation ausgeschlossen)
CRYPTO = [
    'BTC-EUR',  # Bitcoin
    'ETH-EUR',  # Ethereum
]

ALL_GROUPS = {
    '^GDAXI':      GDAXI,
    '^MDAXI':      MDAXI,
    '^SDAXI':      SDAXI,
    '^SPX':        SPX,
    '^NDX':        NDX,
    '^DJI':        DJI,
    'ETP':         ETP,
    'INDEX':       INDEX,
    'COMMODITIES': COMMODITIES,
    'METALS':      METALS,
    'CURRENCIES':  CURRENCIES,
    'CRYPTO':      CRYPTO,
}

# ---------------------------------------------------------------------------
# DB-Pfad — analog zu tools.get_path()
# ---------------------------------------------------------------------------

def get_db_path() -> str:
    tdb = os.environ.get('TradingDB', '')
    if tdb:
        base = Path(tdb)
    else:
        base = Path(sys.argv[0]).resolve().parent / 'database'
    base.mkdir(exist_ok=True)
    return str(base / 'yf_tickers.db')


# ---------------------------------------------------------------------------
# Schema + Seed
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    Ticker   TEXT NOT NULL,
    Date     TEXT,
    INVESTED REAL,
    ISIN     TEXT,
    UNIQUE(Ticker)
);

CREATE TABLE IF NOT EXISTS indices (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_indices (
    stock_id  INTEGER NOT NULL,
    index_id  INTEGER NOT NULL,
    PRIMARY KEY (stock_id, index_id),
    FOREIGN KEY (stock_id)  REFERENCES stocks(id),
    FOREIGN KEY (index_id)  REFERENCES indices(id)
);
"""


def ensure_unique_schema(conn: sqlite3.Connection):
    """Self-healing: dedupe legacy rows, then enforce uniqueness via indexes.

    Older yf_tickers.db were created before the UNIQUE(Ticker) / PK(stock_id,index_id)
    constraints existed in SCHEMA. On those tables `INSERT OR IGNORE` could not dedupe,
    so every seed run appended duplicate stocks/links. This migration removes existing
    duplicates and adds UNIQUE indexes so future INSERT OR IGNORE truly dedupes.
    Idempotent — a no-op once the indexes exist and no duplicates remain (analogous to
    the trades.db PK self-heal).
    """
    c = conn.cursor()
    dup_links = c.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM stock_indices "
        "GROUP BY stock_id, index_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    dup_stocks = c.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM stocks WHERE Ticker IS NOT NULL "
        "GROUP BY Ticker HAVING COUNT(*) > 1)"
    ).fetchone()[0]

    if dup_links or dup_stocks:
        # Repoint valid links to the canonical (min id) stocks row per ticker.
        # Skip already-orphaned links (stock_id with no stocks row) so we don't NULL them.
        c.execute(
            "UPDATE stock_indices SET stock_id = ("
            "  SELECT MIN(s2.id) FROM stocks s2 "
            "  WHERE s2.Ticker = (SELECT s1.Ticker FROM stocks s1 WHERE s1.id = stock_indices.stock_id)) "
            "WHERE EXISTS (SELECT 1 FROM stocks s WHERE s.id = stock_indices.stock_id)"
        )
        c.execute(
            "DELETE FROM stock_indices WHERE rowid NOT IN "
            "(SELECT MIN(rowid) FROM stock_indices GROUP BY stock_id, index_id)"
        )
        c.execute(
            "DELETE FROM stocks WHERE Ticker IS NOT NULL AND id NOT IN "
            "(SELECT MIN(id) FROM stocks WHERE Ticker IS NOT NULL GROUP BY Ticker)"
        )
        print(f"   Dubletten bereinigt: {dup_stocks} Ticker, {dup_links} Verknüpfungen")

    # Enforce uniqueness going forward. SQLite treats multiple NULLs as distinct, so a
    # NULL Ticker will not block the index. IF NOT EXISTS keeps this idempotent and
    # harmless even when the table already carries a UNIQUE(...) table constraint.
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stocks_ticker ON stocks(Ticker)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_indices_pair "
                  "ON stock_indices(stock_id, index_id)")
    except sqlite3.IntegrityError as exc:
        print(f"   WARN: UNIQUE-Index konnte nicht angelegt werden (Restduplikate?): {exc}")
    conn.commit()


def seed(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)
    ensure_unique_schema(conn)

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted = 0
    skipped  = 0

    for index_name, tickers in ALL_GROUPS.items():
        cursor.execute('INSERT OR IGNORE INTO indices (name) VALUES (?)', (index_name,))
        cursor.execute('SELECT id FROM indices WHERE name = ?', (index_name,))
        index_id = cursor.fetchone()[0]

        for ticker in tickers:
            cursor.execute(
                'INSERT OR IGNORE INTO stocks (Ticker, Date) VALUES (?, ?)',
                (ticker, date_str),
            )
            cursor.execute('SELECT id FROM stocks WHERE Ticker = ?', (ticker,))
            stock_id = cursor.fetchone()[0]

            cursor.execute(
                'INSERT OR IGNORE INTO stock_indices (stock_id, index_id) VALUES (?, ?)',
                (stock_id, index_id),
            )

            if cursor.rowcount:
                inserted += 1
            else:
                skipped += 1

    conn.commit()
    return inserted, skipped


def get_config_db_path() -> str:
    tdb = os.environ.get('TradingDB', '')
    if tdb:
        base = Path(tdb)
    else:
        base = Path(sys.argv[0]).resolve().parent / 'database'
    base.mkdir(exist_ok=True)
    return str(base / 'config.db')


def seed_config(conn: sqlite3.Connection):
    """Seed config.db with FMP as the default data provider (free-version default)."""
    import json
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
        ("_app:data_provider", json.dumps("fmp")),
    )
    conn.commit()


def main():
    db_path = get_db_path()
    total   = sum(len(v) for v in ALL_GROUPS.values())
    print(f"Ziel-DB : {db_path}")
    print(f"Ticker  : {total} in {len(ALL_GROUPS)} Indizes")

    if DRY_RUN:
        print("[dry-run] Keine Änderungen geschrieben.")
        for name, tickers in ALL_GROUPS.items():
            print(f"  {name:12s} ({len(tickers):3d}): {', '.join(tickers[:5])} …")
        return

    with open_db(db_path) as conn:
        inserted, skipped = seed(conn)

    print(f"Fertig — {inserted} neu eingetragen, {skipped} bereits vorhanden.")

    cfg_path = get_config_db_path()
    print(f"Config-DB: {cfg_path}")
    with open_db(cfg_path) as conn:
        seed_config(conn)
    print("Config geseedet — Standard-Provider: FMP.")


if __name__ == '__main__':
    main()
