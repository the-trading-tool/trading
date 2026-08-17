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
| `tradinglib/providers/` | Austauschbare Marktdaten-Provider (`yahoo`/`fmp`/`eodhd`) hinter `get_provider()`; aktive Quelle = `_app:data_provider` in `config.db` |
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

### OHLC in `asset_simulation` (2026-07, Commit `ddde705`)

Roh-OHLC ist in `asset_simulation` als **`Open`/`High`/`Low`/`close`** verfügbar
(Ausnahme von der snake_case-Regel — großgeschrieben `Open/High/Low`).
→ In Buy/Sell-Queries direkt nutzbar, z. B. `(close > Open) & (High > sup_resistance)`.

> **⚠️ SQLite ist bei Spaltennamen case-INSENSITIV.** Das Großschreiben von `Open`
> verhindert **nicht** die Kollision mit `asset_info.open` — für SQLite sind `Open`
> und `open` derselbe Bezeichner. Sobald `asset_simulation` mit `asset_info` gejoint
> wird (z. B. All-Assets-Screener, `all_assets.py`), ist unqualifiziertes `Open`/`High`/
> `Low` in der `WHERE`-Klausel **mehrdeutig** → `ambiguous column name: Open`, die
> ganze Query crasht. Fix in `all_assets.py`: der JOIN exponiert per Subquery nur
> `ticker/longName/exchange` aus `asset_info`, damit `asset_info.open/dayHigh/dayLow`
> nicht im Scope sind. Bei neuen Joins mit `asset_info` dieselbe Vorsicht — entweder
> Spalten qualifizieren (`ap.Open`) oder den Join auf die gebrauchten Spalten narrowen.

- **Umbenannt:** `dayHigh → High`, `dayLow → Low` (in `asset_perf2.py` pdict +
  `score_df: col('Low')`; alle 9 `asset_simulation_*.db` per `ALTER TABLE RENAME COLUMN`).
- **Neu:** `Open` (pdict `safe_last(df,'Open')`; DB-Spalte per `ADD COLUMN`).
- **Kein** rohes `open` (lowercase) — das wäre ein Kollisionsname zu `asset_info.open`.
- Bestandsdaten via `python asset_perf2.py /backfill:ohlc /force` gefüllt
  (Map-Eintrag `'ohlc': ['Open','High','Low']`; liest lokale OHLCV, kein Yahoo).
  **Achtung Windows/Git-Bash:** `/force` wird von MSYS in einen Pfad gemangelt →
  `MSYS2_ARG_CONV_EXCL='*'` voranstellen oder über PowerShell laufen lassen.
- **Vintage-Hinweis:** Der Backfill lädt `Open/High/Low` frisch, `close` bleibt der
  alte Sim-Wert → bei Split-/Adjust-Tickern passt `close` ggf. nicht zu `O/H/L`
  (~0,2 % Zeilen). Heilt beim nächsten vollen `init`-Lauf (alle vier aus einem Fetch).

| Alte(r) Name(n) | Neuer Name | Indikator |
|---|---|---|
| `RelVol_Ratio`, `RelVol_Current`, `RelVol_Past`, `AdjVolume` | `relvol_ratio`, `relvol_current`, `relvol_past`, `relvol_adj_vol` | relvol |
| — | `relvol_direction` | relvol (neu) |
| `plus_di`, `minus_di`, `adx_plus_di`, `adx_minus_di` | `adx_plus`, `adx_minus` | adx |
| — | `adx_angle` | adx (neu) |
| — | `momentum_ema_angle` | stoch/indicator (neu) |
| — | `ema9_angle` | fetch_data (neu) |
| `stoch` (aus rsi.py), `rsi_momentum` | `momentum` | rsi |
| `stoch_ema` | `momentum_ema` | stoch/indicator |
| `MA20`, `MA50`, `MA100`, `MA200` | `sma20`, `sma50`, `sma100`, `sma200` | indicator.sma() |
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

### pdict-Keys / DB-Spalten (asset_perf2.py → asset_simulation_*.db)

Bei den folgenden Bezeichnern war die DataFrame-Spalte bereits konsistent benannt
(`sup_resistance`/`sup_support`/`relvol_ratio`), aber das pdict (und damit die
persistierten Spalten in `asset_simulation_.db`, `asset_simulation_{year}.db`,
`asset_simulation_all.db`) verwendete abweichende, nicht-snake_case Namen.
Hier wurde **das pdict an die df-Konvention angepasst** (umgekehrte Richtung zur
Tabelle oben — Regel: bei Konflikt gewinnt der bereits etablierte df-/Indikator-Name,
sofern er der `{prefix}_{name}`-Konvention folgt; sonst gewinnt der pdict-Name).
Die Spalten in allen drei DB-Dateien wurden per `ALTER TABLE ... RENAME COLUMN`
migriert (Daten bleiben erhalten):

| Alter pdict-/DB-Name | Neuer Name | Quelle |
|---|---|---|
| `resistance` | `sup_resistance` | sup (Tagesbasis) |
| `wkResistance` | `sup_resistance_wk` | sup (Wochenbasis) |
| `moResistance` | `sup_resistance_mo` | sup (Monatsbasis) |
| `support` | `sup_support` | sup (Tagesbasis) |
| `wkSupport` | `sup_support_wk` | sup (Wochenbasis) |
| `moSupport` | `sup_support_mo` | sup (Monatsbasis) |
| `relVolRatio` | `relvol_ratio` | relvol (Tagesbasis) |
| `wkRelVolRatio` | `relvol_ratio_wk` | relvol (Wochenbasis) |
| `moRelVolRatio` | `relvol_ratio_mo` | relvol (Monatsbasis) |
| `atc_high` | `atc_top_high` | atc (df-Spalte + Backfill-Map waren schon korrekt) |
| `atc_low` | `atc_bot_low` | atc (df-Spalte + Backfill-Map waren schon korrekt) |

`atc`-Migration (2026-07-02): Der Hauptlauf schrieb `atc_high`/`atc_low`, während
`INDICATOR_BACKFILL_MAP`/`atc.py` schon `atc_top_high`/`atc_bot_low` verwendeten →
8 der 9 `asset_simulation_*.db` hatten **beide** Paare. Migration: bestehende
Backfill-Spalten `atc_top_high`/`atc_bot_low` gelöscht, dann `atc_high`/`atc_low`
per `RENAME COLUMN` an ihre Stelle (Hauptlauf-Datenspur bleibt kanonisch). Hinweis:
die beiden Spuren wichen inhaltlich ~92 % ab (Sim-vs-OHLC-Vintage). Der Backward-
Compat-Alias in `atc.py` (`self.df['atc_high']=atc_top_high` u. `atc_low`) bleibt
als reine Live-DF-Bequemlichkeit bestehen — er wird nicht mehr ins pdict/DB gelesen.

Hinweis: `relvol_ratio_wk`/`relvol_ratio_mo` stammen bereits architektonisch aus
`relvol.py` — `fetch_data.fetch_data()` instanziiert die `Relvol`-Klasse für jedes
Timeframe (1d/1wk/1mo) separat; `asset_perf2.py` liest lediglich den letzten Wert
der Spalte `relvol_ratio` aus `df_weekly`/`df_monthly` (analog zu `ewo_wk`/`ewo_mo`
aus `ewo.py`). Es war also keine zusätzliche Berechnung in `relvol.py` nötig,
sondern nur die Vereinheitlichung der pdict-Schlüssel.

