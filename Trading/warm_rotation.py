"""Cache-Warming für die Rotation / Correlation-Seite.

Berechnet die vier Dashboards des Rotation-Hubs (Global Rotation, Sector
Rotation, Fear & Greed — Correlation kommt bereits aus recalc_correlation.py)
einmal vor und legt die Ergebnisse in der persistenten Tages-Cache
(database/rotation_cache.db) ab. Damit zahlt der erste Seitenaufruf des Tages
nichts mehr: ``st.tabs`` rendert alle Tabs sofort, d.h. ohne Vorwärmung laufen
beim Öffnen der Seite sämtliche Berechnungen gleichzeitig an.

Per Scheduler nach dem morgendlichen Daten-Load aufrufen (analog
warm_market_stress.py und recalc_correlation.py).

Verwendung:
    python warm_rotation.py                  # alles (Default-Auswahl der UI)
    python warm_rotation.py /only:sector     # nur ein Bereich
    python warm_rotation.py /force           # bestehende Tages-Einträge verwerfen
    python warm_rotation.py /index:^SPX,^RUT # Fear & Greed nur für diese Indizes
"""
import datetime as dt
import json
import logging
import sys
import time

from tradinglib import rotation_cache as rc

logger = logging.getLogger("warm_rotation")

# Fear & Greed wird nur für die Indizes vorgewärmt, die die Seite anbietet.
# Der Default (erster Eintrag) ist der teure Erstaufruf; der Rest ist Zugabe.
_FG_INDICES = ["^SPX", "^RUT", "^GDAXI", "^MDAXI", "^SDAXI",
               "^N225", "^FTSE", "^IBEX", "^SSMI"]


def _run(label: str, key: str, fn, force: bool) -> tuple[bool, float]:
    """Compute one cache entry, honouring an existing hit unless *force*."""
    t0 = time.time()
    if not force and rc.get(key) is not None:
        logger.info("  %-34s  bereits gecacht", label)
        return True, 0.0
    try:
        value = fn()
    except Exception as exc:
        logger.warning("  %-34s  Fehler: %s", label, exc)
        return False, time.time() - t0
    if not rc._is_worth_caching(value):
        logger.warning("  %-34s  leeres Ergebnis (nicht gecacht)", label)
        return False, time.time() - t0
    rc.put(key, value)
    dt_s = time.time() - t0
    logger.info("  %-34s  %6.1fs", label, dt_s)
    return True, dt_s


def warm_sector(force: bool) -> tuple[int, int]:
    """Warm the Sector Rotation dashboard for each universe's UI defaults."""
    from tradinglib.sector_rotation import UNIVERSES, SectorRotation
    # _bench_options liefert die Benchmark-Liste der UI; deren erster Eintrag ist
    # die Vorauswahl (selectbox index=0). Von dort gelesen statt nachgebaut,
    # damit eine Umsortierung dort nicht am Cache vorbeiwärmt.
    from tradinglib.sector_rotation_page import _bench_options

    ok = total = 0
    # Die UI startet mit period='2y' (index=1) und include_pe=False.
    period, include_pe = "2y", False
    for name, uni in UNIVERSES.items():
        etfs_json    = json.dumps(uni["etfs"])
        weights_json = json.dumps(uni["weights"])
        benchmark    = _bench_options(name, uni["benchmark"])[0]
        key = rc.sector_key(benchmark, period, etfs_json, weights_json, include_pe)

        def _compute(uni=uni, benchmark=benchmark):
            rot = SectorRotation(sector_etfs=uni["etfs"], benchmark=benchmark,
                                 period=period, weights=uni["weights"])
            rot.fetch_all()
            return (rot.build_summary(include_pe=include_pe),
                    rot.calc_rrg_coordinates(tail_weeks=5),
                    rot.calc_rrg_coordinates_daily(tail_days=15))

        total += 1
        ok += _run(f"sector [{name}]", key, _compute, force)[0]
    return ok, total


def warm_stocks(force: bool) -> tuple[int, int]:
    """Warm the Best-of-Sector table for the sector the tab opens with."""
    from tradinglib.sector_stocks import (RANK_OPTIONS, SECTOR_ETF_MAP,
                                          get_available_sectors,
                                          query_sector_stocks, enrich_with_rsc)

    sectors = get_available_sectors()
    if not sectors:
        logger.warning("  %-34s  keine Sektoren gefunden", "best of sector")
        return 0, 0

    # Vorauswahl der UI: erster Sektor, erste Rank-Option, Top 20 (index=1),
    # RSC an, sobald für den Sektor ein ETF gemappt ist.
    sector   = sectors[0]
    rank_col = RANK_OPTIONS[list(RANK_OPTIONS)[0]]
    top_n    = 20
    etf      = SECTOR_ETF_MAP.get(sector, "")
    show_rsc = bool(etf)

    def _compute():
        df, debug = query_sector_stocks(sector=sector, rank_col=rank_col, limit=top_n)
        if not df.empty and show_rsc and etf:
            df = enrich_with_rsc(df, sector_etf=etf, weeks=4)
        return df, debug

    key = rc.stock_key(sector, rank_col, top_n, show_rsc, etf)
    ok = _run(f"best of sector [{sector}]", key, _compute, force)[0]
    return ok, 1


