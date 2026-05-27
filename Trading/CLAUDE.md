# Trading App — Claude Context

Dieses Dokument ist die Übergabe zwischen Claude-Sessions.
Beim Start einer neuen Session: kurz lesen, dann loslegen.

---

## Projektübersicht

Streamlit-basierte lokale Trading-App (noch nicht produktiv).

**Einstiegspunkt:** `asset_analyzer.py` (Hauptapp), läuft via `streamlit run asset_analyzer.py`

**Kern-Module:**
| Datei | Zweck |
|---|---|
| `tradinglib/tools.py` | Basis-Utilities: `Tools`, `Db_tools` (SQLite-Wrapper), `St_tools`, `ExpressionEvaluator*`, `BuySellSignalGenerator` |
| `tradinglib/fetch_data.py` | Lokale OHLC-Daten aus SQLite laden + Yahoo-Finance-Fallback |
| `tradinglib/market_data.py` | Zentraler yfinance-Wrapper mit Caching und lokalem DB-Fallback |
| `tradinglib/ticker_tools.py` | Ticker-Daten, `OHLCQueryPlanner`, Yahoo-Download |
| `tradinglib/system_config.py` | App-Konfiguration via `config.db` (erbt von `Db_tools`) |
| `tradinglib/multi_transaction.py` | Portfolio-Transaktionslogik, Buy/Sell-Signale |
| `tradinglib/scheduler.py` | Job-Scheduler mit SQLite-Backend |
| `tradinglib/indicator/` | 36 technische Indikatoren (ewo, rsi, macd, …) |
| `tradinglib/utils.py` | `DataUtils`: Tabellennamen, OHLC-Save, Exchange-Rates |

**Hilfsskripte (CLI):**
- `get_asset_data.py` — Preisdaten von Yahoo holen und lokal speichern
- `get_asset_info.py` — Asset-Metadaten laden
- `asset_perf2.py` — Performance-Simulation
- `schedserver.py` — Scheduler als Daemon

**Datenbanken (alle lokal unter `./database/`):**
- `asset_info.db` — Ticker-Stammdaten
- `yf_<TICKER>.db` — OHLC-Preisdaten je Ticker (Tabellen: min_data, h60_data, day_data, week_data, month_data)
- `asset_simulation_.db` — Berechnete Performance-Werte (neue Namenskonvention mit `_`-Trenner; ältere Versionen nutzen noch die Namen ohne `_`). WAL-Modus aktiv → parallele Lese-/Schreibzugriffe ohne Lock-Konflikte. `asset_performance_.db` wird nicht mehr benötigt.
- `config.db` — App-Konfiguration (Key-Value)
- `scheduler.db` — Scheduler-Jobs

---

## Abgeschlossene PRs

| PR | Branch | Inhalt |
|---|---|---|
| #1 | `add-readme` | README.md + .gitignore erstellt |
| #2 | `fix/db-connection-safety` | `__enter__`/`__exit__` auf `Db_tools`; `try/finally` in `fetch_data.py`, `utils.py`; `with Db_tools` in `search.py`; parameterisierte Queries in `fetch_data.py` |
| #3 | `fix/code-quality` | SQL-Injection (f-String → `?` params) in `fetch_data.py`, `excel_executor.py`, `multi_transaction.py`; Cache-Begrenzung (512 Einträge) in `market_data.py`; alle `except:` → `except Exception:` in 60 Dateien |
| #4 | `feature/pine-export-and-portfolio-analysis` | Pine Script v5-Exporter (22 Templates, `pine_exporter.py`); Indikator-`params`-Schema (22 Klassen); ⚙-Config-Dialog in `system_config.py`; `MultiCheckboxSelector` lädt Config-Defaults; Markov-Regime-Indikator; Portfolio-Analyse-Tab auf "Own Transactions"-Seite |
| (WIP) | `refactor/indicator-naming` | Einheitliches `{prefix}_{name}`-Schema für alle ~30 Indikatoren; DB-Dateien auf `_`-Trenner umgestellt; pdict-Keys, buy/sell-Query-Defaults, pine_exporter, live_ticker, performance_details synchronisiert. `macd_signal` in pdict. `--backfill` + `--rescore` Fast-Modes in `asset_perf2.py`. SQLite WAL-Modus auf `asset_simulation_.db`; `asset_performance_.db` entfernt. Siehe Naming-Tabelle unten. |
| (WIP) | `feature/sector-rotation` | Sector Rotation Dashboard: `tradinglib/sector_rotation.py` (Backend: SectorRotation, RSC/CMF/OBV/RRG-Indikatoren, US+EU+EM-Universen, Industry-Drill-down); `tradinglib/sector_rotation_page.py` (6-Tab-UI: RRG Weekly, RRG Daily, Treemap, Sector Matrix, Industry Drill-down, Best of Sector); `tradinglib/sector_stocks.py` (DB-Query für Top-Stocks je Sektor + RSC vs ETF Anreicherung); Route in `asset_analyzer.py` via `?rotation=true` |