Neu: `relvol_direction` (+ `_wk`/`_mo`) zeigt an, **ob** das Relativvolumen von
Käufen oder Verkäufen ausgelöst wurde: `+1` = Close>Open (kauf-getrieben),
`-1` = Close<Open (verkauf-getrieben), `0` = Doji. Deckt sich mit der Balkenfarbe
in `relvol.add_fig()`. In Formeln nutzbar wie `relvol_ratio > 1.5 & relvol_direction
> 0`. Verdrahtung identisch zu `relvol_ratio`: Spalte in `relvol.py`, pdict +
`INDICATOR_BACKFILL_MAP['relvol']` in `asset_perf2.py`; `_wk`/`_mo` kommen aus der
Pro-Timeframe-Instanziierung (kein TF-Backfill-Eintrag → nur beim vollen `init`,
nicht via `/backfill:relvol`). Neue DB-Spalten legen `bulk_upsert_dicts` bzw.
`_ensure_sim_columns` selbst an — kein manuelles `ALTER TABLE` nötig.

### Trend-Scores nach ewo.py / macd.py verlagert

Analog zu `adx_angle` (→ adx.py) wurden zwei weitere, bisher nur als Skalare in
`asset_perf2.py` (für `overallValueTrend`) berechnete Trend-Scores als echte
DataFrame-Spalten in die jeweiligen Indikator-Klassen verlagert. Dadurch stehen
sie jetzt auch live im Asset Viewer (z. B. in buy/sell-Queries) zur Verfügung,
und die wk/mo-Varianten ergeben sich automatisch aus der Pro-Timeframe-
Instanziierung in `fetch_data.fetch_data()` (wie bei `ewo`/`relvol_ratio`):

| Alter pdict-Name (Skalar via Helper) | Neue Spalte (Indikator) | Neuer pdict-Name |
|---|---|---|
| `ewoDayTrend` (`trend(df)`) | `ewo_trend` (ewo.py) | `ewo_trend_day` |
| `ewoWeekTrend` (`trend(df_weekly)`) | `ewo_trend` (ewo.py, Wochen-DF) | `ewo_trend_wk` |
| `ewoMonthTrend` (`trend(df_monthly)`) | `ewo_trend` (ewo.py, Monats-DF) | `ewo_trend_mo` |
| `macdTrend` (`macd_trend(df)`) | `macd_trend` (macd.py) | `macd_trend` |
| `wkMacdTrend` (`macd_trend(df_weekly)`) | `macd_trend` (macd.py, Wochen-DF) | `macd_trend_wk` |
| `moMacdTrend` (`macd_trend(df_monthly)`) | `macd_trend` (macd.py, Monats-DF) | `macd_trend_mo` |
| `wkEwo` | `ewo` (ewo.py, Wochen-DF) | `ewo_wk` |
| `moEwo` | `ewo` (ewo.py, Monats-DF) | `ewo_mo` |

- `ewo.py`: neue Spalte `ewo_trend = np.where(ewo.diff() > 0, 1, -1)` (Richtung
  ggü. Vortag, ersetzt die alte `trend(df, id='ewo')`-Helperfunktion 1:1).
- `macd.py`: neue Spalte `macd_trend = (±0.5 je nach Richtung von macd_diff) +
  (±0.5 je nachdem ob macd > macd_signal)`, Wertebereich -1..+1 (ersetzt die alte
  `macd_trend(df)`-Helperfunktion 1:1).
- Die alten Modulfunktionen `trend()` und `macd_trend()` in `asset_perf2.py`
  wurden entfernt; `fill_pdict` liest die Werte jetzt per
  `DataUtils.safe_last(df[_weekly/_monthly], 'ewo_trend'/'macd_trend', ...)`.
- Auch die Spalten in `asset_simulation_.db`, `asset_simulation_{year}.db`,
  `asset_simulation_all.db` wurden per `ALTER TABLE ... RENAME COLUMN` migriert.

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

**M) Testabdeckung noch dünn** — erste Tests existieren unter `tests/`
(`pytest tests/ -q`, pytest 9.x im venv):
- `tests/test_signal_mask.py` — BuySellSignalGenerator / Multi-Strategies-Masking
- `tests/test_index_normalization.py` — DatetimeIndex-Sort/Dedup (ensure_datetime_index, Bsz)

Für eine App, die Handelsentscheidungen trifft, bleibt die Abdeckung das größte
Stabilitätsrisiko. Nächste sinnvolle Ziele: `fetch_data.load_price_data`
(Lade-Pipeline) und `tools.py` (Evaluator/Db_tools).

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

# Bekannte Indikatoren für --backfill (siehe INDICATOR_BACKFILL_MAP):
#   heikin  → ha_close, ha_open, ha_ema_high, ha_ema_low
#   markov  → markov_regime
#   macd    → macd, macd_diff, macd_signal, macd_trend
#   rsi     → rsi, rsi_ema, momentum
#   ewo     → ewo, ewo_ema, ewo_diff, ewo_angle, ewo_trend
#   adx     → adx, adx_plus, adx_minus, adx_angle
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

GitHub-Repo: https://github.com/the-trading-tool/trading

---

## Neu in dieser Session (2026-06-01)

### Umgebungen
- **Dev/Test:** `C:\Users\kurtl\Claude\Trading` und `C:\Users\kurtl\trading.cloogidoo.com.V2`
- **Produktion:** `C:\Users\kurtl\trading.cloogidoo.com` — **NICHT anfassen**
- Nach jeder Änderung in `Claude\Trading` die geänderten Dateien nach `trading.cloogidoo.com.V2` kopieren
- Korrekte pip-Installation: immer `.venv\Scripts\python.exe -m pip install ...` (nicht `.venv\Scripts\pip.exe`)
- Datenbank-Pfad wird via `TradingDB`-Env-Var überschrieben; Produktions-DBs liegen in `C:\Users\kurtl\Development\database\`

### KI-Integration (`tradinglib/ai_client.py`, `tradinglib/banner_ai.py`, `tradinglib/gemini_api.py`)

**`ai_client.py`** — Unified AI Client mit Provider-Fallback:
- `AiClient(provider='auto'|'groq'|'gemini'|'ollama')` — Provider-Auswahl via `config.db` key `ai_provider`
- Fallback-Reihenfolge: Groq → Gemini → Ollama
- `GeminiRateLimitError` = Alias auf `AiRateLimitError` (Rückwärtskompatibilität)
- Groq-Modelle: `llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → `gemma2-9b-it`
- Gemini-Modelle: `gemini-2.0-flash-lite` → `gemini-2.5-flash-lite` → `gemini-2.5-flash` → `gemini-2.0-flash`
- Ollama: lokale Instanz unter `http://localhost:11434`, Modell aus ksp-Entry `ollama`
- KSP-Keys: `gapi` (Gemini), `groq` (Groq), `ollama` (URL+Modell)
- Installed in both venvs: `groq>=1.4.0`, `google-genai>=2.7.0`