def warm_assessment(force: bool) -> tuple[int, int]:
    """Warm the dashboard's market assessment (incl. the per-sector signals).

    Teuerster Einzelposten des Dashboards: das Signal je Sektor-Titel laeuft
    ueber den Live-Pfad (FetchData + buy_sell), damit es sich mit dem Chart
    deckt. Vorgewaermt zahlt der erste Besucher das nicht.
    """
    from tradinglib import market_assessment as ma
    from tradinglib import system_config as sysconf

    # Default-Ticker und Nutzer wie im Dashboard; ohne hinterlegte Buy/Sell-Query
    # gibt es kein Signal, dann ist der Lauf entsprechend billig.
    users = _dashboard_users()
    ok = 0
    for user in users:
        try:
            ticker = (sysconf.SystemConfig(username=user)
                      .get_value("default_ticker", "^GDAXI")) or "^GDAXI"
        except Exception:
            ticker = "^GDAXI"
        key = rc.assessment_key(ticker, user)
        if force:
            # assess() cached selbst auf die Platte — ohne Loeschen wuerde
            # /force nur den bestehenden Eintrag zurueckschreiben.
            rc.drop(key)

        def _compute(t=ticker, u=user):
            # st.cache_data umgehen, damit ein zweiter Lauf im selben Prozess
            # nicht den RAM-Cache zurueckliefert.
            fn = getattr(ma.assess, "__wrapped__", ma.assess)
            return fn(t, dt.date.today().isoformat(), "database", u)

        ok += _run(f"assessment [{ticker} / {user}]", key, _compute, force)[0]
    return ok, len(users)


def _dashboard_users() -> list:
    """Nutzer, fuer die eine Marktlage vorgewaermt wird.

    Die Buy/Sell-Queries sind nutzer-scoped (``<user>:buy_query``), deshalb
    reicht ein Lauf fuer 'admin' nicht — gewaermt wird fuer jeden Nutzer mit
    hinterlegter Query, mindestens aber 'admin'.
    """
    users = set()
    try:
        from tradinglib.tools import Tools, open_db
        p = Tools().get_path(path='database', file_name='config.db')
        with open_db(p, readonly=True) as c:
            for (k,) in c.execute(
                    "SELECT key FROM config WHERE key LIKE '%:buy_query'"):
                u = str(k).split(':', 1)[0]
                if u and not u.startswith('_'):
                    users.add(u)
    except Exception as exc:
        logger.debug("Nutzerliste nicht ermittelbar: %s", exc)
    return sorted(users) or ['admin']


def warm_global(force: bool) -> tuple[int, int]:
    """Warm Global Rotation: equities, cross-asset and each per-class universe."""
    from tradinglib import global_rotation as gr

    jobs = [("global equities", rc.global_key("equities"), lambda: gr.compute()),
            ("global cross-asset", rc.global_key("all"),
             lambda: gr.compute(gr.ASSETS_ALL))]
    for uni in gr.UNIVERSES:
        jobs.append((f"global [{uni}]", rc.global_key(f"uni|{uni}"),
                     lambda uni=uni: gr.compute(gr.UNIVERSES[uni])))

    ok = 0
    for label, key, fn in jobs:
        ok += _run(label, key, fn, force)[0]
    return ok, len(jobs)


def warm_fear_greed(force: bool, indices: list[str]) -> tuple[int, int]:
    """Warm the Fear & Greed index for the given indices."""
    from tradinglib import fear_greed as fg

    ok = 0
    for idx in indices:
        ok += _run(f"fear_greed [{idx}]", rc.fear_greed_key(idx),
                   lambda idx=idx: fg.compute(idx), force)[0]
    return ok, len(indices)


def main(argv=None) -> None:
    """Precompute the rotation hub's dashboards into the persistent day cache."""
    argv = argv or sys.argv
    args = [a for a in argv[1:]]
    force = any(a.lower() in ('/force', '--force') for a in args)

    only = ''
    indices = list(_FG_INDICES)
    for a in args:
        low = a.lower()
        if low.startswith('/only:') or low.startswith('--only='):
            only = a.split(':', 1)[-1].split('=', 1)[-1].lower()
        elif low.startswith('/index:') or low.startswith('--index='):
            raw = a.split(':', 1)[-1] if ':' in a else a.split('=', 1)[-1]
            indices = [s.strip() for s in raw.split(',') if s.strip()]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sections = {
        'global':     lambda: warm_global(force),
        'sector':     lambda: warm_sector(force),
        'stocks':     lambda: warm_stocks(force),
        'fear_greed': lambda: warm_fear_greed(force, indices),
        'assessment': lambda: warm_assessment(force),
    }
    if only:
        if only not in sections:
            logger.error("Unbekannter Bereich %r — erlaubt: %s",
                         only, ", ".join(sections))
            return
        sections = {only: sections[only]}

    logger.info("Cache-Warming Rotation/Correlation (%s)%s",
                ", ".join(sections), " [force]" if force else "")
    t_all = time.time()
    ok = total = 0
    for name, fn in sections.items():
        o, n = fn()
        ok, total = ok + o, total + n

    removed = rc.purge_old(keep_days=3)
    logger.info("Fertig: %d/%d Einträge gecacht in %.1fs%s.",
                ok, total, time.time() - t_all,
                f" ({removed} alte Einträge entfernt)" if removed else "")


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    main()