---

## Indikator-Spalten-Namenskonvention

Regel: `{indikator_prefix}_{semantischer_name}` in `snake_case`.
Gilt für: DataFrame-Spalten, pdict-Keys (= DB-Spalten), buy/sell-Queries.

| Alte(r) Name(n) | Neuer Name | Indikator |
|---|---|---|
| `RelVol_Ratio`, `RelVol_Current`, `RelVol_Past`, `AdjVolume` | `relvol_ratio`, `relvol_current`, `relvol_past`, `relvol_adj_vol` | relvol |
| `plus_di`, `minus_di` | `adx_plus_di`, `adx_minus_di` | adx |
| `stoch` (aus rsi.py) | `rsi_momentum` | rsi |
| `dema_buy_signal`, `dema_sell_signal` | `dema_buy`, `dema_sell` | dema |
| `Resistance`, `Support` | `sup_resistance`, `sup_support` | sup |
| `ewoEma` | `ewo_ema` | ewo |
| `ema_ha_high`, `ema_ha_low`, `HA_Close`, `HA_Open` | `ha_ema_high`, `ha_ema_low`, `ha_close`, `ha_open` | heikin |
| `horcrux` | `hor_val` | hor |
| `ATR` (in bos.py) | `bos_atr` | bos |
| `ATR`, `BB_width`, `Phase`, `Signal`, `HighTF_Phase` (in mmm.py) | `mmm_atr`, `mmm_bb_width`, `mmm_phase`, `mmm_signal`, `mmm_htf_phase` | mmm |
| `Delta`, `CumDelta` (in cumd.py) | `cumd_delta`, `cumd_cum_delta` | cumd |
| `Delta`, `CumDelta`, `CumDelta_SMA` (in vol.py) | `vol_delta`, `vol_cum_delta`, `vol_cum_delta_sma` | vol |
| `Delta`, `CumDelta` (in mmm.py) | `mmm_delta`, `mmm_cum_delta` | mmm |
| `upperDon`, `lowerDon`, `midDon` | `don_upper`, `don_lower`, `don_mid` | don |
| `bbBbm{n}`, `bbBbh{n}`, `bbBbl{n}` | `bol_mid_{n}`, `bol_upper_{n}`, `bol_lower_{n}` | bol |
| `gan_R{i}`, `gan_S{i}` | `gan_r{i}`, `gan_s{i}` | gan |
| `Price Change`, `Buy Volume`, `Sell Volume`, etc. (oft.py) | `oft_price_change`, `oft_buy_vol`, `oft_sell_vol`, etc. | oft |

**DB-Dateinamen** (ab Branch `refactor/indicator-naming`):
- `asset_performance.db` → `asset_performance_.db`
- `asset_simulation.db` → `asset_simulation_.db`
- `asset_simulation{year}.db` → `asset_simulation_{year}.db`
- `asset_simulationall.db` → `asset_simulation_all.db`

---

## Bekannte Probleme & Backlog

### Prio 1 — Sicherheit (vor Produktiv-Einsatz zwingend)

**A) `eval()` auf Benutzereingaben (Code-Injection-Risiko)**

Wer die Config-DB schreiben kann (z.B. über den SQLite-Editor in der App), kann
beliebigen Python-Code ausführen.