**`banner_ai.py`** — Auto-Analyse des letzten Trade-Signals:
- `BannerAiGenerator.run(force=False)` — liest `trades{year}.db` → Top-Asset → Simulation → Gemini/Groq-Analyse → `banner_notes.db`
- `BannerAiGenerator.build_debug_info()` — sammelt alle Daten ohne API-Call (für Debug-Ansicht in Admin)
- Datenquellen: `trades{year}.db` (letzter Kauf), `asset_simulation_all.db` (85 Spalten), `asset_info.db`, `yf_<ticker>.db`
- Upsert in `banner_notes.db`: alte Einträge für denselben Ticker werden gelöscht
- `buyDate`-Spalte wird via `ALTER TABLE ADD COLUMN` nachgerüstet falls sie fehlt

**`gemini_api.py`** — Backward-Compat-Wrapper um `ai_client.AiClient`

**Admin-UI (`admin.py`):**
- Banner Note Expander: Provider-Dropdown, Debug-Checkbox, Analyse-Button
- Debug-Ergebnis wird in `st.session_state['_bn_dbg']` gespeichert (überlebt Reruns)
- `from tradinglib import system_config as sysconf` ist als Import vorhanden (wurde in dieser Session ergänzt)

### Banner Page (`tradinglib/banner_page.py`, `locales/en.json`, `locales/de.json`)

**Struktur:**
1. 🌐 Sprachauswahl (Deutsch als Default, gespeichert in `config.db` key `language`)
2. 4 Metriken: Startkapital · Portfoliowert · Performance · Max. Positionen
3. Strategien-Label + Indizes-Label (korrekte Terminologie)
4. Expander "Wie die Strategie funktioniert" mit Detail-Tabelle
5. KI Trading-Tipp aus `banner_notes.db`
6. Trades-Tabelle + Cumulative-Gain-Chart
7. **Strategie-Performance-Analyse** (Kennzahlen je Strategie aus `trades{year}.db`)
8. **Disclaimer-Expander** (zugeklappt)

**Terminologie (wichtig!):**
- Äußere `multi_transactions`-Keys = **Strategie-Namen** (z.B. "Value Trend Strategy")
- Innere Keys = **Index-Namen** (z.B. "SPX", "GDAXI")
- `num_assets`-Gesamtmenge = Summe der **Maxima** pro Index über alle Strategien
  (Wenn Strategy1/SPX=3 und Strategy2/SPX=4 → 4 wird addiert, nicht 3+4)

**Strategie-Kennzahlen (`_render_strategy_analysis`):**
- Offene Positionen, Profit-Faktor, Trefferquote, Ø Gewinn/Trade, Realisierter Gewinn
- Längste Gewinn-/Verlustserie
- Beste/Schlechteste Trades als 2-Spalten-DataFrame (3M + 1J)
- Automatischer `⚠️ Geopolitik-Hinweis` bei Strategien die "value trend" im Namen enthalten

**Strategien sind 2019–heute getestet, auch 2020 positiv:**
- Support/Resistance Strategy: PF 16.77, Win-Rate 75% (84 Trades)
- Value Trend Strategy: PF 3.02, Win-Rate 50.6% (233 Trades)

### Nächste Session — Priorisierte Optimierungen

**Prio 1 — Pushover-Alert bei neuem Buy-Signal** ✅ implementiert
- **`tradinglib/signal_notifier.py`** — zentrales Notifier-Modul (kein Streamlit-/MTP-Overhead):
  - Liest Buys/Sells direkt aus `trades{year}.db`
  - Buy: `stop_loss`/`take_profit` aus `asset_simulation_all.db` + KI-Text aus `banner_notes.db`
  - Sell: `gainPct`/`gain` direkt aus trades-DB
  - Deduplication via `pushover_notifier.json` — kein Doppelversand
  - CLI: `python -m tradinglib.signal_notifier [/force] [/date:YYYY-MM-DD] [/user:admin]`
- **`tradinglib/premium/multi_transaction.py` `run_notifier()`** → dünner Wrapper auf `signal_notifier.run()`
- **`tradinglib/pushover_notifier.py`**: `%M`-Bug gefixt, alle `print()` → `logger`
- `asset_perf2.py` ruft weiterhin `mt.run_notifier()` auf — keine Änderung nötig
- **Hinweis:** `tradinglib/multi_transaction.py` in V2 ist eine Leiche (Datei nach `premium/` verschoben)

**Prio 2 — Portfolio-Overlap-Check** ✅ implementiert (auf Banner Page + Multi Strategies)
**Prio 3 — Monatliche Performance-Heatmap** ✅ implementiert (auf Banner Page + Multi Strategies + Strategy Finder)

### Shared Backtest Widgets (`tradinglib/backtest_widgets.py`)
Zentrales Modul — alle Widgets einmalig implementiert, von allen 3 Seiten importiert:
- `render_monthly_heatmap(df, region, system_currency)` — RdYlGn Heatmap Monat×Jahr
- `render_strategy_analysis(df, region, system_currency)` — KPI-Block je Strategy (Banner + Multi)
- `render_portfolio_overlap(df, region)` — WARNING bei Doppelpositionen (Banner + Multi)
- `render_compact_analysis(df, region, system_currency)` — KPIs ohne Strategy-Gruppierung (Strategy Finder)

Einstiegspunkte:
- `banner_page.py` — delegiert via `_render_*`-Wrapper-Methoden
- `premium/multi_transaction.py` — Block nach Cumulative-Gain-Chart (vor `buy_gain = float(...)`)
- `premium/asset_simulator.py` — Block nach `total_gain_expander`, vor Parity-Render

### In dieser Session erledigt (2026-06-02)

**DB-Optimierungen:**
- `open_db()` in `tools.py` — zentraler SQLite-Wrapper mit WAL, synchronous=NORMAL, mmap, cache (64 MB)
- Alle ~64 raw `sqlite3.connect()`-Calls auf `open_db()` migriert (22 Dateien)
- `@st.cache_resource` für `market_map.py` + `performance_details.py` — Connection-Reuse über Reruns
  - **Wichtig:** `check_same_thread=False` nötig, da Connection thread-übergreifend genutzt wird
- `Scheduler`: `Lock()` → `RLock()`, `load_schedule_from_db` + `save_schedule_to_db` unter Lock

**Code-Qualität:**
- `eval()` → `ast.literal_eval()` in allen Config-DB-Lese-Stellen (banner_page, banner_ai, multi_select, system_config, asset_analyzer)
- `pd.json.loads` → `json.loads` in fetch_data.py
- `print()` → `logger.*` in 47 Dateien (84 → 8 intentionale Print-Calls)
- `inplace=True` → `df = df.method()` in 109 → 0 aktiven Stellen
- `ExpressionEvaluatorNew` → `ExpressionEvaluator` (alte Klasse gelöscht)
- `PortfolioAnalysis1.py` gelöscht, 133 `.tmp.*`-Dateien gelöscht
- `_xrate_cache` bounded auf 256 Einträge
- `duckdb>=1.0.0`, `scipy>=1.11.0`, `tqdm>=4.0.0` in `requirements.txt`

**DuckDB-Integration:**
- `duckdb` installiert in beiden venvs (V2 + Claude\Trading)
- `_duckdb_fetch_years()` in `performance_details.py` — analytische Multi-Jahr-Engine
  - `latest_only=True` für Performance-Seite (1 Zeile/Ticker)
  - `latest_only=False` für Simulation (alle Zeilen = Zeitreihe)
