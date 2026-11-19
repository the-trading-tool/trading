"""
seed_db.py — erstellt database/yf_tickers.db mit Ticker-Daten.

Enthält:
  ^GDAXI      — DAX 40
  ^MDAXI      — MDAX 50
  ^SDAXI      — SDAX 70
  ^SPX        — S&P 500 (502 Titel)
  ^NDX        — NASDAQ-100 (103 Titel)
  ^DJI        — Dow Jones 30
  ETP         — UCITS-ETF/ETC-Auswahl (Xetra, 19 Titel)
  INDEX       — Index-Instrumente selbst (^GDAXI, ^GSPC …)
  COMMODITIES — Energie- und Agrar-Futures (BZ=F, ZW=F …)
  METALS      — Metall-Futures (GC=F, SI=F …)
  CURRENCIES  — Währungspaare (EURUSD=X …)
  CRYPTO      — Kryptowährungen (BTC-EUR, ETH-EUR …)

INDEX, COMMODITIES, METALS, CURRENCIES und CRYPTO sind Nicht-Aktien-Gruppen
(siehe tradinglib.tools.NON_STOCK_GROUPS) — sie werden von der Simulation
und vom Aktien-Download ausgeschlossen.

Indexmitglieder und die Gruppen INDEX/CURRENCIES/CRYPTO: Stand 2026-11-19,
uebernommen aus dem gepflegten Bestand (DAX/MDAX/SDAX/NDX/DJI per finanzen.net-Abgleich,
S&P 500 per sync_index_members.py). ETP bleibt eine kuratierte Auswahl.
Abweichend vom Bestand stehen TLT/HYG/LQD (dort ETP) zusaetzlich in INDEX: der
Fear & Greed braucht sie, und --init laedt die Gruppe INDEX, die Gruppe ETP nicht.

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

# DAX 40 (^GDAXI), Stand 2026-11-19
GDAXI = [
    'ADS.DE',  'AIR.PA',  'ALV.DE',  'BAS.DE',  'BAYN.DE', 'BEI.DE',
    'BMW.DE',  'BNR.DE',  'CBK.DE',  'CON.DE',  'DB1.DE',  'DBK.DE',
    'DHL.DE',  'DTE.DE',  'DTG.DE',  'ENR.DE',  'EOAN.DE', 'FME.DE',
    'FRE.DE',  'G1A.DE',  'G24.DE',  'HEI.DE',  'HEN3.DE', 'HNR1.DE',
    'HOT.DE',  'IFX.DE',  'MBG.DE',  'MRK.DE',  'MTX.DE',  'MUV2.DE',
    'QGEN',    'RHM.DE',  'RWE.DE',  'SAP.DE',  'SHL.DE',  'SIE.DE',
    'SY1.DE',  'VNA.DE',  'VOW3.DE', 'ZAL.DE',
]

# MDAX 50 (^MDAXI), Stand 2026-11-19
MDAXI = [
    '8TRA.DE', 'AG1.DE',  'AIXA.DE', 'AMV0.DE', 'AT1.DE',  'BC8.DE',
    'DEZ.DE',  'DHER.DE', 'DWS.DE',  'ELG.DE',  'EVD.DE',  'EVK.DE',
    'FNTN.DE', 'FPE3.DE', 'FRA.DE',  'FTK.DE',  'GBF.DE',  'HAG.DE',
    'HLE.DE',  'IOS.DE',  'JEN.DE',  'KBX.DE',  'KGX.DE',  'KRN.DE',
    'LEG.DE',  'LHA.DE',  'LXS.DE',  'NDA.DE',  'NDX1.DE', 'NEM.DE',
    'P911.DE', 'PAH3.DE', 'PUM.DE',  'R3NK.DE', 'RAA.DE',  'RRTL.DE',
    'SAX.DE',  'SDF.DE',  'SHA0.DE', 'SMHN.DE', 'SRT3.DE', 'SZG.DE',
    'TEG.DE',  'TKA.DE',  'TKMS.DE', 'TLX.DE',  'TUI1.DE', 'UTDI.DE',
    'WAF.DE',  'WCH.DE',
]

# SDAX 70 (^SDAXI), Stand 2026-11-19
SDAXI = [
    '1AST.DE', '1SXP.DE', '1U1.DE',  'ACT.DE',  'ADV.DE',  'AFX.DE',
    'AOF.DE',  'BFSA.DE', 'BOSS.DE', 'BSL.DE',  'COK.DE',  'CWC.DE',
    'DBAN.DE', 'DEQ.DE',  'DMP.DE',  'DOU.DE',  'DRW3.DE', 'DUE.DE',
    'EIN.DE',  'EKT.DE',  'EUZ.DE',  'EVT.DE',  'F3C.DE',  'FIE.DE',
    'GFT.DE',  'GLJ.DE',  'GYC.DE',  'HABA.DE', 'HBH.DE',  'HDD.DE',
    'HFG.DE',  'HYQ.DE',  'ILM1.DE', 'INH.DE',  'IXX.DE',  'JST.DE',
    'JUN3.DE', 'KSB3.DE', 'KTN.DE',  'KWS.DE',  'LPK.DE',  'MBB.DE',
    'MLP.DE',  'MUX.DE',  'NOEJ.DE', 'OBCK.DE', 'OHB.DE',  'PAT.DE',
    'PBB.DE',  'PCZ.DE',  'PNE3.DE', 'RDC.DE',  'S92.DE',  'SFQ.DE',
    'SGL.DE',  'SIX2.DE', 'SLYG.DE', 'SPG.DE',  'STM.DE',  'STO3.DE',
    'SZU.DE',  'TMV.DE',  'TNIE.DE', 'TPE.DE',  'V1NC.DE', 'VBK.DE',
    'VH2.DE',  'VOS.DE',  'WAC.DE',  'YSN.DE',
]

# S&P 500 (^SPX) — 502 Titel, Stand 2026-11-19
SPX = [
    'A',      'AAPL',   'ABBV',   'ABNB',   'ABT',    'ACGL',   'ACN',    'ADBE',
    'ADI',    'ADM',    'ADP',    'ADSK',   'AEE',    'AEP',    'AES',    'AFL',
    'AIG',    'AIZ',    'AJG',    'AKAM',   'ALB',    'ALGN',   'ALL',    'ALLE',
    'AMAT',   'AMCR',   'AMD',    'AME',    'AMGN',   'AMP',    'AMT',    'AMZN',
    'ANET',   'AON',    'AOS',    'APA',    'APD',    'APH',    'APO',    'APP',
    'APTV',   'ARE',    'ARES',   'ATO',    'AVGO',   'AVY',    'AWK',    'AXON',
    'AXP',    'AZO',    'BA',     'BAC',    'BALL',   'BAX',    'BBY',    'BDX',
    'BEN',    'BF-B',   'BG',     'BIIB',   'BKNG',   'BKR',    'BLDR',   'BLK',
    'BMY',    'BNY',    'BR',     'BRK-B',  'BRO',    'BSX',    'BX',     'BXP',
    'C',      'CAH',    'CARR',   'CASY',   'CAT',    'CB',     'CBOE',   'CBRE',
    'CCI',    'CCL',    'CDNS',   'CDW',    'CEG',    'CF',     'CFG',    'CHD',
    'CHRW',   'CHTR',   'CI',     'CIEN',   'CINF',   'CL',     'CLX',    'CMCSA',
    'CME',    'CMG',    'CMI',    'CMS',    'CNC',    'CNP',    'COF',    'COHR',
    'COIN',   'COO',    'COP',    'COR',    'COST',   'CPAY',   'CPRT',   'CPT',
    'CRH',    'CRL',    'CRM',    'CRWD',   'CSCO',   'CSGP',   'CSX',    'CTAS',
    'CTSH',   'CTVA',   'CVNA',   'CVS',    'CVX',    'D',      'DAL',    'DASH',
    'DD',     'DDOG',   'DE',     'DECK',   'DELL',   'DG',     'DGX',    'DHI',
    'DHR',    'DIS',    'DLR',    'DLTR',   'DOC',    'DOV',    'DOW',    'DPZ',
    'DRI',    'DTE',    'DUK',    'DVA',    'DVN',    'DXCM',   'EBAY',   'ECHO',
    'ECL',    'ED',     'EFX',    'EG',     'EIX',    'EL',     'ELV',    'EME',
    'EMR',    'EOG',    'EQIX',   'EQR',    'EQT',    'ERIE',   'ES',     'ESS',
    'ETN',    'ETR',    'EVRG',   'EW',     'EXC',    'EXE',    'EXPD',   'EXPE',
    'EXR',    'F',      'FANG',   'FAST',   'FCX',    'FDS',    'FDX',    'FDXF',
    'FE',     'FERG',   'FFIV',   'FICO',   'FIS',    'FISV',   'FITB',   'FIX',
    'FLEX',   'FOX',    'FOXA',   'FRT',    'FSLR',   'FTNT',   'FTV',    'GD',
    'GDDY',   'GE',     'GEHC',   'GEN',    'GEV',    'GILD',   'GIS',    'GL',
    'GLW',    'GM',     'GNRC',   'GOOG',   'GOOGL',  'GPC',    'GPN',    'GRMN',
    'GS',     'GWW',    'HAL',    'HAS',    'HBAN',   'HCA',    'HD',     'HIG',
    'HII',    'HLT',    'HON',    'HONA',   'HOOD',   'HPE',    'HPQ',    'HRL',
    'HSIC',   'HST',    'HSY',    'HUBB',   'HUM',    'HWM',    'IBKR',   'IBM',
    'ICE',    'IDXX',   'IEX',    'IFF',    'INCY',   'INTC',   'INTU',   'INVH',
    'IP',     'IQV',    'IR',     'IRM',    'ISRG',   'IT',     'ITW',    'IVZ',
    'J',      'JBHT',   'JBL',    'JCI',    'JKHY',   'JNJ',    'JPM',    'KDP',
    'KEY',    'KEYS',   'KHC',    'KIM',    'KKR',    'KLAC',   'KMB',    'KMI',
    'KO',     'KR',     'KVUE',   'L',      'LDOS',   'LEN',    'LH',     'LHX',
    'LII',    'LIN',    'LITE',   'LLY',    'LMT',    'LNT',    'LOW',    'LRCX',
    'LULU',   'LUV',    'LVS',    'LYB',    'LYV',    'MA',     'MAA',    'MAR',
    'MAS',    'MCD',    'MCHP',   'MCK',    'MCO',    'MDLZ',   'MDT',    'MET',
    'META',   'MGM',    'MKC',    'MLM',    'MMM',    'MNST',   'MO',     'MOS',
    'MPC',    'MPWR',   'MRK',    'MRNA',   'MRSH',   'MRVL',   'MS',     'MSCI',
    'MSFT',   'MSI',    'MTB',    'MTD',    'MU',     'NCLH',   'NDAQ',   'NDSN',
    'NEE',    'NEM',    'NFLX',   'NI',     'NKE',    'NOC',    'NOW',    'NRG',
    'NSC',    'NTAP',   'NTRS',   'NUE',    'NVDA',   'NVR',    'NWS',    'NWSA',
    'NXPI',   'O',      'ODFL',   'OKE',    'OMC',    'ON',     'ORCL',   'ORLY',
    'OTIS',   'OXY',    'PANW',   'PAYX',   'PCAR',   'PCG',    'PEG',    'PEP',
    'PFE',    'PFG',    'PG',     'PGR',    'PH',     'PHM',    'PKG',    'PLD',
    'PLTR',   'PM',     'PNC',    'PNR',    'PNW',    'PODD',   'PPG',    'PPL',
    'PRU',    'PSA',    'PSKY',   'PSX',    'PTC',    'PWR',    'PYPL',   'Q',
    'QCOM',   'RCL',    'REG',    'REGN',   'RF',     'RJF',    'RL',     'RMD',
    'ROK',    'ROL',    'ROP',    'ROST',   'RSG',    'RTX',    'RVTY',   'SBAC',
    'SBUX',   'SCHW',   'SHW',    'SJM',    'SLB',    'SMCI',   'SNA',    'SNDK',
    'SNPS',   'SO',     'SOLV',   'SPG',    'SPGI',   'SRE',    'STE',    'STLD',
    'STT',    'STX',    'STZ',    'SW',     'SWK',    'SWKS',   'SYF',    'SYK',
    'SYY',    'T',      'TAP',    'TDG',    'TDY',    'TECH',   'TEL',    'TER',
    'TFC',    'TGT',    'TJX',    'TKO',    'TMO',    'TMUS',   'TPL',    'TPR',
    'TRGP',   'TRMB',   'TROW',   'TRV',    'TSCO',   'TSLA',   'TSN',    'TT',
    'TTD',    'TTWO',   'TXN',    'TXT',    'TYL',    'UAL',    'UBER',   'UDR',
    'UHS',    'ULTA',   'UNH',    'UNP',    'UPS',    'URI',    'USB',    'V',
    'VEEV',   'VICI',   'VLO',    'VLTO',   'VMC',    'VRSK',   'VRSN',   'VRT',
    'VRTX',   'VST',    'VTR',    'VTRS',   'VZ',     'WAB',    'WAT',    'WBD',
    'WDAY',   'WDC',    'WEC',    'WELL',   'WFC',    'WM',     'WMB',    'WMT',
    'WRB',    'WSM',    'WST',    'WTW',    'WY',     'WYNN',   'XEL',    'XOM',
    'XYL',    'XYZ',    'YUM',    'ZBH',    'ZBRA',   'ZTS',
]

# NASDAQ-100 (^NDX) — 103 Titel, Stand 2026-11-19
NDX = [
    'AAPL',   'ABNB',   'ADBE',   'ADI',    'ADP',    'ADSK',   'AEP',    'ALAB',
    'ALNY',   'AMAT',   'AMD',    'AMGN',   'AMZN',   'ANT',    'APP',    'ARM',
    'ASML',   'AVGO',   'AXON',   'BKNG',   'BKR',    'CCEP',   'CDNS',   'CEG',
    'CMCSA',  'COST',   'CPRT',   'CRWD',   'CRWV',   'CSCO',   'CSX',    'CTAS',
    'DASH',   'DDOG',   'DXCM',   'EXC',    'FANG',   'FAST',   'FER',    'FTNT',
    'GEHC',   'GILD',   'GOOG',   'GOOGL',  'HON',    'HONA',   'IDXX',   'INTC',
    'INTU',   'ISRG',   'KDP',    'KHC',    'KLAC',   'LIN',    'LITE',   'LRCX',
    'MAR',    'MCHP',   'MDLZ',   'MELI',   'META',   'MNST',   'MPWR',   'MRNA',
    'MRVL',   'MSFT',   'MSTR',   'MU',     'NBIS',   'NFLX',   'NVDA',   'NXPI',
    'ODFL',   'ORLY',   'PANW',   'PAYX',   'PCAR',   'PDD',    'PEP',    'PLTR',
    'PYPL',   'QCOM',   'REGN',   'RKLB',   'ROP',    'ROST',   'SBUX',   'SHOP',
    'SNDK',   'SNPS',   'SPCX',   'STX',    'TER',    'TMUS',   'TRI',    'TSLA',
    'TTWO',   'TXN',    'VRTX',   'WDAY',   'WDC',    'WMT',    'XEL',
]

# Dow Jones Industrial Average (^DJI) — 30 Titel, Stand 2026-11-19
DJI = [
    'AAPL',  'AMGN',  'AMZN',  'AXP',   'BA',    'CAT',
    'CRM',   'CSCO',  'CVX',   'DIS',   'GOOGL', 'GS',
    'HD',    'HON',   'IBM',   'JNJ',   'JPM',   'KO',
    'MCD',   'MMM',   'MRK',   'MSFT',  'NKE',   'NVDA',
    'PG',    'SHW',   'TRV',   'UNH',   'V',     'WMT',
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
    '^AXJO',     # S&P/ASX 200
    '^BVSP',     # Ibovespa
    '^DJI',      # Dow Jones
    '^FCHI',     # CAC 40
    '^GDAXI',    # DAX 40
    '^HSI',      # Hang Seng
    '^IBEX',     # IBEX 35
    '^IXIC',     # NASDAQ Composite
    '^KS11',     # KOSPI Composite
    '^KS200',    # KOSPI 200
    '^MDAXI',    # MDAX
    '^MOVE',     # ICE BofA MOVE (Anleihe-Volatilitaet)
    '^N225',     # Nikkei 225
    '^NDX',      # NASDAQ-100
    '^NYA',      # NYSE Composite
    '^PUT',      # CBOE S&P 500 PutWrite
    '^RUT',      # Russell 2000
    '^SDAXI',    # SDAX
    '^SPX',      # S&P 500
    '^SSMI',     # Swiss Market Index
    '^STOXX50E', # Euro Stoxx 50
    '^TNX',      # 10-Year Treasury Yield
    '^TYX',      # 30-Year Treasury Yield
    '^VIX',      # CBOE Volatility Index
    '^VIX3M',    # CBOE 3-Month Volatility (Terminstruktur, Fear & Greed)
    '^VIXEQ',    # CBOE Equity VIX
    'DX-Y.NYB',  # US-Dollar-Index (auch in CURRENCIES)
    'HYG',       # iShares High Yield Corporate Bond ETF (Fear & Greed: Junk-Bonds)
    'IXG',       # iShares Global Financials ETF
    'LQD',       # iShares Investment Grade Corporate Bond ETF (Fear & Greed: Junk-Bonds)
    'TLT',       # iShares 20+ Year Treasury Bond ETF (Fear & Greed: Safe Haven)
    'ZN=F',      # 10Y US-Treasury-Note-Future (auch in COMMODITIES)
    'ZT=F',      # 2Y US-Treasury-Note-Future
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
    'AUDEUR=X',
    'BRLEUR=X',
    'CADEUR=X',
    'CHFEUR=X',
    'CNYEUR=X',
    'HKDEUR=X',
    'INREUR=X',
    'JPYEUR=X',
    'KRWEUR=X',
    'MXNEUR=X',
    'NOKEUR=X',
    'SEKEUR=X',
    'SGDEUR=X',
    'DX-Y.NYB',  # ICE US-Dollar-Index (Spot) — Makro-Kontext für den Korrelationsindex.
                 # NICHT 'DX' verwenden (das ist die Aktie Dynex Capital)!
]

# Kryptowährungen (werden von der Simulation ausgeschlossen)
CRYPTO = [
    'BTC-EUR',   # Bitcoin
    'ETH-EUR',   # Ethereum
    'BNB-EUR',   # BNB
    'DOGE-EUR',  # Dogecoin
    'SOL-EUR',   # Solana
    'TRX-EUR',   # Tron
    'XMR-EUR',   # Monero
    'XRP-EUR',   # XRP
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
    # Membership history: installed after the seed so the starting lists count
    # as "member since before tracking", not as entries dated today.
    try:
        from tradinglib import index_history
        index_history.ensure_schema(conn)
    except Exception as exc:
        print(f"   WARN: Indexhistorie nicht angelegt: {exc}")
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