| Datei | Zeile | Problem |
|---|---|---|
| `asset_analyzer.py` | 336 | `tickers = eval(ti)` |
| `banner_page.py` | 43 | `eval(sys_conf.get_value('multi_transactions', …))` |
| `multi_transaction.py` | 404, 504, 508 | `eval()` auf Buy/Sell-Bedingungen aus Config-DB |
| `system_config.py` | 119, 132, 158 | `eval()` auf Overlay/Oszillator-Werte aus DB |
| `indicator/indicator.py` | 123, 130 | `eval()` auf transformierte Ausdrücke |
| `tools.py` (BuySellSignalGenerator) | 718, 760, 768 | Fallback-`eval()` auf rohe Sell-Conditions |

**Fix-Ansatz:** Config-Werte als JSON speichern (`json.loads` statt `eval`). Der
`ExpressionEvaluatorNew.validate_and_transform()` ist bereits vorhanden — kein
Fallback auf bare `eval()` mehr verwenden.

**B) `pd.json.loads` existiert nicht (`fetch_data.py:113`)**
```python
# Falsch — pd hat kein json-Modul:
df[col] = df[col].apply(pd.json.loads)
# Richtig:
df[col] = df[col].apply(json.loads)
```
Fehler wird durch `except Exception: pass` verschluckt → JSON-Spalten in
`asset_info` werden **nie deserialisiert** (stilles Datenproblem).

---

### Prio 2 — Stabilität (kurzfristig)

**C) sqlite3-Verbindungen ohne garantiertes `.close()` bei Exceptions**

Viele Klassen öffnen Connections in `__init__` ohne `try/finally`:
- `asset_simulator.py:417` — `self.ticker_conn`, `self.info_conn`
- `market_map.py:28,55` — `self.ticker_conn`, `self.info_conn`
- `performance_details.py:69,95,211` — `self.ticker_conn`, `self.info_conn`
- `ticker_tools.py:315,411` — lokale `conn` ohne `try/finally`
- `live_ticker.py:89,106,208,234,245` — alle ohne `try/finally`
- `search.py:31,212` — ohne `try/finally`
- `sqlite_editor.py:31,53,70,95` — ohne `try/finally`

**Fix:** `with sqlite3.connect(...) as conn:` oder `try/finally: conn.close()`

**D) `DataUtils._xrate_cache` ist unbegrenzt**

Klasssen-Variable in `utils.py` wächst endlos in langen Sessions.
**Fix:** `_put_cache()` aus `market_data.py` wiederverwenden, max. 256 Einträge.

**E) Strftime-Tippfehler in `pushover_notifier.py:27`**
```python
# %M = Minuten (falsch), %m = Monat (richtig):
now = datetime.now().strftime("%Y-%M-%d 00:00:00")
```
`now` wird danach nicht verwendet (toter Code), aber der Tippfehler zeigt
ein Muster, das auch an echten Stellen auftreten könnte.

---

### Prio 3 — Code-Qualität (mittelfristig)

**F) 131 `print()`-Statements** — sollten durch `logger.debug/info/warning` ersetzt
werden. Structured Logging ist in ~15 Dateien bereits vorhanden.

**G) 134x `pandas inplace=True`** — in Pandas 2.0+ deprecated, wird irgendwann
entfernt. Migration: `df = df.sort_values(...)` statt `df.sort_values(inplace=True)`

**H) Zwei parallele `ExpressionEvaluator`-Klassen in `tools.py`**

`ExpressionEvaluator` (alt) und `ExpressionEvaluatorNew` (neu) koexistieren.
`BuySellSignalGenerator` probiert zuerst den neuen, fällt auf den alten zurück —
inkonsistentes Verhalten je nach Expression.
**Fix:** Alten Evaluator entfernen, sobald der neue alle Fälle abdeckt.

**I) Zwei `PortfolioAnalysis`-Dateien** — `PortfolioAnalysis.py` und
`PortfolioAnalysis1.py` liegen parallel. Unklar welche aktuell ist → eine löschen.