- **Wichtig:** nach duckdb-Installation Streamlit neu starten (Import-Cache!)
- Datenbank-Namenskonvention: `asset_simulation_.db` = aktuelles Jahr (kein Suffix!), `asset_simulation_2025.db` = 2025

**Strategy Finder — Multi-Jahr:**
- `selectbox` → `multiselect` für Jahresauswahl in `asset_simulator.py`
- `📅 Stand per` Datumspicker für historische Snapshots (Backtesting)
- Multi-Jahr-Loop via SQLite: für jedes Jahr `fetch_combined_data_with_attach()` + `pd.concat()` + `drop_duplicates()`
- DuckDB als optionale Beschleunigung (kein Hard-Requirement)

**Performance-Seite:**
- `selectbox` → `multiselect` in `performance_details.py`
- DuckDB-Cache via `@st.cache_data(ttl=300)`
- Thread-Bug behoben: `check_same_thread=False` in `_cached_ticker_conn` + `_cached_info_conn`

**Backlog-Status (alle Prio 1-3 erledigt):**
- ✅ 1-A: eval() → ast.literal_eval
- ✅ 1-B: pd.json.loads fix
- ✅ 2-C: sqlite3 Connections via open_db
- ✅ 2-D: _xrate_cache bounded
- ✅ 2-E: strftime Bug (war schon gefixt)
- ✅ 3-F: print() → logger
- ✅ 3-G: inplace=True
- ✅ 3-H: ExpressionEvaluator konsolidiert
- ✅ 3-I: PortfolioAnalysis1.py gelöscht
- ✅ 3-J: .tmp-Dateien gelöscht
- ✅ 4-N: Scheduler thread-safe (RLock)
- ⬜ 4-K: tools.py God Object (große Refaktorierung)
- ⬜ 4-M: Keine Tests

---

## Neu in dieser Session (2026-06-13) — Ticker-Auswahl & paralleler Download

### `^`-Konvention für die Index-Auswahl (wichtig!)
In der `indices`-Tabelle (`yf_tickers.db`) beginnen **echte Börsen-Indizes
ausnahmslos mit `^`** (`^GDAXI`, `^MDAXI`, `^SPX`, `^N225`, … — 16 Stück).
Alles ohne `^` sind **Kategorie-Gruppen**: `INDEX`, `COMMODITIES`, `METALS`,
`CURRENCIES`, `CRYPTO` und neu `ETP` (3376 Mitglieder).

`/index_member` (get_asset_data.py) bzw. der Default-Lauf (asset_perf2.py)
filtern die Gruppenliste jetzt hart auf den `^`-Präfix:
```python
filtered = [name for name in filtered if name.startswith('^')]
```
→ robuster als die `NON_STOCK_GROUPS`-Blockliste: jede künftige Nicht-Index-Gruppe
ist automatisch ausgeschlossen, ohne Listenpflege. **`NON_STOCK_GROUPS` wurde
bewusst NICHT um ETP erweitert** — die Konstante hat zwei gegensätzliche
Verwendungen (Blockliste in `/index_member`, Auswahlliste in `/index`/`index_only`);
ETP dort einzutragen hätte 3376 ETPs in den `/index`-Pfad und die Sim gezogen.

### `/group:NAME` jetzt auch in asset_perf2.py
`asset_perf2.py` wertet `args.get('group')` jetzt aus (Parsing war in `cli.py`
längst vorhanden). Damit volle Parität mit get_asset_data.py:
- Default / `/index_member` → nur `^`-Indizes (998 Ticker)
- `/group:ETP` → ETPs (3376) im **eigenen Lauf** (so gewollt)
- `/group:CURRENCIES` → die 4 `=X`-FX-Paare
- `/inverse` → die **3650** Ticker ohne jede Gruppe (Einzeltitel wie BABA, AMC) —
  NICHT redundant zu `/group`, „keine Gruppe" ist kein Gruppenname → bleibt erhalten
- `/index:NAME` (asset_perf2) wertet jetzt **nur** den genannten Index aus; das alte
  pauschale `OR i.name IN (NON_STOCK_GROUPS)` war ein Relikt aus der Zeit vor `/group`
  und wurde entfernt.

Aus `get_asset_data.py` `/index_member` zusätzlich das `OR s.Ticker LIKE "%=X"`
entfernt (FX-Paare hängen alle an `CURRENCIES` → über `/group:CURRENCIES` erreichbar).

### Paralleler Download in get_asset_data.py (`/worker:N`)
Download-Schleife auf `ThreadPoolExecutor` umgestellt; `/worker:N` (Default 1).
yfinance ist **nicht threadsafe** (modul-globales `yfinance.shared._DFS`) → ein
`threading.Lock` serialisiert `yf.download` in `yahoo_provider.py` und im Fallback
von `market_data.py`. Empfehlung 2–4 Worker (Yahoo HTTP 429). asset_perf2.py nutzt
weiterhin `ProcessPoolExecutor` (CPU-lastig), get_asset_data ThreadPool (I/O).

Commits: `8137c55` (parallel download), `26e12fb` (get_asset_data ^-Filter),
`0d3c0ca` (asset_perf2 ^-Filter + /group). HELP-Seiten `get_asset_data.html` /
`asset_perf2.html` entsprechend aktualisiert.

### Daten-Pipeline: wann werden importierte Assets in der App sichtbar?
Drei DBs, drei Stufen — wichtig zu verstehen, warum ein frisch importierter Markt
(z.B. ETP) zwar im „Select by market" steht, aber „No options to select" bei den
Firmen zeigt:
1. **`get_asset_data.py`** (Excel-Default oder `/group`) → `yf_tickers.db`
   (stocks/indices/stock_indices) + `yf_<TICKER>.db` (OHLCV).
   → Der **Markt** erscheint sofort (`MarketSearch.get_index_list()` liest
   `SELECT name FROM indices`).
2. **`get_asset_info.py`** → `asset_info.db` (Stammdaten).
   → Erst jetzt füllt sich der **„Select company"-Dropdown** und die Volltextsuche.
   Grund: `make_query(q=7)` nutzt `INNER JOIN info_db.asset_info ON yt.Ticker =
   ai.ticker` ([make_query.py](tradinglib/make_query.py)) — ohne asset_info-Zeile
   kein Dropdown-Eintrag. q=7 joint NICHT auf asset_simulation, d.h. asset_perf2
   muss dafür noch nicht gelaufen sein.
3. **`asset_perf2.py`** → `asset_simulation_.db` (Scores/Signale).
   → Chart-Overlays, Buy/Sell, Kennzahlen-Panel.

**Volltextsuche** (`FullTextSearch`, search.py) liest `asset_info_fts` (FTS5 aus
`asset_info`). `create_fts_table()` befüllt nur, wenn leer. `get_asset_info.py`
baut die FTS-Tabelle am Ende automatisch neu auf (`rebuild_fts_table()`, direktes
SQL, kein Streamlit-Import) → neue Ticker sind sofort volltextsuchbar. Admin-Button
„Update index" (`update_fts_table()`) bleibt als Fallback.

### get_asset_info.py: `/group:NAME` + `/worker:N` ergänzt
Vorher las get_asset_info immer die volle Liste (~8000 Ticker). Jetzt:
- `/group:ETP` → nur Mitglieder dieser Gruppe(n) (via `build_ticker_list(group)`,
  Query mit `UPPER(i.name) IN (...)`) — gezieltes Nachladen statt alles.
