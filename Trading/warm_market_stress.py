"""Cache-Warming für den Market-Stress-Score je Index.

Berechnet compute_market_stress() einmal pro Index vor und füllt damit die
persistente Tages-Cache (database/regime_cache.db). Dadurch muss der Asset
Viewer den Score nicht beim ersten Aufruf des Tages live über bis ~1900 Member
berechnen (^RUT ≈ 123 s kalt) — alle Seitenaufrufe des Tages lesen ihn instant.

Per Scheduler nach dem morgendlichen Daten-Load aufrufen (compute_regimes liest
day_data aus yf_<ticker>.db, hängt also nicht an asset_perf2).

Verwendung:
    python warm_market_stress.py              # alle ^-Indizes aus yf_tickers.db
    python warm_market_stress.py ^RUT ^SPX    # nur diese
"""
import logging
import sys
import time

from tradinglib import tools
from tradinglib.tools import open_db
from tradinglib import regime_data_engine as rde

logger = logging.getLogger("warm_market_stress")


def list_indices() -> list[str]:
    """Alle echten Börsen-Indizes (^-Präfix) aus yf_tickers.db."""
    db = tools.Tools().get_path(path='database', file_name='yf_tickers.db')
    try:
        with open_db(db, readonly=True) as conn:
            rows = conn.execute(
                "SELECT name FROM indices WHERE name LIKE '^%' ORDER BY name"
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:
        logger.warning("Index-Liste konnte nicht geladen werden: %s", exc)
        return []


def main(argv=None) -> None:
    """Warm the per-day market-stress cache for the given (or all ^-) indices."""
    argv = argv or sys.argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    indices = [a for a in argv[1:] if a.startswith('^')] or list_indices()
    if not indices:
        logger.warning("Keine Indizes zum Vorberechnen gefunden.")
        return

    logger.info("Cache-Warming Market-Stress für %d Indizes: %s",
                len(indices), ", ".join(indices))
    t_all = time.time()
    ok = 0
    for idx in indices:
        t0 = time.time()
        try:
            res = rde.compute_market_stress(idx)
            if isinstance(res, dict) and res:
                ok += 1
                logger.info("  %-8s -> %6.1fs  (n=%s, score=%s, level=%s)",
                            idx, time.time() - t0, res.get('n'),
                            res.get('score'), res.get('level'))
            else:
                logger.info("  %-8s -> kein Ergebnis (%.1fs)", idx, time.time() - t0)
        except Exception as exc:
            logger.warning("  %-8s -> Fehler: %s", idx, exc)

    logger.info("Fertig: %d/%d Indizes gecacht in %.1fs.",
                ok, len(indices), time.time() - t_all)


if __name__ == '__main__':
    main()