**J) Stale `.tmp.*`-Dateien** in `tradinglib/` bereinigen:
```
tradinglib/market_data.py.tmp.19064.*
tradinglib/multi_transaction.py.tmp.19064.*
tradinglib/tools.py.tmp.19064.*
tradinglib/search.py.tmp.19064.*
```

---

### Prio 4 — Architektur (langfristig)

**K) `tools.py` ist ein God Object** — enthält Utilities, SQLite-Wrapper,
Streamlit-Helpers, zwei Evaluatoren und einen Signal-Generator. Schwer testbar.
Aufteilen in: `db_tools.py`, `st_tools.py`, `expression_eval.py`.

**L) Keine `st.session_state` für DB-Verbindungen** — Klassen wie `AssetSimulator`,
`MarketMap`, `PerformanceDetails` öffnen Connections in `__init__`. Bei jedem
Streamlit-Rerun wird eine neue Instanz gebaut → Connection-Leak.
**Fix:** Instanzen in `st.session_state` cachen.

**M) Keine Tests** — kein einziger Test im Repository. Für eine App, die
Handelsentscheidungen trifft, ist das das größte Stabilitätsrisiko.
Einstieg: `pytest` + Smoke-Tests für `fetch_data.py` und `tools.py`.

**N) `Scheduler` nicht thread-safe genug** — `check_same_thread=False` mit nur
einem `Lock`, aber `load_schedule_from_db` und `save_schedule_to_db` sind nicht
beide unter dem Lock geschützt.

---

## asset_perf2.py — Fast-Modes (backfill / rescore)

Nach einem `init`-Lauf können fehlende Indikator-Spalten ohne Neuberechnung befüllt werden:

```bash
# 1. Fehlende Spalten aus lokaler OHLCV auffüllen (kein Internet):
python asset_perf2.py /backfill:heikin,markov,macd
python asset_perf2.py /backfill:heikin,markov,macd /year:2024
python asset_perf2.py /backfill:heikin,markov,macd /all

# Bekannte Indikatoren für --backfill:
#   heikin  → ha_close, ha_open, ha_ema_high, ha_ema_low
#   markov  → markov_regime
#   macd    → macd, macd_diff, macd_signal
#   rsi     → rsi, rsi_ema, rsi_momentum
#   ewo     → ewo, ewo_ema, ewo_diff, ewo_angle
#   adx     → adx, adx_plus_di, adx_minus_di
#   dema    → dema_ema_fast, dema_ema_slow, dema_buy, dema_sell
#   hor     → hor_val, hor_threshold
#   sup     → sup_support, sup_resistance
#   relvol  → relvol_ratio
#   atc     → atc_top_high, atc_bot_low

# 2. Scores neu berechnen (verwendet gespeicherte DB-Spalten + asset_info):
python asset_perf2.py /rescore
python asset_perf2.py /rescore /year:2024
```

**Hinweis:** `macd_signal` ist in allen 8 `asset_simulation_*.db` via `ALTER TABLE ADD COLUMN` ergänzt (Wert=0).
Nach `--backfill macd` werden echte Werte eingetragen.

---

## Coding-Konventionen (bisher beobachtet)

- SQLite-Zugriff idealerweise über `Db_tools` (aus `tools.py`) — direkte
  `sqlite3.connect()`-Calls sind Legacy und sollten schrittweise ersetzt werden
- Parameterisierte Queries: immer `?`-Platzhalter + `params=(value,)` — kein f-String
  mit Benutzerwerten
- Exception-Handling: `except Exception:` (nie bare `except:`) — spezifische Typen
  wo bekannt (`ValueError`, `TypeError`, `IndexError`, `KeyError`, `AttributeError`)
- Cache-Dicts: mit `_put_cache(cache, key, value)` aus `market_data.py` befüllen,
  max. 512 Einträge
- Logging: `logger = logging.getLogger(__name__)` pro Modul, kein `print()` in Prod-Code

---

## Git-Workflow

```bash
# Neues Feature:
git checkout -b feature/mein-feature
# ...
gh pr create --title "feat: ..." --body "..."

# Bugfix:
git checkout -b fix/mein-fix
```

GitHub-Repo: https://github.com/online-junkie/trading-tools