- `/worker:N` → parallele Info-Downloads (ThreadPool). `.info/.financials/
  .balance_sheet` sind pro Ticker eigene Requests → threadsicher, **kein** Lock
  nötig (anders als `yf.download`). row_maps werden im Main-Thread per
  `as_completed` eingesammelt, einmal `bulk_upsert_dicts` am Ende.
- Schleifenkörper in `fetch_info_for(ticker)` ausgelagert; Flush sauber in
  `__main__` (vorher Modul-Ebene). HELP `get_asset_info.html` / `get_asset_data.html`
  um Pipeline-Tabelle erweitert.
- **FTS-Auto-Rebuild:** `rebuild_fts_table(conn, 'asset_info')` baut am Ende die
  `asset_info_fts`-Suchtabelle neu (DROP/CREATE fts5(ticker, longName)/INSERT) →
  neue Ticker sofort volltextsuchbar, kein Admin-„Update index" mehr nötig. Struktur
  identisch zu search.py, damit die App die Tabelle übernimmt. In try/except, damit
  ein Rebuild-Fehler den bereits committeten Upsert nicht gefährdet.

### Fix: `/add_current` in asset_perf2 war wirkungslos
Im `ProcessPoolExecutor`-Aufruf war das 3. Positionsargument (`add_current`) hart
auf `False` verdrahtet → die geparste `/add_current`-Option erreichte
`process_symbol` nie (aktuelle, noch nicht geschlossene Tageskerze wurde nie
ergänzt). Jetzt wird die Variable durchgereicht.

### ✅ cli.parse_args-Lowercasing-Bug gefixt
`cli.parse_args` schrieb `pref = (argv[i][1:]).lower()` über das **ganze** Argument
klein, also auch den Wert nach `:` → case-sensitive Werte gingen verloren
(`/index:^SPX` → `^spx` matchte `^SPX` nicht; `/select:… "%.MC"` → `%.mc`).
**Fix (commit s.u.):** erst auf `:` splitten, dann nur den Key (`pref`) lowercasen,
den Wert (`suf`) im Original belassen. Case-insensitive gewollte Werte werden
gezielt normalisiert: `group → .upper()` (+ Query `UPPER(i.name)`),
`backfill → .lower()` (Map-Keys sind lowercase). Verifiziert: `/index:^SPX` → 515,
`/select:… "%.MC"` → 30 Treffer (vorher je 0).

---

## Neu in dieser Session (2026-06-28) — Broker-Handelbarkeits-Filter

Ziel: Signale auf die beim eigenen Broker **tatsächlich orderbaren** Ticker
einschränken (Auslöser: Scalable Capital handelt nicht jeden ^RUT-Small-Cap).

**`tradinglib/broker_tradability.py`** — broker-agnostischer Filter (Plugin-Muster):
- Checker je Broker: `scalable` (Proxy `unofficial-scalable-capital-api`,
  `GET /securities/{isin}/buyable`), `alpaca` (`/v2/assets`-Liste), `ibkr`
  (permissiv: SMART deckt faktisch alles; `config.db ibkr_exclude`), `none`
  (kein Filter, Default). Aktiver Broker: `config.db '<user>:broker'`.
- Öffentliche API: `check_tradable(tickers, broker_id=None)` → `{ticker:
  Tradability}`; `filter_tradable(...)` → `{tradable, not_tradable, unknown}`.
  `drop_unknown=False` (Default) lässt Unbekannte durch (kein Signalverlust bei
  Broker/Proxy-Ausfall); `drop_unknown=True` = strikt.
- Cache: `asset_info.db.broker_tradability_cache` (PK broker+ticker), Re-Check
  nach `REFRESH_DAYS=7` → eine Online-Abfrage pro ISIN, danach offline.
- Scalable-Antwort wird defensiv geparst (`_parse_scalable_payload`); Proxy down
  / keine ISIN → `tradable=None`. Scalable-Config-Keys: `scalable_proxy_url`
  (Default `http://localhost:8080`), `scalable_gateway_token`.
- CLI: `python -m tradinglib.broker_tradability /index:^RUT|/tickers:A,B|/file:x.txt
  [/broker:scalable] [/strict] [/out:report.json]`. Ersetzt den Ad-hoc-
  `rut_*`-Workflow (die `.DE`-Yahoo-Heuristik in `rut_91_check.json` lieferte
  Fehltreffer wie IESC→tonies SE).

**ISIN-Quelle / -Lücke:** ISINs liegen in `yf_tickers.db.stocks.ISIN`
(`backfill_isin.py`), aber nur ~43 % gefüllt — gerade junge Small-Caps (PLUG,
OKLO, RGTI…) fehlten, weil `yf.Ticker().isin` für sie nichts liefert.
- `backfill_isin.py fetch_isin()` hat jetzt **Provider-Fallback** nach yfinance.
  (2026-07-15: nicht mehr FMP-fest — läuft über `get_isin_resolver()`, also FMP
  *oder* EODHD, je nach aktivem Provider/hinterlegtem Key. Param hieß `use_fmp`,
  jetzt `use_provider`; Helper `_get_fmp()` → `_get_isin_provider()`.)
- `tradinglib/providers/fmp_provider.py`: neue Methode `profile_isin(ticker)`
  (Inverse zu `search_isin`); `eodhd_provider.py` bietet beide ebenfalls.
- `IsinResolver` (in broker_tradability) liest `stocks.ISIN`, lädt Fehlende bei
  Bedarf nach und **schreibt sie zurück** → Lücke schließt sich progressiv.

**Einfacher `require_isin`-Vorfilter (verdrahtet):** Zusätzlich zum proxy-
basierten Broker-Check gibt es einen leichtgewichtigen Schalter, der die
Selektion auf Werte **mit gültiger ISIN** beschränkt — als Näherung für
„handelbar" ohne jede externe Abhängigkeit.
- Eingehängt in `AssetSimulator.fetch_combined_data_with_attach()`
  (asset_simulator.py) — der **gemeinsame Engpass**: Strategy Finder (Single-
  *und* Multi-Jahr via `_fetch_for_year`) UND Multi-Transactions
  (`multi_transaction.py:541`) laufen dort durch, ein Filter deckt alles ab.
- Schalter: `config.db` key `require_isin` (Default False). UI-Checkbox im
  Strategy-Finder-Sidebar („Nur handelbare (ISIN vorhanden)", Locale-Keys
  `sf.require_isin[_help]`), schreibt den Wert global in config.db (kein
  `on_change` → meidet das Overlay-Korruptions-Muster).
- **Global + per-Index-Override:** `fetch_combined_data_with_attach(...,
  require_isin=None)` — `None` = globaler Schalter, `True/False` = überschreibt
  global. Multi-Transactions liest pro Index das optionale Feld `require_isin`
  aus `multi_transactions` (analog `trailing_stop`) und reicht es durch; akzeptiert
  bool / `'yes'`/`'no'`/`'true'`/`'ja'`. Beispiel-Eintrag im Index-Dict:
  `'^RUT': { ..., 'order_by': 'sortino', 'require_isin': True }`. Fehlt das Feld
  → globaler Default. Strategy Finder ruft ohne Param → global.
