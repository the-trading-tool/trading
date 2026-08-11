"""The symbol sets the collector can scrape.

The name is the label as printed in the page's table; the headline selects the
section it lives in. Lookups are case-insensitive, so a site-side casing change
does not silently drop a symbol.
"""

INDICES = {
    "^GDAXI":    {"name": "DAX",                 "headline": "Indikation auf Indizes"},
    "^MDAXI":    {"name": "MDAX",                "headline": "Indikation auf Indizes"},
    "^SDAXI":    {"name": "SDAX",                "headline": "Indikation auf Indizes"},
    "^STOXX50E": {"name": "EURO STOXX 50",       "headline": "Indikation auf Indizes"},
    "^TECDAX":   {"name": "TecDAX",              "headline": "Indikation auf Indizes"},
    "^FTSE":     {"name": "FTSE 100",            "headline": "Indikation auf Indizes"},
    "^DJI":      {"name": "Dow Jones",           "headline": "Indikation auf Indizes"},
    "^SPX":      {"name": "S&P 500",             "headline": "Indikation auf Indizes"},
    "^HSI":      {"name": "Hang Seng",           "headline": "Indikation auf Indizes"},
    "^N225":     {"name": "NIKKEI 225",          "headline": "Indikation auf Indizes"},
    "GC=F":      {"name": "Goldpreis",           "headline": "Indikation auf Rohstoffe"},
    "SI=F":      {"name": "Silberpreis",         "headline": "Indikation auf Rohstoffe"},
    "BZ=F":      {"name": "Ölpreis (Brent)",     "headline": "Indikation auf Rohstoffe"},
    "EURUSD=X":  {"name": "Dollarkurs",
                  "headline": "Indikation auf Währungen und Wechselkurse"},
    "BUND-FUT":  {"name": "Euro-BUND-Future",    "headline": "Indikation auf Futures"},
}

DAX_MEMBERS = {
    "ADS.DE":  {"name": "adidas",                "headline": "Name"},
    "AIR.DE":  {"name": "Airbus",                "headline": "Name"},
    "ALV.DE":  {"name": "Allianz",               "headline": "Name"},
    "BAS.DE":  {"name": "BASF",                  "headline": "Name"},
    "BAYN.DE": {"name": "Bayer",                 "headline": "Name"},
    "BEI.DE":  {"name": "Beiersdorf",            "headline": "Name"},
    "BMW.DE":  {"name": "BMW",                   "headline": "Name"},
    "BNR.DE":  {"name": "Brenntag",              "headline": "Name"},
    "CBK.DE":  {"name": "Commerzbank",           "headline": "Name"},
    "CON.DE":  {"name": "Continental",           "headline": "Name"},
    "DTG.DE":  {"name": "Daimler Truck",         "headline": "Name"},
    "DBK.DE":  {"name": "Deutsche Bank",         "headline": "Name"},
    "DB1.DE":  {"name": "Deutsche Börse",        "headline": "Name"},
    "DTE.DE":  {"name": "Deutsche Telekom",      "headline": "Name"},
    "DHL.DE":  {"name": "DHL Group (ex Deutsche Post)", "headline": "Name"},
    "EOAN.DE": {"name": "E.ON",                  "headline": "Name"},
    "FME.DE":  {"name": "Fresenius Medical Care (FMC) St.", "headline": "Name"},
    "FRE.DE":  {"name": "Fresenius",             "headline": "Name"},
    "HNR1.DE": {"name": "Hannover Rück",         "headline": "Name"},
    "HEI.DE":  {"name": "Heidelberg Materials",  "headline": "Name"},
    "HEN.DE":  {"name": "Henkel vz.",            "headline": "Name"},
    "IFX.DE":  {"name": "Infineon",              "headline": "Name"},
    "MBG.DE":  {"name": "Mercedes-Benz Group (ex Daimler)", "headline": "Name"},
    "MRK.DE":  {"name": "Merck",                 "headline": "Name"},
    "MTX.DE":  {"name": "MTU Aero Engines",      "headline": "Name"},
    "MUV2.DE": {"name": "Münchener Rückversicherungs-Gesellschaft", "headline": "Name"},
    "P911.DE": {"name": "Porsche",               "headline": "Name"},
    "PAH3.DE": {"name": "Porsche Automobil vz.", "headline": "Name"},
    "QIA.DE":  {"name": "QIAGEN",                "headline": "Name"},
    "RHM.DE":  {"name": "Rheinmetall",           "headline": "Name"},
    "RWE.DE":  {"name": "RWE",                   "headline": "Name"},
    "SAP.DE":  {"name": "SAP",                   "headline": "Name"},
    "SRT3.DE": {"name": "Sartorius vz.",         "headline": "Name"},
    "SIE.DE":  {"name": "Siemens",               "headline": "Name"},
    "ENR.DE":  {"name": "Siemens Energy",        "headline": "Name"},
    "SHL.DE":  {"name": "Siemens Healthineers",  "headline": "Name"},
    "SY1.DE":  {"name": "Symrise",               "headline": "Name"},
    "VOW3.DE": {"name": "Volkswagen (VW) vz.",   "headline": "Name"},
    "VNA.DE":  {"name": "Vonovia",               "headline": "Name"},
    "ZAL.DE":  {"name": "Zalando",               "headline": "Name"},
}

SETS = {'indices': INDICES, 'members': DAX_MEMBERS}


def for_type(fetch_type):
    """Return the symbol set for a fetch type ('indices' or 'members')."""
    return SETS.get(fetch_type, INDICES)