- ISIN-Validierung: `AssetSimulator._has_valid_isin()` —
  Regex `[A-Z]{2}[A-Z0-9]{9}[0-9]`; NULL→'None'/'nan', Indizes (`^…`), FX (`=X`)
  fallen korrekt raus.
- **Wichtig:** erst nach vollem `python backfill_isin.py` (jetzt mit FMP-Fallback)
  aktivieren — sonst filtert er ~57 % bloß-noch-nicht-aufgelöste Werte (inkl.
  handelbarer Large-Caps) mit raus. „Keine ISIN" = derzeit eher „nicht aufgelöst"
  als „nicht handelbar".

**Noch offen:** Der proxy-basierte Scalable/IBKR-Filter (`broker_tradability.py`)
ist als Funktion/CLI nutzbar, aber noch **nicht** in den Live-Signal-Loop
eingehängt (Integrationspunkt: vor dem Trade-Insert). Scalable-Pfad braucht den
lokalen Proxy (Login/2FA, Session wird wiederverwendet).

---

## Neu in dieser Session (2026-07-15) — EODHD als dritter Datenprovider

Die Provider-Schicht kennt jetzt **drei** Quellen: `yahoo` (Default), `fmp`, `eodhd`.
Downstream (`market_data.py`, `fetch_data.py`, `get_asset_data.py`) blieb unverändert —
alles läuft weiter über `get_provider()`.

**`tradinglib/providers/eodhd_provider.py`** (neu, an `fmp_provider.py` angelehnt):
`download()`, `ticker_history()`, `search_isin()`, `profile_isin()`, `test_connection()`.
Key via KSP-Eintrag `eodhd`/`password`, Overrides unter `_app:eodhd_ticker_overrides`.

**Ticker-Format — der Hauptunterschied zu FMP.** EODHD verlangt **immer**
`CODE.EXCHANGE`, auch für US-Werte (`AAPL` allein liefert nichts). Indizes, Krypto
und FX laufen über virtuelle Exchanges:

| Yahoo | EODHD |
|---|---|
| `AAPL` | `AAPL.US` (Suffix wird angehängt) |
| `SAP.DE` | `SAP.XETRA` |
| `VOD.L` | `VOD.LSE` |
| `^GSPC`, `^GDAXI` | `GSPC.INDX`, `GDAXI.INDX` |
| `BTC-USD` | `BTC-USD.CC` |
| `EURUSD=X` / `JPY=X` | `EURUSD.FOREX` / `USDJPY.FOREX` |
| `CT=F` (Futures) | kein verlässliches Äquivalent → Override nötig |

Die meisten Börsensuffixe sind identisch (`.PA`, `.MI`, `.SW`, `.TO`, `.HK` …) →
`_SUFFIX_MAP` enthält **nur** die echten Abweichungen (`.DE→.XETRA`, `.L→.LSE`,
`.AX→.AU`, `.WA→.WAR`, `.KS→.KO`, `.SS→.SHG`, `.SZ→.SHE`, `.KL→.KLSE`, `.BD→.BUD`)
— Quelle ist die offizielle EODHD-Exchange-Liste, nicht geraten. Unsichere Kandidaten
(`.T`, `.NS`, `.BO`, `.SI`) sind bewusst **nicht** gemappt → über Overrides lösen.
`_eodhd_to_yahoo()` ist die Umkehrung (für die ISIN-Auflösung, deren Konsumenten
Yahoo-Ticker erwarten).

**Intervalle:** EODHD liefert intraday nur `1m`/`5m`/`1h`. Statt stillschweigend
5m-Kerzen zurückzugeben, wenn `15m` angefragt wurde, wird die nächstfeinere
Auflösung geholt und **lokal resampled** (`_resample_ohlcv`, O=first/H=max/L=min/
C=last/V=sum). Startdatum wird an die Historien-Limits geklemmt (1m ≈ 120 d,
5m ≈ 600 d, 1h ≈ 7200 d). `1d`/`1wk`/`1mo` → EOD-Endpoint mit `period=d/w/m`.

**Refactorings drumherum (Verhalten für Bestands-FMP-Nutzer unverändert):**
- `providers/__init__.py`: `_read_fmp_key` → generisches `_read_provider_key(name)`
  (der alte Name bleibt als Wrapper, `backfill_isin.py` importiert ihn). Neu:
  `KEYED_PROVIDERS`/`PROVIDERS`, `_build_keyed_provider()`, `get_eodhd_provider()`.
  Fehlender Key → weiterhin stiller Fallback auf Yahoo (nur Log-Eintrag).
- **`get_isin_resolver()`** (neu): ISIN-Auflösung war auf FMP verdrahtet
  (`scalable_import.py`, `backfill_isin.py`). Jetzt gewinnt der **aktive**
  Datenprovider, sonst der andere mit Key → ein EODHD-Key allein genügt, kein
  Zusatz-FMP-Key mehr nötig. Bei `data_provider = yahoo`/`fmp` bleibt FMP erster
  Kandidat → Bestandsverhalten identisch.
- `system_config.py`: der FMP-Block ist jetzt **ein** generischer Pfad über
  `KEYED_PROVIDERS` (Key-Status, Overrides, 🔌-Test). Locale-Keys `cfg.fmp_*` →
  `cfg.provider_*` mit `{provider}`-Platzhalter (de+en). Die FMP-Texte rendern
  wortgleich wie vorher; für Inline-Labels wird der Kurzname genutzt
  (`EODHD-Ticker`, nicht `EOD Historical Data (EODHD)-Ticker`).
- HELP `providers.html`/`providers_en.html` um EODHD-Abschnitt erweitert
  (Ticker-Tabelle, Intervalle, Rate-Limits, ISIN-Hinweis); Labels „Datenquellen
  (Yahoo / FMP / EODHD)" in `index.html`, `setup_scheduler*.html`, `system_config.py`.

**⚠ Noch nicht gegen die Live-API getestet** — im Dev-Env ist kein EODHD-Key
hinterlegt. Verifiziert wurde mit gemockten Responses (Ticker-Mapping hin/zurück,
EOD-/Intraday-Shaping, 5m→15m-Resampling, MultiIndex-Form, ISIN-Suche inkl.
Primary-Listing-Auswahl, Fallback ohne Key). Endpoints/Feldnamen stammen aus der
aktuellen EODHD-Doku. **Erster echter Call = eigentlicher Test:** Key als
KSP-Eintrag `eodhd` anlegen → 🏵-Konfiguration → 🔌 Verbindung testen.

**Rate-Limits:** EODHD-Free nur **20 Anfragen/Tag** (reines Testkontingent — ein
`get_asset_data.py`-Lauf sprengt das sofort), kostenpflichtig 100.000/Tag. Für
echte Hintergrund-Jobs ist FMP-Free (250/Tag) praktikabler.

---

## Neu in dieser Session (2026-07-29) — `ovt`-Indikator (Overall (Value) Trend)

Neuer Oszillator [`tradinglib/indicator/ovt.py`](tradinglib/indicator/ovt.py)
(Klasse `Ovt`), der die Engine-Scores `overallTrend` (technisch) und
`overallValueTrend` (Value/Fundamental) — plus `ovtEma{span}` (EMA über
`overallValueTrend`, gleiche Semantik wie `OvtEmaUpdater`) — **live** als
Sub-Plot bereitstellt. **Hybrid A+B, ohne Logik-Duplizierung:**

- **B (autoritativ):** liest `overallTrend`/`overallValueTrend` direkt aus
  `asset_simulation_*.db` per `ticker`+Datum, über mehrere Jahres-DBs
  (`asset_simulation_.db` = aktuelles Jahr, sonst `_{year}.db`). Verifiziert:
  **100 % exakter Match** gegen die gespeicherten Werte (AAOI, 145 Zeilen).
- **A (Fallback):** für Bars *ohne* gespeicherten Wert (aktueller/Intraday-Bar
  oder nie simulierter Ticker) ruft `data()` **`asset_perf2.score_df` selbst**
  auf (derselbe vektorisierte Scorer). Nur die live fehlenden Skalare
  (`vola`/`sharpe`/`sortino`/`logVola`/`wkTrend`/`roa` + `asset_info`-
  Fundamentals) werden mit denselben Helfern (`indicator.sharpe_ratio`,
  `log_return`, `trend_pct_df`, `asset_perf2.get_roa`) rekonstruiert.
  `score_df`/`get_roa` werden **lazy** importiert → kein Zirkularimport (das
  Modul liegt selbst im `indicator`-Paket).

**Nutzung:** erscheint automatisch in der Oszillator-Auswahl (Auto-Discovery).
Spalten `overallTrend`/`overallValueTrend`/`ovtEma9` sind **namensgleich zur
Backtest-Engine** → Buy/Sell-Formeln laufen live *und* im Backtest identisch.
Kein `INDICATOR_BACKFILL_MAP`-Eintrag nötig (die Spalten existieren in
`asset_simulation` bereits über `asset_perf2`).

**Einschränkung (bewusst):** Der A-Tail ist eine Näherung — `macd_trend_wk/mo`
und `moTrend` liegen live nicht vor, `score_df` zählt deren Gewicht bei Wert 0
→ leichte Dämpfung der *unsimulierten* Randbars. Historie über B ist exakt. Für
besten Tail ewo/macd/adx/rsi/heikin mit-aktivieren (sonst sieht `score_df` auch
deren Spalten als 0).

**⚠️ Oszillatoren dürfen keine Layout-Annotationen via `_add_hline_outside`
setzen.** Bug gefunden: `_add_hline_outside` erzeugt eine Annotation mit
`xref='paper'`/`x=0.0`. Der **Overlay**-Pfad in `tiny_chart` (Zeile ~344ff.)
überträgt Annotationen bewusst **ohne** `row/col` und erhält `xref='paper'` —
darum funktioniert der Helper bei Overlays (`sup`/`gan`/`fib`/`pre`/`pvt`). Der
**Sub-Plot-Pfad** (Zeile ~674) überträgt sie dagegen mit
`add_annotation(an, row=1, col=1)`, wodurch Plotly `xref='paper'`→`'x'`
umschreibt → `x=0.0` wird zur Epoch-Datumskoordinate **1970-01-01** und streckt
die geteilte `type="date"`-Achse (gesamter Chart 1970→heute, Daten rechts
zusammengequetscht). `ovt` nutzt deshalb reine `add_hline`-Referenzlinien (nur
Shapes, die als `x{row} domain` sauber übertragen werden). Latent: der Sub-Plot-
Annotations-Pfad in `tiny_chart` ist generell fehlerhaft (homed nach `row=1`
statt `row=row` + xref-Override) — bisher ohne Konsumenten.

---

## Neu in dieser Session (2026-08-17) — 4PS-Methode (4 Phase Sequence)

Neue Seite + Indikator + Backtest-Spalten für die "4 Phase Sequence"-Methode
(Trendfolge: bewährte Historie → Konsolidierung → Ausbruch → bestätigter Trend).

| Datei | Rolle |
|---|---|
| `tradinglib/four_ps.py` | Rechenkern (kein Streamlit): `compute()`, `analyze()`, `scan()`, `index_regime()`, `zigzag()`, CLI |
| `tradinglib/indicator/fps.py` | Overlay `fps` — lädt die volle lokale Tageshistorie, projiziert das Ergebnis auf die Chart-Zeitebene |
| `tradinglib/four_ps_page.py` | Seite (`?four_ps=true`): Index-Regime, Screener, Detail, Methode |
| `HELP/four_ps_page[_en].html` | Hilfeseite (in `system_config` unter "Hauptansichten" registriert) |
| `tests/test_four_ps.py` | Phasenlauf + Kausalitätstest auf synthetischen Kursen |

**Phasen-Logik (alles kausal):**
- Phase 1: Monats-Zickzack (25 % Umkehr) — ein Aufwärtsschub zählt erst mit
  **bestätigter** Gegenbewegung, nie am (dann noch unbekannten) Extrempunkt.
- Phase 2: längstes Wochenfenster (≥ 8 W) mit Spanne ≤ 25 % **und** nahe 52W-Hoch.
- Phase 3/4: Zustandsautomat auf **Tages**-Closes; Basislevel kommen aus
  `_to_daily(..., completed_only=True)`, also aus abgeschlossenen Wochen/Monaten
  (`PeriodIndex` + `shift(1)`) — deshalb kein Look-ahead.
- Verifiziert: `compute()` auf abgeschnittener Historie == `compute()` auf voller
  Historie für den überlappenden Teil (Test `test_no_look_ahead`, zusätzlich
  gegen echte Ticker geprüft).

**Spalten (live == Backtest):** `fps_phase` (0–4), `fps_best_trend`,
`fps_trend_gain`, `fps_base_high/_low/_weeks`, `fps_breakout`, `fps_buy`,
`fps_sell`, `fps_stop`, `fps_target`, `fps_rs`, `fps_dist_high`.
Verdrahtet in `asset_perf2.py`: `INDICATOR_BACKFILL_MAP['fps']`, `'fps'` in der
Indikatorliste von `process_symbol`, pdict-Schleife (NaN → 0.0).
**Bestandsdaten füllen:** `python asset_perf2.py /backfill:fps /force`
(+ `/year:YYYY` bzw. `/all`) — noch **nicht** gelaufen, das ist der nächste Schritt.

**Performance-Falle:** `scan()` läuft bewusst **single-threaded**
(`workers=1`). Gemessen an 60 SPX-Mitgliedern: 6,3 s mit 1 Worker, 21,6 s mit 8 —
die Arbeit ist CPU-gebunden (pandas + Zustandsautomat), Threads bringen nur
GIL-Contention. Ergebnis wird tageweise in `rotation_cache.db` persistiert
(`rotation_cache.four_ps_key`), ein SPX+DAX-Scan dauert einmalig ~70 s.

**Sonstiges:** Sidebar-Eintrag `four_ps` (Gruppe Assets, Default an), Route in
`_START_PAGE_ROUTES` + `app_edition._ROUTE_PARAMS`, 98 Locale-Keys je Sprache
(`fps.*`, `nav/page/error.four_ps`). Parameter pro Nutzer in `config.db`
(`fps_params`), Universen-Default `^GDAXI,^MDAXI,^SDAXI,^SPX` (`fps_universes`).

### 4PS-Nachbesserung: zwei Filter, die die Methode erst tragfähig machen

Erster Praxistest zeigte Kaufsignale in Abwärtstrends (Beispiel KMB 2026-07-02:
Basis 92,42–111,82, Kurs 26 % unter dem Rekordhoch, **fallender** 30W-SMA):

1. **`near_high` lief gegen das 52-Wochen-Hoch.** Das sinkt mit dem Kurs mit → nach
   einem Jahr Abwärtstrend ist „20 % unter dem 52W-Hoch" praktisch immer erfüllt.
   Jetzt gegen ein **Rekordhoch-Fenster** (`record_weeks`, Default 520 Wochen),
   Toleranz enger (`near_high_pct` 20 → 15 %).
2. **Einstieg hatte keinen Trendfilter, Ausstieg schon.** Verkauft wird bei
   `close < Wochen-SMA30`; gekauft wurde ohne diese Bedingung → Positionen, die am
   Folgetag ausstiegsreif waren. Neu `require_uptrend` (Default True): Ausbruch nur
   über dem SMA30 **und** wenn dieser über `slope_weeks` (8) steigt.

Gemessen über ^SPX+^GDAXI+^MDAXI+^SDAXI (656 Ticker, Signale seit 2015, Signal→Signal,
ohne Kosten):

| Variante | Trades | Trefferquote | Ø | PF |
|---|---|---|---|---|
| alt (52W-Hoch, kein Trendfilter) | 6566 | 37,2 % | +4,2 % | 1,96 |
| nur Rekordhoch-Filter | 4744 | 38,8 % | +4,2 % | 1,95 |
| nur Trendfilter | 5229 | 40,0 % | +4,8 % | 2,08 |
| **neu (beides)** | **4209** | **40,8 %** | **+4,4 %** | **2,02** |

Profil der Methode (neue Defaults): Median −3,3 %, Haltedauer Ø 143 Tage, ~11 % p. a.
je Position; getragen vom rechten Rand (173 Trades > +50 %, 37 > +100 %). Zusätzliche
Entry-Filter (RS > 0, `fps_best_trend` ≥ 150, Abstand zum ATH, lange Basen) brachten
**nichts** — lange Basen (≥ 20 W) sind sogar schlechter (PF 1,81).

**Datenqualität schlägt durch:** die 16 Trades unter −20 % (bis −93 %) sind
Split-Artefakte in `yf_*.db` (ORLY, MNST, FAST, VST …), keine Methodenverluste →
`tradinglib/data_quality.py` gegenprüfen, bevor Ergebnisse interpretiert werden.

### 4PS-Ausstiegsvarianten (Messung 2026-08-17)

Gleiche Einstiege, nur anderes Positionsmanagement (656 Ticker, Signale seit 2015,
Signal→Signal, ohne Kosten). Trades unter −60 % (Split-Artefakte) ausgeschlossen:

| Ausstieg | Trades | Win | Ø | PF | Haltedauer | p. a. |
|---|---|---|---|---|---|---|
| SMA20 | 5111 | 39,7 % | +2,6 % | 1,68 | 99 d | +9,6 % |
| SMA30 (Default) | 4208 | 40,8 % | +4,4 % | 2,03 | 143 d | +11,4 % |
| SMA35 | 3955 | 40,4 % | +5,3 % | 2,18 | 162 d | +12,1 % |
| SMA40 | 3721 | 39,6 % | +6,1 % | 2,32 | 181 d | +12,4 % |
| SMA45 | 3565 | 39,2 % | +6,6 % | 2,39 | 195 d | +12,4 % |
| SMA50 | 3419 | 38,9 % | +7,2 % | 2,46 | 209 d | +12,6 % |
| SMA40 + Stop 12 % | 3615 | 41,2 % | +6,6 % | 2,40 | 190 d | +12,6 % |
| SMA30 + Trailing 20 % | 4227 | 40,9 % | +4,2 % | 1,99 | 141 d | +11,0 % |
| SMA30 + Zielverkauf | 4346 | 40,5 % | +4,1 % | 1,93 | 137 d | +10,8 % |

- **Monotone Rampe, kein Peak:** PF steigt durchgehend mit der SMA-Länge, weil die
  Haltedauer proportional mitwächst — annualisiert bleibt es bei ~12 %. Also kein
  überangepasster Sweet Spot, aber auch kein großer Hebel.
- **Trailing-Stop und Gewinnmitnahme schaden** (kappen den rechten Rand) → bleiben aus.
- Robustheit von SMA40+Stop 12 % gegen SMA30+Stop 8 %: **in allen drei Teilperioden
  (2015–19, 2020–22, 2023–26) und beiden Regionen besser** (PF 6/6, Win 5/6).
  Schwächste Phase bei beiden: 2020–2022 (PF 1,76 bzw. 1,89).
- **Einschränkung:** Alles gerechnet je Position (Signal→Signal), NICHT als Portfolio
  mit begrenzten Slots. Längere Haltedauern binden Kapital — im `multi_transactions`-
  Kontext mit `num_assets` kann das die Reihenfolge der Varianten drehen.

**Entscheidung 2026-08-17:** Defaults auf **SMA40 + Stop 12 %** umgestellt (`trend_sma_weeks` 30→40, `stop_pct` 8→12) — in allen Teilperioden und Regionen besser. Trailing/Zielverkauf bleiben aus.

### 4PS: Sektor-Vergleich (2026-08-17)

Screener + Detail zeigen `sector`, `vs_sector` (eigene 52W-Rendite − Sektor-**Median**, pp)
und `sector_rank` (Perzentil im Sektor) plus Filter „nur über dem Sektor-Median“.
Engine: `four_ps.sector_map/window_return/quick_return/sector_reference/sector_context`.

- **Vergleichsgruppe = Sektor im gewählten Universum** (nicht Sektor-ETF, nicht global).
  `scan()` nutzt die beim Scoring ohnehin berechneten Renditen → keine zusätzliche IO;
  die Detailansicht baut die Referenz über `quick_return` (liest nur den Tail von
  `day_data`, ~8 s für 656 Ticker) und cached sie tageweise in `rotation_cache.db`.
- Sektoren mit < 5 Werten bleiben leer (`_MIN_PEERS`).
- **Bewusst KEINE `fps_*`-Spalte in der Sim-DB**: universumsabhängige Momentaufnahme,
  keine kausale Zeitreihe. Für Backtests bleibt `fps_rs` (gegen den Index) zuständig.

### 4PS: Spaltenerklaerung + zwei Hoch-Referenzen (2026-08-17)

Screener-Spalten sind jetzt im Tab **Methode** (`fps.columns_md`, de/en) und in beiden
HELP-Seiten erklaert. Zwei Punkte, die vorher verwirrten:

- **`bis Ausbruch %`** stand ab Phase 3 auf 0 (las sich wie „direkt am Ausloeser") →
  wird jetzt leer gelassen, sobald der Ausbruch passiert ist.
- **Zwei verschiedene Hoch-Referenzen:** `fps_dist_high` („vom Hoch %") misst gegen das
  **Allzeithoch** (`close.cummax()` ueber die volle lokale Historie), der Phase-2-Filter
  dagegen gegen das **Rekord-Fenster** (`record_weeks`, 10 Jahre). Deshalb kann ein Wert
  86 % unter seinem Hoch stehen und sich trotzdem qualifizieren (CBK.DE: Hoch 2007 bei
  282,86, aktueller Kurs 39,61, 10-Jahres-Hoch 40,13). Bewusst so gelassen — beide
  Aussagen sind fuer sich sinnvoll —, aber in Doku und Spaltentext explizit gemacht.
