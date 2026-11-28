"""Streamlit page: withdrawal simulator (Entnahme-Simulator) over the whole retirement.

Calculation in ``tradinglib.withdrawal_simulation``; this module only collects
inputs, builds the scenarios (chosen split of stocks / ETFs / commodities, own
depot as it is, ETFs & commodities only) and shows the results.
"""
from __future__ import annotations

import logging
import os
import sqlite3

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tradinglib import withdrawal_simulation as ws
from tradinglib.i18n import t as _t

logger = logging.getLogger(__name__)

_KEY = '_wd_'
_STATE_VERSION = 10          # bump when the stored result layout or SimConfig changes
PRESET_EXTRAS = ['EUNL.DE', 'VWCE.DE', 'SXR8.DE', 'EQQQ.DE', 'EXS1.DE', 'SPY', 'QQQ',
                 'GC=F', '4GLD.DE', 'SI=F', 'PL=F', 'HG=F', 'BZ=F']
DEFAULT_EXTRAS = ['EUNL.DE', 'GC=F']
DEFAULT_PROXY = 'SPY'


# ─────────────────────────────────────────────────────────────────────────────
# Cached data
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _depot(username: str) -> pd.DataFrame:
    from tradinglib import portfolio_map as pm
    try:
        df = pm.by_ticker(pm.load('own', username=username))
    except Exception:
        logger.warning('withdrawal sim: own depot not available', exc_info=True)
        return pd.DataFrame(columns=pm.COLUMNS)
    df = df[pd.to_numeric(df['positionValue'], errors='coerce') > 0]
    return df[df['ticker'].astype(str).str.lower() != 'none'].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _extra_options() -> pd.DataFrame:
    """ETFs and futures known to asset_info that have a local price DB."""
    db_dir = ws._db_dir('database')
    path = os.path.join(db_dir, 'asset_info.db')
    rows = pd.DataFrame(columns=['ticker', 'name', 'quoteType'])
    if os.path.exists(path):
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            rows = pd.read_sql_query(
                "SELECT ticker, COALESCE(longName, shortName, '') AS name, quoteType "
                "FROM asset_info WHERE quoteType IN ('ETF', 'FUTURE')", con)
        except Exception:
            logger.debug('asset_info options failed', exc_info=True)
        finally:
            con.close()
    rows = pd.concat([pd.DataFrame({'ticker': PRESET_EXTRAS}), rows]).drop_duplicates('ticker')
    rows = rows[rows['ticker'].map(lambda t: os.path.exists(os.path.join(db_dir, f'yf_{t}.db')))]
    return rows.fillna('').reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _meta(tickers: tuple) -> pd.DataFrame:
    return ws.asset_meta(list(tickers))


@st.cache_data(ttl=3600, show_spinner=False)
def _returns(tickers: tuple, currency: str):
    return ws.monthly_returns(list(tickers), system_currency=currency)


_BANKS_KEY = 'wd_banks'


def _load_banks(username: str) -> pd.DataFrame:
    """Saved fixed-deposit accounts of the user (bank, amount, rate in %)."""
    rows = None
    try:
        from tradinglib.system_config import SystemConfig
        rows = SystemConfig(username=username or 'admin').get_value(_BANKS_KEY, None)
    except Exception:
        logger.debug('could not read %s', _BANKS_KEY, exc_info=True)
    df = pd.DataFrame(rows if isinstance(rows, list) and rows else
                      [{'bank': _t('wd.bank_default'), 'amount': 100_000.0, 'rate': 2.5}])
    for c, d in (('bank', ''), ('amount', 0.0), ('rate', 0.0)):
        if c not in df.columns:
            df[c] = d
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0.0)
    df['rate'] = pd.to_numeric(df['rate'], errors='coerce').fillna(0.0)
    return df[['bank', 'amount', 'rate']]


def _bank_rows(banks: pd.DataFrame) -> list:
    return [{'bank': str(r.bank or ''), 'amount': float(r.amount),
             'rate': float(r.rate) if pd.notna(r.rate) else 0.0}
            for r in banks.itertuples() if pd.notna(r.amount)]


def _save_banks(username: str, banks: pd.DataFrame) -> None:
    from tradinglib.system_config import SystemConfig
    SystemConfig(username=username or 'admin').set_value(_BANKS_KEY, _bank_rows(banks))


# Further tables of the page, saved automatically on every change (like the settings).
_DEPOT_KEY = 'wd_depot'            # {ticker: {'use': bool, 'value': float | None}}
_CLASSES_KEY = 'wd_classes'        # {ticker: {'cls': 'stock'|'etf'|'commodity', 'inner': float}}
_BRACKETS_KEY = 'wd_tax_brackets'  # {regime: [[upper | None, rate], ...]}


def _cfg_get(username: str, key: str, default=None):
    try:
        from tradinglib.system_config import SystemConfig
        v = SystemConfig(username=username or 'admin').get_value(key, None)
        return default if v is None else v
    except Exception:
        logger.debug('could not read %s', key, exc_info=True)
        return default


def _cfg_set(username: str, key: str, value) -> None:
    try:
        from tradinglib.system_config import SystemConfig
        SystemConfig(username=username or 'admin').set_value(key, value)
    except Exception:
        logger.warning('could not save %s', key, exc_info=True)


def _saved_once(name: str, loader):
    """Saved value of a table, read from config.db once per session."""
    k = _KEY + name + '_saved'
    if k not in st.session_state:
        st.session_state[k] = loader()
    return st.session_state[k]


def _editor_base(editor_key: str, base_key: str, builder):
    """Base data of a data_editor: fixed while the editor is on screen (its edits are deltas
    to it), rebuilt only when the editor is new (first visit, back from another page)."""
    if editor_key not in st.session_state or base_key not in st.session_state:
        st.session_state[base_key] = builder()
    return st.session_state[base_key]


def _autosave(name: str, username: str, config_key: str, value) -> None:
    """Write *value* to config.db when it differs from what was saved last."""
    k = _KEY + name + '_saved'
    if value != st.session_state.get(k):
        _cfg_set(username, config_key, value)
        st.session_state[k] = value


def depot_overrides(table: pd.DataFrame, current: pd.Series) -> dict:
    """Deselected positions and changed values of the depot table, per ticker."""
    out = {}
    for r in table.itertuples(index=False):
        cur = float(current.get(r.ticker, np.nan))
        val = float(r.positionValue) if pd.notna(r.positionValue) else np.nan
        changed = np.isfinite(val) and (not np.isfinite(cur) or abs(val - cur) > 0.5)
        if not bool(r.use) or changed:
            out[str(r.ticker)] = {'use': bool(r.use), 'value': val if changed else None}
    return out


def apply_depot_overrides(table: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    t = table.copy()
    for i, tk in enumerate(t['ticker']):
        o = (overrides or {}).get(str(tk))
        if not o:
            continue
        t.iloc[i, t.columns.get_loc('use')] = bool(o.get('use', True))
        if o.get('value') is not None:
            t.iloc[i, t.columns.get_loc('positionValue')] = float(o['value'])
    return t


def class_overrides(table: pd.DataFrame, label_cls: dict, defaults_cls: pd.Series,
                    defaults_inner: pd.Series) -> dict:
    """Rows of the class table that differ from the suggested class or weight."""
    out = {}
    for r in table.itertuples(index=False):
        c = label_cls.get(r.cls, r.cls)
        inner = float(r.inner) if pd.notna(r.inner) else 0.0
        if c != defaults_cls.get(r.ticker) or abs(inner - round(float(defaults_inner.get(r.ticker, 0.0)), 1)) > 0.05:
            out[str(r.ticker)] = {'cls': c, 'inner': inner}
    return out


def apply_class_overrides(table: pd.DataFrame, overrides: dict, cls_label: dict) -> pd.DataFrame:
    t = table.copy()
    for i, tk in enumerate(t['ticker']):
        o = (overrides or {}).get(str(tk))
        if not o:
            continue
        if o.get('cls') in cls_label:
            t.iloc[i, t.columns.get_loc('cls')] = cls_label[o['cls']]
        if o.get('inner') is not None:
            t.iloc[i, t.columns.get_loc('inner')] = float(o['inner'])
    return t


def bracket_rows(br: pd.DataFrame) -> list:
    return [[(float(u) if pd.notna(u) else None), float(r)]
            for u, r in zip(br['upper'], br['rate']) if pd.notna(r)]


_TAX_KEY = 'wd_tax'
_EXPENSES_KEY = 'wd_expenses'
_EXP_COLS = ['year', 'label', 'amount', 'every', 'until']


def _load_expenses(username: str) -> pd.DataFrame:
    rows = []
    try:
        from tradinglib.system_config import SystemConfig
        v = SystemConfig(username=username or 'admin').get_value(_EXPENSES_KEY, None)
        rows = v if isinstance(v, list) else []
    except Exception:
        logger.debug('could not read %s', _EXPENSES_KEY, exc_info=True)
    df = pd.DataFrame(rows, columns=_EXP_COLS)
    for c in ('year', 'every', 'until'):
        df[c] = pd.to_numeric(df[c], errors='coerce').astype('Int64')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['label'] = df['label'].fillna('').astype(str)
    return df[_EXP_COLS]


def _expense_rows(df: pd.DataFrame) -> list:
    rows = []
    for r in df.itertuples(index=False):
        if pd.isna(r.year) or pd.isna(r.amount):
            continue
        rows.append({'year': int(r.year), 'label': str(r.label or ''), 'amount': float(r.amount),
                     'every': int(r.every) if pd.notna(r.every) else 0,
                     'until': int(r.until) if pd.notna(r.until) else None})
    return rows


def _save_expenses(username: str, rows: list) -> None:
    from tradinglib.system_config import SystemConfig
    SystemConfig(username=username or 'admin').set_value(_EXPENSES_KEY, rows)
_SETTINGS_KEY = 'wd_settings'
# session keys that are not settings: results, buttons, table editors, choices that depend on data
_NOT_SETTINGS = ('results', 'rules_cmp', 'sust', 'loaded', 'shadow', 'saved_snapshot', 'pending_spend',
                 'banks_ed', 'banks_init', 'banks_saved', 'depot_init', 'depot_saved', 'cls_init_',
                 'classes_saved', 'tax_brackets_saved',
                 'exp_ed', 'exp_saved', 'exp_init',
                 'pending_run', 'use_sust_', 'applied_sust', 'depot_ed', 'banks_ed', 'cls_ed_', 'tax_br_',
                 'chart_sc', 'restored', 'run', 'banks_save', 'tax_save', 'rules_run', 'sust_run',
                 'set_save', 'set_reset', 'stress', 'stress_run', 'stress_sc', 'stress_pick')


def _is_setting(name: str, value) -> bool:
    if any(name == x or (x.endswith('_') and name.startswith(x)) for x in _NOT_SETTINGS):
        return False
    if isinstance(value, (bool, int, float, str)):
        return True
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _collect_settings() -> dict:
    """All widget values of this page from the session (without the _wd_ prefix)."""
    return {k[len(_KEY):]: v for k, v in st.session_state.items()
            if isinstance(k, str) and k.startswith(_KEY) and _is_setting(k[len(_KEY):], v)}


def _save_settings(username: str) -> int:
    from tradinglib.system_config import SystemConfig
    data = _collect_settings()
    SystemConfig(username=username or 'admin').set_value(_SETTINGS_KEY, data)
    return len(data)


def _load_settings(username: str) -> dict:
    try:
        from tradinglib.system_config import SystemConfig
        v = SystemConfig(username=username or 'admin').get_value(_SETTINGS_KEY, None)
        return v if isinstance(v, dict) else {}
    except Exception:
        logger.debug('could not read %s', _SETTINGS_KEY, exc_info=True)
        return {}


def _apply_saved_settings(username: str) -> None:
    """Put the page's values back into the session state before the widgets are drawn.

    Two sources: the saved settings in config.db (first visit in a session) and the
    session copy ``_wd_shadow`` (coming back from another page of the app — Streamlit drops
    the state of widgets that were not drawn in a run, so without the copy every page
    switch would reset the inputs). The rerun lets the widgets pick the values up.
    """
    widgets_alive = (_KEY + 'hh') in st.session_state
    if widgets_alive:
        return
    shadow = st.session_state.get(_KEY + 'shadow')
    from_config = False
    if isinstance(shadow, dict) and shadow:
        saved = dict(shadow)
    elif not st.session_state.get(_KEY + 'loaded'):
        saved = _load_settings(username)
        from_config = True
    else:
        return
    st.session_state[_KEY + 'loaded'] = True
    if not saved:
        return
    if isinstance(saved.get('extras'), list):          # options depend on the local data
        known = set(_extra_options()['ticker'])
        saved['extras'] = [t for t in saved['extras'] if t in known]
    for k, v in saved.items():
        if _is_setting(k, v):
            st.session_state[_KEY + k] = v
    if from_config:
        st.session_state[_KEY + 'saved_snapshot'] = {k: v for k, v in saved.items() if _is_setting(k, v)}
    # Restoring is intended: skip Streamlit's one-time hint "widget created with a default value but
    # also set via Session State" (shown at most once per process anyway; private flag, guarded).
    try:
        from streamlit.elements.lib import policies as _policies
        _policies._shown_default_value_warning = True
    except Exception:
        logger.debug('could not silence the session-state default warning', exc_info=True)
    if from_config:
        st.session_state[_KEY + 'restored'] = True
    st.rerun()


def _reset_settings(username: str) -> None:
    from tradinglib.system_config import SystemConfig
    SystemConfig(username=username or 'admin').set_value(_SETTINGS_KEY, {})
    for k in [k for k in st.session_state.keys() if isinstance(k, str) and k.startswith(_KEY)
              and k not in (_KEY + 'exp_saved', _KEY + 'exp_init')]:
        del st.session_state[k]
    st.session_state[_KEY + 'loaded'] = True


def _load_tax(username: str) -> dict:
    """Saved tax settings of the user: regime plus edited brackets/allowance/exemption."""
    out = {'regime': 'de'}
    try:
        from tradinglib.system_config import SystemConfig
        v = SystemConfig(username=username or 'admin').get_value(_TAX_KEY, None)
        if isinstance(v, dict) and v.get('regime'):
            out = {'regime': v['regime'],
                   'brackets': [((float(u) if u is not None else ws.INF), float(r)) for u, r in v.get('brackets', [])],
                   'allowance': float(v.get('allowance', 0.0)),
                   'fund_exemption': float(v.get('fund_exemption', 0.0)),
                   'wealth': v.get('wealth', {}) if isinstance(v.get('wealth'), dict) else {}}
    except Exception:
        logger.debug('could not read %s', _TAX_KEY, exc_info=True)
    return out


def _save_tax(username: str, regime: str, brackets: pd.DataFrame, allowance: float, fund_ex: float,
              wealth: dict | None = None) -> None:
    from tradinglib.system_config import SystemConfig
    rows = [[(float(u) if pd.notna(u) else None), float(r) / 100.0]
            for u, r in zip(brackets['upper'], brackets['rate']) if pd.notna(r)]
    w = wealth or {}
    wealth_cfg = {}
    if regime == 'es' and w:
        wealth_cfg = {'on': bool(w.get('wealth_tax')), 'exemption': float(w.get('wt_exemption', 0.0)),
                      'bonus': float(w.get('wt_bonus', 0.0)), 'solidarity': bool(w.get('wt_solidarity', True)),
                      'other': float(w.get('wt_other', 0.0)), 'cap60': bool(w.get('wt_cap60', True)),
                      'income': float(w.get('wt_general_income', 0.0)),
                      'region': str(w.get('wt_income_region', 'avg'))}
    SystemConfig(username=username or 'admin').set_value(
        _TAX_KEY, {'regime': regime, 'brackets': rows, 'allowance': float(allowance),
                   'fund_exemption': float(fund_ex), 'wealth': wealth_cfg})


def _system_currency(username: str) -> str:
    try:
        from tradinglib.system_config import SystemConfig
        return SystemConfig(username=username).get_value('system_currency', 'EUR') or 'EUR'
    except Exception:
        return 'EUR'


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _eur(v: float, cur: str = '€') -> str:
    if v is None or not np.isfinite(v):
        return '–'
    return f"{v:,.0f} {cur}".replace(',', '.')


def _pct(v: float, digits: int = 1) -> str:
    if v is None or not np.isfinite(v):
        return '–'
    return f"{v * 100:.{digits}f} %".replace('.', ',')


def _opt(prefix: str, keys) -> dict:
    return {k: _t(f'{prefix}.{k}') for k in keys}


def _person_inputs(col, idx: int, default_age: int, default_sex: str, default_pension: float) -> ws.Person:
    k = f'{_KEY}p{idx}_'
    col.markdown(f"**{_t('wd.person', n=idx + 1)}**")
    a, b = col.columns(2)
    age = a.number_input(_t('wd.age'), 40, 100, default_age, key=k + 'age')
    sexes = _opt('wd.sex', ['m', 'f'])
    sex = b.selectbox(_t('wd.sex_label'), list(sexes), format_func=sexes.get,
                      index=0 if default_sex == 'm' else 1, key=k + 'sex')
    health = _opt('wd.health', ws.HEALTH_FACTORS)
    smoking = _opt('wd.smoking', ws.SMOKING_FACTORS)
    activity = _opt('wd.activity', ws.ACTIVITY_FACTORS)
    weight = _opt('wd.weight', ws.WEIGHT_FACTORS)
    alcohol = _opt('wd.alcohol', ws.ALCOHOL_FACTORS)
    a, b = col.columns(2)
    h = a.selectbox(_t('wd.health_label'), list(health), format_func=health.get, index=1, key=k + 'health')
    s = b.selectbox(_t('wd.smoking_label'), list(smoking), format_func=smoking.get, key=k + 'smoke')
    a, b, c = col.columns(3)
    ac = a.selectbox(_t('wd.activity_label'), list(activity), format_func=activity.get, index=1, key=k + 'act')
    we = b.selectbox(_t('wd.weight_label'), list(weight), format_func=weight.get, key=k + 'bmi')
    al = c.selectbox(_t('wd.alcohol_label'), list(alcohol), format_func=alcohol.get, key=k + 'alc')
    a, b = col.columns(2)
    pension = a.number_input(_t('wd.pension'), 0.0, 20_000.0, default_pension, step=50.0,
                             key=k + 'pension', help=_t('wd.pension_help'))
    pension_age = b.number_input(_t('wd.pension_age'), 50, 75, max(67, age) if age < 67 else age,
                                 key=k + 'page')
    return ws.Person(age=int(age), sex=sex, health=h, smoking=s, activity=ac, weight=we, alcohol=al,
                     pension=float(pension), pension_age=int(pension_age))


def _life_table(persons: list[ws.Person], improvement: float) -> pd.DataFrame:
    rows = []
    for i, p in enumerate(persons):
        avg = ws.Person.average(p.age, p.sex)
        surv = ws.survival_curve(p, improvement)
        reach = {a: (surv[a - p.age] if 0 <= a - p.age < len(surv) else 0.0) for a in (85, 90, 95, 100)}
        rows.append({
            _t('wd.lt_person'): _t('wd.person', n=i + 1),
            _t('wd.lt_factor'): f"{p.risk_factor():.2f}".replace('.', ','),
            _t('wd.lt_e_table'): f"{ws.life_expectancy(avg):.1f}".replace('.', ','),
            _t('wd.lt_e_personal'): f"{ws.life_expectancy(p, improvement):.1f}".replace('.', ','),
            _t('wd.lt_expected_age'): f"{p.age + ws.life_expectancy(p, improvement):.0f}",
            **{_t('wd.lt_reach', age=a): _pct(v, 0) for a, v in reach.items()},
        })
    if len(persons) > 1:
        joint = ws.joint_survival(persons, improvement)
        base = min(p.age for p in persons)
        rows.append({
            _t('wd.lt_person'): _t('wd.lt_joint'),
            _t('wd.lt_e_personal'): f"{joint[1:].sum() + 0.5:.1f}".replace('.', ','),
            **{_t('wd.lt_reach', age=a): _pct(joint[a - base] if a - base < len(joint) else 0.0, 0)
               for a in (85, 90, 95, 100)},
        })
    return pd.DataFrame(rows).fillna('')


# ─────────────────────────────────────────────────────────────────────────────
# Page
# ─────────────────────────────────────────────────────────────────────────────

def render_withdrawal_simulation(username: str = '') -> None:
    _apply_saved_settings(username)
    pending = st.session_state.pop(_KEY + 'pending_spend', None)
    if pending is not None:
        st.session_state[_KEY + 'spend'] = float(pending)
    st.title(_t('wd.title'))
    st.caption(_t('wd.caption', source=ws.LIFE_TABLE_SOURCE))
    a, b, c = st.columns([1, 1, 3])
    if a.button(_t('wd.set_save'), key=_KEY + 'set_save', help=_t('wd.set_save_help')):
        n = _save_settings(username)
        c.success(_t('wd.set_saved', n=n))
    if b.button(_t('wd.set_reset'), key=_KEY + 'set_reset', help=_t('wd.set_reset_help')):
        _reset_settings(username)
        st.rerun()
    if st.session_state.pop(_KEY + 'restored', False):
        c.info(_t('wd.set_restored'))
    cur = _system_currency(username)
    sym = '€' if cur == 'EUR' else cur

    # ── 1 · Household & life expectancy ────────────────────────────────────
    st.subheader(_t('wd.sec_household'))
    hh_opts = _opt('wd.household', ['single', 'couple'])
    household = st.radio(_t('wd.household_label'), list(hh_opts), format_func=hh_opts.get,
                         horizontal=True, key=_KEY + 'hh')
    cols = st.columns(2)
    persons = [_person_inputs(cols[0], 0, 65, 'm', 1_400.0)]
    if household == 'couple':
        persons.append(_person_inputs(cols[1], 1, 63, 'f', 900.0))
    else:
        cols[1].info(_t('wd.single_info'))

    a, b, c = st.columns(3)
    improvement = a.slider(_t('wd.improvement'), 0.0, 2.5, 1.0, 0.25, key=_KEY + 'impr',
                           help=_t('wd.improvement_help')) / 100.0
    horizon_opts = _opt('wd.horizon', ['life', 'fixed'])
    horizon = b.radio(_t('wd.horizon_label'), list(horizon_opts), format_func=horizon_opts.get,
                      key=_KEY + 'horizon')
    fixed_years = c.number_input(_t('wd.fixed_years'), 5, 60, 30, key=_KEY + 'fixed',
                                 disabled=horizon != 'fixed')
    st.dataframe(_life_table(persons, improvement), hide_index=True, use_container_width=True)
    st.caption(_t('wd.lt_caption', source=ws.LIFE_TABLE_SOURCE,
                  e65m=ws.DESTATIS_E65['m'], e65f=ws.DESTATIS_E65['f']))

    with st.expander(_t('wd.care_expander'), expanded=False):
        a, b, c = st.columns(3)
        care_on = a.toggle(_t('wd.care_on'), value=True, key=_KEY + 'care',
                           disabled=horizon == 'fixed')
        care_cost = b.number_input(_t('wd.care_cost', cur=sym), 0.0, 10_000.0, 2_000.0, 100.0,
                                   key=_KEY + 'care_cost', help=_t('wd.care_cost_help'))
        care_real = c.number_input(_t('wd.care_real'), 0.0, 5.0, 1.5, 0.25,
                                   key=_KEY + 'care_real', help=_t('wd.care_real_help')) / 100.0
        st.caption(_t('wd.care_caption'))

    # ── 2 · Wealth today: depot + fixed deposits ───────────────────────────
    st.subheader(_t('wd.sec_capital'))
    depot = _depot(username)
    if depot.empty:
        st.info(_t('wd.no_depot'))

    depot_weights = pd.Series(dtype=float)
    if not depot.empty:
        depot_total0 = float(depot['positionValue'].sum())
        with st.expander(_t('wd.depot_expander', n=len(depot), value=_eur(depot_total0, sym)), expanded=False):
            current_values = depot.set_index('ticker')['positionValue'].astype(float)
            saved_dep = _saved_once('depot', lambda: _cfg_get(username, _DEPOT_KEY, {}) or {})

            def _depot_base():
                base = depot[['ticker', 'longName', 'positionValue']].copy()
                base.insert(0, 'use', True)
                return apply_depot_overrides(base, saved_dep)

            ed = st.data_editor(
                _editor_base(_KEY + 'depot_ed', _KEY + 'depot_init', _depot_base),
                hide_index=True, use_container_width=True, key=_KEY + 'depot_ed',
                disabled=['ticker', 'longName'],
                column_config={
                    'use': st.column_config.CheckboxColumn(_t('wd.col_use')),
                    'ticker': _t('wd.col_ticker'), 'longName': _t('wd.col_name'),
                    'positionValue': st.column_config.NumberColumn(_t('wd.col_value', cur=sym),
                                                                   format='%.0f'),
                })
            _autosave('depot', username, _DEPOT_KEY, depot_overrides(ed, current_values))
            sel = ed[ed['use']]
            depot_weights = sel.set_index('ticker')['positionValue'].astype(float).clip(lower=0.0)
            st.caption(_t('wd.depot_caption'))
    depot_value = float(depot_weights.sum())

    st.markdown(f"**{_t('wd.banks_header')}**")
    saved_banks = _saved_once('banks', lambda: _bank_rows(_load_banks(username)))
    banks = st.data_editor(
        _editor_base(_KEY + 'banks_ed', _KEY + 'banks_init',
                     lambda: pd.DataFrame(saved_banks, columns=['bank', 'amount', 'rate'])),
        hide_index=True, use_container_width=True, num_rows='dynamic', key=_KEY + 'banks_ed',
        column_config={
            'bank': st.column_config.TextColumn(_t('wd.col_bank'), required=True),
            'amount': st.column_config.NumberColumn(_t('wd.col_amount', cur=sym), min_value=0.0,
                                                    format='%.0f', required=True),
            'rate': st.column_config.NumberColumn(_t('wd.col_rate'), min_value=0.0, max_value=20.0,
                                                  step=0.05, format='%.2f', required=True),
        })
    banks = banks.dropna(subset=['amount']).copy()
    banks['amount'] = pd.to_numeric(banks['amount'], errors='coerce').fillna(0.0).clip(lower=0.0)
    banks['rate'] = pd.to_numeric(banks['rate'], errors='coerce').fillna(0.0)
    cash_total = float(banks['amount'].sum())
    cash_rate_avg = float((banks['amount'] * banks['rate']).sum() / cash_total / 100.0) if cash_total > 0 else 0.0
    _autosave('banks', username, _BANKS_KEY, _bank_rows(banks))
    st.caption(_t('wd.banks_caption'))

    total = depot_value + cash_total
    a, b, c, d = st.columns(4)
    a.metric(_t('wd.now_depot'), _eur(depot_value, sym), _pct(depot_value / total if total else 0, 0),
             delta_color='off')
    b.metric(_t('wd.now_cash'), _eur(cash_total, sym), _pct(cash_total / total if total else 0, 0),
             delta_color='off')
    c.metric(_t('wd.now_total'), _eur(total, sym))
    d.metric(_t('wd.now_rate'), _pct(cash_rate_avg, 2))

    gain_default = 0.0
    if not depot.empty:
        g = pd.to_numeric(depot['gainPct'], errors='coerce').fillna(0.0) / 100.0
        v = depot['positionValue']
        gain_value = (v - v / (1.0 + g.clip(lower=-0.99))).sum()
        gain_default = float(np.clip(gain_value / v.sum(), 0.0, 0.95)) * 100.0 if v.sum() > 0 else 0.0
    a, b = st.columns(2)
    gain_share = a.number_input(_t('wd.gain_share'), 0.0, 95.0, round(gain_default, 1), 1.0,
                                key=_KEY + 'gain', help=_t('wd.gain_share_help')) / 100.0
    cash_follow = b.toggle(_t('wd.cash_follow'), value=False, key=_KEY + 'cashf',
                           help=_t('wd.cash_follow_help'))
    cash_spread = 0.0
    if cash_follow:
        cash_spread = b.number_input(_t('wd.cash_spread'), -3.0, 3.0, 0.0, 0.25, key=_KEY + 'cashs') / 100.0
    cash_rate = cash_rate_avg

    # ── Target split ───────────────────────────────────────────────────────
    st.subheader(_t('wd.sec_target'))
    st.caption(_t('wd.sec_target_caption'))
    opts = _extra_options()
    labels = {r.ticker: f"{r.ticker} – {r.name}" if r.name else r.ticker for r in opts.itertuples()}
    extras = st.multiselect(_t('wd.extras'), list(labels), default=[x for x in DEFAULT_EXTRAS if x in labels],
                            format_func=lambda x: labels.get(x, x), key=_KEY + 'extras',
                            help=_t('wd.extras_help'))
    extras = [x for x in extras if x not in depot_weights.index]

    all_assets = list(depot_weights.index) + extras
    cls_label = _opt('wd.cls', ws.ASSET_CLASSES)
    label_cls = {v: k for k, v in cls_label.items()}
    classes = pd.Series(dtype=object)
    inner = pd.Series(dtype=float)
    if all_assets:
        meta0 = _meta(tuple(all_assets))
        classes = pd.Series({t: ws.asset_class(t, meta0.loc[t, 'quoteType'], meta0.loc[t, 'name'])
                             for t in all_assets})
        inner0 = ws.default_inner_weights(classes, depot_weights)
        tbl = pd.DataFrame({
            'ticker': all_assets,
            'name': [meta0.loc[t, 'name'] or labels.get(t, t) for t in all_assets],
            'source': [_t('wd.src_depot') if t in depot_weights.index else _t('wd.src_extra')
                       for t in all_assets],
            'cls': [cls_label[classes[t]] for t in all_assets],
            'inner': [round(float(inner0[t]), 1) for t in all_assets],
        })
        tag = str(abs(hash(tuple(all_assets))))
        ed_key = _KEY + 'cls_ed_' + tag
        saved_cls = _saved_once('classes', lambda: _cfg_get(username, _CLASSES_KEY, {}) or {})
        default_tbl = tbl
        with st.expander(_t('wd.alloc_table', n=len(all_assets)), expanded=False):
            tbl = st.data_editor(
                _editor_base(ed_key, _KEY + 'cls_init_' + tag,
                             lambda: apply_class_overrides(default_tbl, saved_cls, cls_label)),
                hide_index=True, use_container_width=True, key=ed_key,
                disabled=['ticker', 'name', 'source'],
                column_config={
                    'ticker': _t('wd.col_ticker'), 'name': _t('wd.col_name'),
                    'source': _t('wd.col_source'),
                    'cls': st.column_config.SelectboxColumn(_t('wd.col_class'), options=list(label_cls),
                                                            required=True, help=_t('wd.col_class_help')),
                    'inner': st.column_config.NumberColumn(_t('wd.col_inner'), min_value=0.0, format='%.1f',
                                                           help=_t('wd.col_inner_help')),
                })
            st.caption(_t('wd.alloc_table_caption'))
        # keep overrides of assets that are not on screen right now (e.g. an ETF deselected for a while)
        diffs = class_overrides(tbl, label_cls, classes, inner0)
        merged = {k: v for k, v in saved_cls.items() if k not in set(all_assets)}
        merged.update(diffs)
        _autosave('classes', username, _CLASSES_KEY, merged)
        classes = pd.Series([label_cls.get(c, 'stock') for c in tbl['cls']], index=tbl['ticker'])
        inner = pd.Series(pd.to_numeric(tbl['inner'], errors='coerce').fillna(0.0).values, index=tbl['ticker'])

    cur_cash_pct = int(round(cash_total / total * 100)) if total else 20
    cash_quota = st.slider(_t('wd.cash_quota'), 0, 100, cur_cash_pct, 1, key=_KEY + 'cashq',
                           help=_t('wd.cash_quota_help')) / 100.0
    present = {c: bool(((classes == c) & (inner > 0)).any()) for c in ws.ASSET_CLASSES}
    defaults = {'stock': 60, 'etf': 25, 'commodity': 15}
    a, b, c, d = st.columns(4)
    targets = {}
    for col, cl in zip((a, b, c), ws.ASSET_CLASSES):
        targets[cl] = col.number_input(_t('wd.target', cls=cls_label[cl]), 0, 100,
                                       defaults[cl] if present[cl] else 0, 1,
                                       key=_KEY + 'tgt_' + cl, disabled=not present[cl],
                                       help=_t('wd.target_help'))
        if not present[cl]:
            targets[cl] = 0
    tsum = sum(targets.values())
    _, eff_shares = ws.class_target_weights(classes, inner, targets) if len(classes) else (None, {})
    if tsum and tsum != 100:
        d.info(_t('wd.target_scaled', sum=tsum))
    elif not tsum and len(classes):
        d.warning(_t('wd.target_zero'))

    # today vs target, per class
    if total > 0:
        now_cls = {c: float(depot_weights[[t for t in depot_weights.index if classes.get(t) == c]].sum())
                   for c in ws.ASSET_CLASSES}
        rows = []
        for c in ws.ASSET_CLASSES:
            tgt = (eff_shares or {}).get(c, 0.0) * (1.0 - cash_quota)
            rows.append({_t('wd.cmp_class'): cls_label[c],
                         _t('wd.cmp_now'): f"{_eur(now_cls[c], sym)} ({_pct(now_cls[c] / total, 0)})",
                         _t('wd.cmp_target'): f"{_eur(total * tgt, sym)} ({_pct(tgt, 0)})"})
        rows.append({_t('wd.cmp_class'): _t('wd.now_cash'),
                     _t('wd.cmp_now'): f"{_eur(cash_total, sym)} ({_pct(cash_total / total, 0)})",
                     _t('wd.cmp_target'): f"{_eur(total * cash_quota, sym)} ({_pct(cash_quota, 0)})"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(_t('wd.cmp_caption'))

    scen_opts = _opt('wd.scen', ['current', 'target', 'extras'])
    has_extras = any(classes.get(t) in ('etf', 'commodity') and inner.get(t, 0) > 0 for t in classes.index)
    available = [s for s in scen_opts
                 if (s != 'current' or total > 0)
                 and (s != 'extras' or has_extras)
                 and (s != 'target' or (len(classes) and tsum > 0) or cash_quota >= 1.0)]
    if _KEY + 'scen' in st.session_state:
        kept = [x for x in (st.session_state[_KEY + 'scen'] or []) if x in available]
        if kept:
            st.session_state[_KEY + 'scen'] = kept
        else:
            del st.session_state[_KEY + 'scen']
    scenarios = st.multiselect(_t('wd.scenarios'), available,
                               default=None if _KEY + 'scen' in st.session_state
                               else [x for x in ('current', 'target') if x in available],
                               format_func=scen_opts.get, key=_KEY + 'scen', help=_t('wd.scenarios_help'))

    cm_opts = _opt('wd.cash_mode', ['rebalance', 'bucket'])
    cash_mode = st.radio(_t('wd.cash_mode_label'), list(cm_opts), format_func=cm_opts.get,
                         horizontal=True, key=_KEY + 'cashm', help=_t('wd.cash_mode_help'))
    capital = total

    # ── 3 · Spending, inflation, taxes ─────────────────────────────────────
    st.subheader(_t('wd.sec_spending'))
    a, b, c = st.columns(3)
    spending = a.number_input(_t('wd.spending', cur=sym), 0.0, 100_000.0,
                              3_000.0 if household == 'couple' else 2_200.0, 50.0,
                              key=_KEY + 'spend', help=_t('wd.spending_help'))
    sp_opts = _opt('wd.profile', ['constant', 'declining'])
    profile = b.selectbox(_t('wd.profile_label'), list(sp_opts), format_func=sp_opts.get,
                          key=_KEY + 'profile', help=_t('wd.profile_help'))
    survivor = c.slider(_t('wd.survivor'), 30, 100, 70, 1, key=_KEY + 'surv',
                        disabled=household != 'couple', help=_t('wd.survivor_help')) / 100.0
    widow = 0.55
    if household == 'couple':
        widow = c.slider(_t('wd.widow'), 0, 100, 55, 1, key=_KEY + 'widow',
                         help=_t('wd.widow_help')) / 100.0

    # withdrawal rule
    rule_opts = _opt('wd.rule', ws.WITHDRAWAL_RULES)
    a, b, c, d = st.columns([2, 1, 1, 1])
    rule = a.selectbox(_t('wd.rule_label'), list(rule_opts), format_func=rule_opts.get,
                       key=_KEY + 'rule', help=_t('wd.rule_help'))
    rule_kw = {'withdrawal_rule': rule}
    if rule != 'fixed':
        rule_kw['spend_floor'] = b.number_input(_t('wd.spend_floor'), 10, 100, 75, 1, key=_KEY + 'floor',
                                                help=_t('wd.spend_floor_help')) / 100.0
        rule_kw['spend_ceiling'] = c.number_input(_t('wd.spend_ceiling'), 100, 200, 125, 1,
                                                  key=_KEY + 'ceil', help=_t('wd.spend_ceiling_help')) / 100.0
        if rule == 'guardrails':
            g1, g2 = d.columns(2)
            rule_kw['guard_band'] = g1.number_input(_t('wd.guard_band'), 1, 50, 20, 1, key=_KEY + 'gband',
                                                    help=_t('wd.guard_band_help')) / 100.0
            rule_kw['guard_step'] = g2.number_input(_t('wd.guard_step'), 1, 30, 10, 1, key=_KEY + 'gstep',
                                                    help=_t('wd.guard_step_help')) / 100.0
        elif rule == 'percent':
            rate0 = 12.0 * spending / capital * 100.0 if capital else 4.0
            rule_kw['percent_rate'] = d.number_input(_t('wd.percent_rate'), 0.5, 15.0,
                                                     float(round(min(max(rate0, 0.5), 15.0), 1)), 0.1,
                                                     format='%.1f', key=_KEY + 'prate',
                                                     help=_t('wd.percent_rate_help')) / 100.0
        elif rule == 'vol':
            rule_kw['vol_sensitivity'] = d.number_input(_t('wd.vol_sens'), 0.25, 3.0, 1.0, 0.25,
                                                        key=_KEY + 'vsens', help=_t('wd.vol_sens_help'))
    st.caption(_t(f'wd.rule_caption.{rule}'))

    # one-off expenses (car, renovation, …), saved automatically
    import datetime as _dt
    this_year = _dt.date.today().year
    if _KEY + 'exp_saved' not in st.session_state:
        st.session_state[_KEY + 'exp_saved'] = _expense_rows(_load_expenses(username))
    exp_rows_saved = st.session_state[_KEY + 'exp_saved']
    # the editor's base data must stay fixed while it is on screen (its edits are deltas to it);
    # it is renewed only when the editor is new (first visit, back from another page)
    if _KEY + 'exp_ed' not in st.session_state or _KEY + 'exp_init' not in st.session_state:
        st.session_state[_KEY + 'exp_init'] = list(exp_rows_saved)
    with st.expander(_t('wd.exp_expander', n=len(exp_rows_saved)), expanded=bool(exp_rows_saved)):
        exp_df = st.data_editor(
            pd.DataFrame(st.session_state[_KEY + 'exp_init'], columns=_EXP_COLS).astype(
                {'year': 'float', 'every': 'float', 'until': 'float', 'amount': 'float'}),
            hide_index=True, use_container_width=True, num_rows='dynamic', key=_KEY + 'exp_ed',
            column_config={
                'year': st.column_config.NumberColumn(_t('wd.exp_year'), min_value=this_year,
                                                      max_value=this_year + 75, step=1, format='%d',
                                                      required=True),
                'label': st.column_config.TextColumn(_t('wd.exp_label')),
                'amount': st.column_config.NumberColumn(_t('wd.exp_amount', cur=sym), min_value=0.0,
                                                        step=500.0, format='%.0f', required=True),
                'every': st.column_config.NumberColumn(_t('wd.exp_every'), min_value=0, max_value=50, step=1,
                                                       format='%d', help=_t('wd.exp_every_help')),
                'until': st.column_config.NumberColumn(_t('wd.exp_until'), min_value=this_year,
                                                       max_value=this_year + 75, step=1, format='%d',
                                                       help=_t('wd.exp_until_help')),
            })
        exp_rows = _expense_rows(exp_df)
        if exp_rows != exp_rows_saved:
            try:
                _save_expenses(username, exp_rows)
                st.session_state[_KEY + 'exp_saved'] = exp_rows
            except Exception:
                logger.warning('one-off expenses not saved', exc_info=True)
        expanded = ws.expand_expenses(exp_rows)
        if expanded:
            st.caption(_t('wd.exp_summary', n=len(expanded), total=_eur(sum(a for _, a in expanded), sym)))
        st.caption(_t('wd.exp_caption'))
    one_off = ws.expand_expenses(exp_rows)

    a, b, c, d = st.columns(4)
    infl_opts = _opt('wd.infl_mode', ['stochastic', 'constant'])
    infl_mode = a.selectbox(_t('wd.infl_mode_label'), list(infl_opts), format_func=infl_opts.get,
                            key=_KEY + 'inflm', help=_t('wd.infl_mode_help'))
    inflation = b.number_input(_t('wd.inflation'), -1.0, 15.0, 2.0, 0.1, key=_KEY + 'infl',
                               help=_t('wd.inflation_help')) / 100.0
    infl_vol = c.number_input(_t('wd.infl_vol'), 0.0, 5.0, 1.2, 0.1, key=_KEY + 'inflv',
                              disabled=infl_mode != 'stochastic', help=_t('wd.infl_vol_help')) / 100.0
    pension_real = d.number_input(_t('wd.pension_real'), -2.0, 2.0, 0.0, 0.25, key=_KEY + 'preal',
                                  help=_t('wd.pension_real_help')) / 100.0

    a, b, c = st.columns(3)
    saved_tax = _load_tax(username)
    tax_keys = ['de', 'de_kist8', 'de_kist9', 'es', 'off']
    tax_opts = _opt('wd.tax', tax_keys)
    tax = a.selectbox(_t('wd.tax_label'), tax_keys, format_func=tax_opts.get, key=_KEY + 'tax',
                      index=tax_keys.index(saved_tax['regime']) if saved_tax['regime'] in tax_keys else 0,
                      help=_t('wd.tax_help'))
    ter = b.number_input(_t('wd.ter'), 0.0, 3.0, 0.25, 0.05, key=_KEY + 'ter',
                         help=_t('wd.ter_help')) / 100.0
    preset = ws.tax_regime(tax)
    if saved_tax['regime'] == tax and saved_tax.get('brackets'):
        preset = {'brackets': saved_tax['brackets'], 'allowance': saved_tax['allowance'],
                  'fund_exemption': saved_tax['fund_exemption']}
    saved_br = _saved_once('tax_brackets', lambda: _cfg_get(username, _BRACKETS_KEY, {}) or {})
    with st.expander(_t('wd.tax_expander', regime=tax_opts[tax]), expanded=False):
        def _br_base():
            rows = saved_br.get(tax)
            if rows:
                return pd.DataFrame(rows, columns=['upper', 'rate']).astype(float)
            return pd.DataFrame({'upper': [np.nan if not np.isfinite(u) else float(u) for u, _ in preset['brackets']],
                                 'rate': [round(r * 100.0, 3) for _, r in preset['brackets']]})
        x, y = st.columns([3, 2])
        br = x.data_editor(_editor_base(_KEY + 'tax_br_' + tax, _KEY + 'tax_br_init_' + tax, _br_base),
                           hide_index=True, use_container_width=True, num_rows='dynamic',
                           key=_KEY + 'tax_br_' + tax, disabled=tax == 'off',
                           column_config={
                               'upper': st.column_config.NumberColumn(_t('wd.tax_upper', cur=sym), min_value=0.0,
                                                                      format='%.0f', help=_t('wd.tax_upper_help')),
                               'rate': st.column_config.NumberColumn(_t('wd.tax_rate_col'), min_value=0.0,
                                                                     max_value=100.0, format='%.3f',
                                                                     required=True)})
        tax_brackets = [((float(u) if pd.notna(u) else ws.INF), float(r) / 100.0)
                        for u, r in zip(br['upper'], br['rate']) if pd.notna(r)] or [(ws.INF, 0.0)]
        tax_allow = y.number_input(_t('wd.tax_allowance', cur=sym), 0.0, 100_000.0,
                                   float(preset['allowance']), 100.0, key=_KEY + 'tax_allow_' + tax,
                                   help=_t('wd.tax_allowance_help'))
        fund_ex = y.number_input(_t('wd.tax_fund_ex'), 0.0, 100.0, float(preset['fund_exemption']) * 100.0,
                                 5.0, key=_KEY + 'tax_fex_' + tax, help=_t('wd.tax_fund_ex_help')) / 100.0
        st.caption(_t(f'wd.tax_note.{tax}'))
        wt_kw = {'wealth_tax': False}
        if tax == 'es':
            swt = saved_tax.get('wealth', {}) if saved_tax['regime'] == 'es' else {}
            st.markdown(f"**{_t('wd.wt_header')}**")
            w1, w2, w3, w4 = st.columns(4)
            wt_on = w1.toggle(_t('wd.wt_on'), value=bool(swt.get('on', True)), key=_KEY + 'wt_on',
                              help=_t('wd.wt_on_help'))
            wt_ex = w2.number_input(_t('wd.wt_exemption', cur=sym), 0.0, 10_000_000.0,
                                    float(swt.get('exemption', ws.WEALTH_TAX_EXEMPTION)), 10_000.0,
                                    key=_KEY + 'wt_ex', disabled=not wt_on, help=_t('wd.wt_exemption_help'))
            wt_bonus = w3.number_input(_t('wd.wt_bonus'), 0, 100, int(round(swt.get('bonus', 0.0) * 100)), 1,
                                       key=_KEY + 'wt_bonus', disabled=not wt_on,
                                       help=_t('wd.wt_bonus_help')) / 100.0
            wt_sol = w4.toggle(_t('wd.wt_sol'), value=bool(swt.get('solidarity', True)), key=_KEY + 'wt_sol',
                               disabled=not wt_on, help=_t('wd.wt_sol_help'))
            wt_other = w1.number_input(_t('wd.wt_other', cur=sym), 0.0, 100_000_000.0,
                                       float(swt.get('other', 0.0)), 10_000.0, key=_KEY + 'wt_other',
                                       disabled=not wt_on, help=_t('wd.wt_other_help'))
            gi_default = float(round(np.mean([p.pension for p in persons]) * 12 / 0.85, -2))
            wt_cap = w3.toggle(_t('wd.wt_cap'), value=bool(swt.get('cap60', True)), key=_KEY + 'wt_cap',
                               disabled=not wt_on, help=_t('wd.wt_cap_help'))
            wt_gi = w4.number_input(_t('wd.wt_income', cur=sym), 0.0, 10_000_000.0,
                                    float(swt.get('income', gi_default)), 500.0, key=_KEY + 'wt_gi',
                                    disabled=not (wt_on and wt_cap), help=_t('wd.wt_income_help'))
            reg_opts = _opt('wd.wt_region', list(ws.IRPF_GENERAL_REGIONS))
            reg_keys = list(reg_opts)
            wt_region = w2.selectbox(_t('wd.wt_region_label'), reg_keys, format_func=reg_opts.get,
                                     index=reg_keys.index(swt.get('region', 'avg')) if swt.get('region', 'avg')
                                     in reg_keys else 0, key=_KEY + 'wt_region',
                                     disabled=not (wt_on and wt_cap), help=_t('wd.wt_region_help'))
            n_p = max(len(persons), 1)
            per_now = (total + wt_other) / n_p
            now_tax = 0.0
            if wt_on:
                now_pp = ws.wealth_tax_es(per_now, wt_ex, None, wt_bonus, wt_sol)
                if wt_cap:
                    now_pp = ws.wealth_tax_cap_es(now_pp, wt_gi, cash_total * cash_rate_avg / n_p, tax_brackets,
                                                  wt_region)
                now_tax = float(now_pp) * n_p
            w2.metric(_t('wd.wt_now'), _eur(now_tax, sym),
                      help=_t('wd.wt_now_help', per=_eur(per_now, sym), n=len(persons)))
            st.caption(_t('wd.wt_caption'))
            wt_kw = {'wealth_tax': wt_on, 'wt_exemption': wt_ex, 'wt_bonus': wt_bonus,
                     'wt_solidarity': wt_sol, 'wt_other': wt_other, 'wt_cap60': wt_cap,
                     'wt_general_income': wt_gi, 'wt_income_region': wt_region}
        # brackets are saved per regime as soon as they differ from the preset
        rows_now = bracket_rows(br)
        preset_rows = [[(None if not np.isfinite(u) else float(u)), round(r * 100.0, 3)]
                       for u, r in preset['brackets']]
        new_br = dict(saved_br)
        if rows_now != preset_rows or tax in saved_br:
            new_br[tax] = rows_now
        _autosave('tax_brackets', username, _BRACKETS_KEY, new_br)
    br = br.dropna(subset=['rate'])
    tax_brackets = [((float(u) if pd.notna(u) else ws.INF), float(r) / 100.0)
                    for u, r in zip(br['upper'], br['rate'])] or [(ws.INF, 0.0)]

    # ── 4 · Market model ───────────────────────────────────────────────────
    with st.expander(_t('wd.sec_market'), expanded=False):
        a, b, c, d = st.columns(4)
        years = a.slider(_t('wd.hist_years'), 10, 30, 20, 1, key=_KEY + 'years', help=_t('wd.hist_years_help'))
        use_cap = b.toggle(_t('wd.cap_on'), value=True, key=_KEY + 'capon', help=_t('wd.cap_help'))
        cap = b.number_input(_t('wd.cap'), 2.0, 20.0, 8.0, 0.5, key=_KEY + 'cap', disabled=not use_cap) / 100.0
        haircut = c.number_input(_t('wd.haircut'), 0.0, 5.0, 0.0, 0.25, key=_KEY + 'haircut',
                                 help=_t('wd.haircut_help')) / 100.0
        with_div = c.toggle(_t('wd.dividends'), value=True, key=_KEY + 'div', help=_t('wd.dividends_help'))
        crash = d.selectbox(_t('wd.crash'), [0, 20, 30, 40, 50], format_func=lambda x: f"-{x} %" if x else _t('wd.crash_none'),
                            key=_KEY + 'crash', help=_t('wd.crash_help')) / 100.0
        proxy_tk = a.text_input(_t('wd.proxy'), DEFAULT_PROXY, key=_KEY + 'proxy', help=_t('wd.proxy_help')).strip()
        n_sims = d.select_slider(_t('wd.n_sims'), [500, 1000, 2000, 3000, 5000], value=2000, key=_KEY + 'n')
        seed = int(d.number_input(_t('wd.seed'), 0, 99_999, 42, key=_KEY + 'seed'))

    run = st.button(_t('wd.run'), type='primary', key=_KEY + 'run', disabled=not scenarios)
    if st.session_state.pop(_KEY + 'pending_run', False) and scenarios:
        run = True
    if not scenarios:
        st.info(_t('wd.no_scenario'))

    if run:
        try:
            _save_settings(username)
        except Exception:
            logger.warning('withdrawal settings not saved', exc_info=True)
        wanted = list(dict.fromkeys(list(depot_weights.index) + list(classes.index)))
        all_tk = list(dict.fromkeys(wanted + ([proxy_tk] if proxy_tk else [])))
        with st.spinner(_t('wd.loading')):
            ret, meta = _returns(tuple(all_tk), cur)
        if ret.empty:
            st.error(_t('wd.no_data'))
            return
        proxy = ret[proxy_tk] if proxy_tk in ret.columns else None
        assets = [t for t in wanted if t in ret.columns]
        missing = [t for t in wanted if t not in ret.columns]
        div = meta['divYield'].reindex(assets) if with_div else None
        if div is not None:
            # dividends are taxed every year: net at a typical marginal rate (30k income per person)
            r_div = float(ws.marginal_rate(30_000.0, tax_brackets))
            ex = pd.Series([fund_ex if classes.get(t) == 'etf' else 0.0 for t in assets], index=assets)
            div = div * (1.0 - r_div * (1.0 - ex))
        M, stats = ws.build_return_matrix(ret[assets], proxy, years, div, cap if use_cap else None, haircut)
        if M.empty or len(M) < 24:
            st.error(_t('wd.no_data'))
            return
        cls_m = classes.reindex(M.columns).fillna('stock')
        inner_m = inner.reindex(M.columns).fillna(0.0)
        tf = np.array([fund_ex if cls_m[t] == 'etf' else 0.0 for t in M.columns])

        wd = depot_weights.reindex(M.columns).fillna(0.0)
        wd = wd / wd.sum() if wd.sum() > 0 else wd
        w_mixed, _ = ws.class_target_weights(cls_m, inner_m, targets)
        ex_targets = {'stock': 0, 'etf': targets.get('etf', 0), 'commodity': targets.get('commodity', 0)}
        if not ex_targets['etf'] and not ex_targets['commodity']:
            ex_targets = {'stock': 0, 'etf': 1, 'commodity': 1}
        w_extras, _ = ws.class_target_weights(cls_m, inner_m, ex_targets)
        weights = {'current': wd, 'target': w_mixed, 'extras': w_extras}

        # start of each scenario: today's split as it is, or switched to the target (taxed)
        tax_rate = 0.0
        allowance = tax_allow * len(persons)
        tf_s = pd.Series(tf, index=M.columns)
        held = depot_weights.reindex(M.columns).fillna(0.0)
        starts = {}
        for sc in scenarios:
            if sc == 'current':
                quota = cash_total / total if total > 0 else 1.0
                starts[sc] = {'capital': total, 'quota': quota, 'gain': gain_share, 'tax': 0.0, 'sold': 0.0}
                continue
            target_vals = weights[sc] * total * (1.0 - cash_quota)
            sw = ws.switch_cost(held, target_vals, gain_share, tax_rate, allowance, tf_s,
                                brackets=tax_brackets, persons=len(persons))
            risky_after = float(target_vals.sum())
            starts[sc] = {'capital': total - sw['tax'], 'quota': cash_quota, 'tax': sw['tax'],
                          'sold': sw['sold'],
                          'gain': sw['kept_gain'] / risky_after if risky_after > 0 else 0.0}

        results = {}
        progress = st.progress(0.0)
        for i, sc in enumerate(scenarios):
            wv = weights[sc].values
            st0 = starts[sc]
            if wv.sum() <= 0:
                if st0['quota'] < 1.0:
                    continue
                wv = np.full(len(wv), 1.0 / max(len(wv), 1))
            cfg = ws.SimConfig(
                capital=st0['capital'], weights=wv, partial_exemption=tf, cash_quota=st0['quota'],
                cash_rate=cash_rate, cash_mode=cash_mode, cash_follows_inflation=cash_follow,
                cash_spread=cash_spread, spending=spending, spending_profile=profile,
                survivor_factor=survivor, widow_share=widow, inflation=inflation,
                inflation_mode=infl_mode, inflation_vol=infl_vol, pension_real=pension_real,
                care_real=care_real, ter=ter, tax=tax, tax_brackets=tax_brackets, tax_allowance=tax_allow,
                **wt_kw,
                initial_gain_share=st0['gain'], asset_classes=[str(c) for c in cls_m.values],
                one_off_expenses=one_off,
                care_enabled=care_on and horizon == 'life', care_cost=care_cost,
                improvement=improvement, fixed_years=int(fixed_years) if horizon == 'fixed' else None,
                crash_at_start=crash, n_sims=int(n_sims), seed=seed, persons=persons, **rule_kw)
            res = ws.simulate(cfg, M.values)
            results[sc] = {'cfg': cfg, 'res': res, 'sum': ws.summarize(res),
                           'pct': ws.wealth_percentiles(res), 'weights': weights[sc], 'start': st0}
            progress.progress((i + 1) / len(scenarios))
        progress.empty()
        st.session_state[_KEY + 'results'] = {
            'results': results, 'stats': stats, 'meta': meta, 'missing': missing, 'rule': rule,
            'window': (M.index.min(), M.index.max()), 'M': M, 'sym': sym,
            'age0': min(p.age for p in persons), 'fixed': horizon == 'fixed',
            'classes': {t: cls_label[c] for t, c in cls_m.items()}, 'version': _STATE_VERSION,
            'cls_list': [str(c) for c in cls_m.values], 'inner': inner_m.values.astype(float),
            'expenses': exp_rows,
            'cls_label': dict(cls_label)}

    current = _collect_settings()
    st.session_state[_KEY + 'shadow'] = current
    # save automatically whenever an input changed (no need to press a button)
    if current != st.session_state.get(_KEY + 'saved_snapshot'):
        try:
            _save_settings(username)
            st.session_state[_KEY + 'saved_snapshot'] = current
        except Exception:
            logger.warning('withdrawal settings not saved', exc_info=True)

    state = st.session_state.get(_KEY + 'results')
    if state and state.get('version') != _STATE_VERSION:
        for k in ('results', 'rules_cmp', 'sust', 'stress'):
            st.session_state.pop(_KEY + k, None)
        st.info(_t('wd.stale_results'))
        state = None
    if state and state['results']:
        _render_results(state, scen_opts)


_CLASS_COLORS = {'stock': '#1f78d1', 'etf': '#34a853', 'commodity': '#f9ab00', 'cash': '#80868b'}
_CLASS_FILL = {'stock': 'rgba(31,120,209,0.12)', 'etf': 'rgba(52,168,83,0.12)',
               'commodity': 'rgba(249,171,0,0.14)', 'cash': 'rgba(128,134,139,0.12)'}


def _render_class_chart(state: dict, pick: str, real: bool, sym: str) -> None:
    r = state['results'][pick]
    res, cfg = r['res'], r['cfg']
    labels = dict(state.get('cls_label', {}))
    labels['cash'] = _t('wd.now_cash')
    st.markdown(f"**{_t('wd.cls_chart_title')}**")
    view_opts = {'growth': _t('wd.cls_view.growth'), 'composition': _t('wd.cls_view.composition')}
    a, b = st.columns([3, 1])
    view = a.radio(_t('wd.cls_view_label'), list(view_opts), format_func=view_opts.get, horizontal=True,
                   key=_KEY + 'cls_view')
    pc = ws.wealth_percentiles(res, real=real)
    # show the years in which at least 10 % of the households are still alive
    last_year = int(pc.loc[pc['alive'] >= 0.10, 'year'].max()) if (pc['alive'] >= 0.10).any() else int(pc['year'].max())
    age0 = state['age0']
    fig = go.Figure()
    if view == 'growth':
        bands = b.toggle(_t('wd.cls_bands'), value=False, key=_KEY + 'cls_bands')
        growth = ws.class_growth(res, state['M'].values, state.get('cls_list', []), state.get('inner', []),
                                 cfg, real=real)
        for cl, df in growth.items():
            df = df[df['year'] <= last_year]
            x = age0 + df['year']
            if bands:
                fig.add_trace(go.Scatter(x=x, y=df['p90'], line=dict(width=0), showlegend=False,
                                         hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=x, y=df['p10'], fill='tonexty', fillcolor=_CLASS_FILL[cl],
                                         line=dict(width=0), showlegend=False, hoverinfo='skip'))
            fig.add_trace(go.Scatter(x=x, y=df['p50'], name=labels.get(cl, cl),
                                     line=dict(color=_CLASS_COLORS[cl], width=3)))
        fig.add_hline(y=1.0, line_dash='dot', line_color='#999')
        fig.update_layout(yaxis=dict(title=_t('wd.cls_y_growth_real' if real else 'wd.cls_y_growth'),
                                     rangemode='tozero'))
        caption = _t('wd.cls_caption_growth')
    else:
        wd_opts = {'with': _t('wd.cls_wd.with'), 'without': _t('wd.cls_wd.without'),
                   'both': _t('wd.cls_wd.both')}
        wd_mode = b.selectbox(_t('wd.cls_wd_label'), list(wd_opts), format_func=wd_opts.get,
                              key=_KEY + 'cls_wd', help=_t('wd.cls_wd_help'))
        res_use = res
        if wd_mode in ('without', 'both'):
            cache = state.setdefault('nowd', {})
            if pick not in cache:
                with st.spinner(_t('wd.cls_wd_running')):
                    cache[pick] = ws.simulate(ws.without_withdrawals(cfg), state['M'].values)
            res_free = cache[pick]
            if wd_mode == 'without':
                res_use = res_free
        comp = ws.composition_percentiles(res_use, real=real)
        comp = comp[comp['year'] <= last_year]
        x = age0 + comp['year']
        for cl in ws.CHART_CLASSES:
            if not np.nan_to_num(comp[cl].values).any():
                continue
            fig.add_trace(go.Scatter(x=x, y=comp[cl].fillna(0.0), name=labels.get(cl, cl), stackgroup='one',
                                     line=dict(color=_CLASS_COLORS[cl], width=0.5)))
        if wd_mode == 'both':
            tot_free = ws.composition_total(res_free, real=real)
            tot_with = ws.composition_total(res, real=real)
            keep = tot_free.index <= last_year
            fig.add_trace(go.Scatter(x=age0 + tot_free.index[keep], y=tot_free[keep],
                                     name=_t('wd.cls_total_without'),
                                     line=dict(color='#202124', width=2.5, dash='dash')))
            fig.add_trace(go.Scatter(x=age0 + tot_with.index[keep], y=tot_with[keep],
                                     name=_t('wd.cls_total_with'), line=dict(color='#202124', width=1.5)))
        fig.update_layout(yaxis=dict(title=_t('wd.y_wealth_real' if real else 'wd.y_wealth_nominal', cur=sym),
                                     rangemode='tozero'))
        caption = _t('wd.cls_caption_composition') + ' ' + _t(f'wd.cls_wd_caption.{wd_mode}')
        nf = ws.net_flow_summary(res)
        if nf:
            amount = _eur(abs(nf['flow_p50']), sym)
            if nf['flow_p50'] < -1.0:
                st.info(_t('wd.cls_flow_in', amount=amount, years=nf['years']))
            elif nf['flow_p50'] > 1.0:
                st.info(_t('wd.cls_flow_out', amount=amount, years=nf['years']))
    fig.update_layout(height=420, hovermode='x unified', margin=dict(t=20, b=40),
                      xaxis_title=_t('wd.x_age'), legend=dict(orientation='h', y=-0.2))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(caption)


RUIN_FEW_CASES = 20      # below this many ruined paths the median age is only a rough figure


def _stress_timing() -> tuple[int, int, str]:
    """(start_year, random_years, label) from the timing widgets."""
    when = st.session_state.get(_KEY + 'stress_when', 'now')
    if when == 'year':
        y = int(st.session_state.get(_KEY + 'stress_year', 5))
        return y - 1, 0, _t('wd.stress_when_year_lbl', n=y)
    if when == 'random':
        n = int(st.session_state.get(_KEY + 'stress_rand', 10))
        return 0, max(n - 1, 0), _t('wd.stress_when_random_lbl', n=n)
    return 0, 0, _t('wd.stress_when_now_lbl')


def _render_stress(state: dict, scen_opts: dict, sym: str) -> None:
    """Stress test: every chosen crisis on the same random paths as the normal run."""
    results = state['results']
    st.caption(_t('wd.stress_intro'))
    a, b, c = st.columns(3)
    sc = a.selectbox(_t('wd.scenario'), list(results), format_func=scen_opts.get, key=_KEY + 'stress_sc')
    when = b.radio(_t('wd.stress_when'), ['now', 'year', 'random'],
                   format_func=lambda k: _t(f'wd.stress_when.{k}'), key=_KEY + 'stress_when')
    if when == 'year':
        b.slider(_t('wd.stress_year'), 1, 40, 5, 1, key=_KEY + 'stress_year')
    elif when == 'random':
        b.slider(_t('wd.stress_rand'), 1, 40, 10, 1, key=_KEY + 'stress_rand', help=_t('wd.stress_rand_help'))
    recovery = c.toggle(_t('wd.stress_rec'), value=True, key=_KEY + 'stress_rec', help=_t('wd.stress_rec_help'))
    crisis_opts = _opt('wd.crisis', ws.CRISIS_ORDER)
    if _KEY + 'stress_keys' in st.session_state:      # restored setting: drop unknown keys
        st.session_state[_KEY + 'stress_keys'] = [k for k in st.session_state[_KEY + 'stress_keys']
                                                  if k in ws.CRISIS_ORDER]
    keys = st.multiselect(_t('wd.stress_keys'), list(ws.CRISIS_ORDER),
                          default=None if _KEY + 'stress_keys' in st.session_state else list(ws.CRISIS_ORDER),
                          format_func=crisis_opts.get, key=_KEY + 'stress_keys')
    custom_on = st.toggle(_t('wd.stress_custom'), value=False, key=_KEY + 'stress_custom',
                          help=_t('wd.stress_custom_help'))
    custom = None
    if custom_on:
        c1, c2, c3, c4, c5 = st.columns(5)
        custom = {
            'months': int(c1.number_input(_t('wd.stress_c_months'), 1, 120, 24, 1, key=_KEY + 'stress_c_months')),
            'stock': c2.number_input(_t('wd.stress_c_stock'), -95, 100, -50, 1, key=_KEY + 'stress_c_stock') / 100.0,
            'commodity': c3.number_input(_t('wd.stress_c_comm'), -95, 200, 0, 1, key=_KEY + 'stress_c_comm') / 100.0,
            'cash_cut': c4.number_input(_t('wd.stress_c_cash'), 0, 100, 0, 1, key=_KEY + 'stress_c_cash') / 100.0,
            'inflation': c5.number_input(_t('wd.stress_c_infl'), -10, 50, 3, 1, key=_KEY + 'stress_c_infl') / 100.0,
        }
    run_keys = list(keys) + (['custom'] if custom_on else [])
    if st.button(_t('wd.stress_run', scenario=scen_opts[sc]), key=_KEY + 'stress_run', disabled=not run_keys):
        r = results[sc]
        cfg, M = r['cfg'], state['M']
        classes = list(cfg.asset_classes or ['stock'] * M.shape[1])
        start_year, rand_years, when_lbl = _stress_timing()
        rows = {}
        prog = st.progress(0.0, text=_t('wd.stress_running'))
        for i, key in enumerate(run_keys):
            win = ws.stress_window(key, M, classes, recovery=recovery, custom=custom)
            if win is None:
                rows[key] = None
            else:
                res = ws.simulate(ws.stress_config(cfg, win, start_year, rand_years), M.values)
                rows[key] = {'win': win, 'sum': ws.summarize(res), 'pct': ws.wealth_percentiles(res),
                             'dd': ws.stress_drawdown(win, cfg.weights)}
            prog.progress((i + 1) / len(run_keys), text=_t('wd.stress_running'))
        prog.empty()
        st.session_state[_KEY + 'stress'] = {'sc': sc, 'rows': rows, 'when': when_lbl, 'start': start_year,
                                             'random': rand_years, 'recovery': recovery,
                                             'cash_quota': float(cfg.cash_quota)}
    out = st.session_state.get(_KEY + 'stress')
    if not out or out['sc'] not in results:
        st.caption(_t('wd.stress_caption'))
        return
    base = results[out['sc']]['sum']
    labels = dict(crisis_opts, custom=_t('wd.crisis.custom'))
    st.markdown(_t('wd.stress_result_head', scenario=scen_opts[out['sc']], when=out['when'],
                   rec=_t('wd.stress_rec_on') if out['recovery'] else _t('wd.stress_rec_off')))
    table = [{
        _t('wd.stress_col_crisis'): _t('wd.stress_normal'), _t('wd.stress_col_period'): '',
        _t('wd.stress_col_trough'): '', _t('wd.stress_col_end'): '', _t('wd.stress_col_infl'): '',
        _t('wd.m_success'): _pct(base['p_success']), _t('wd.stress_col_delta'): '',
        _t('wd.m_bequest'): _eur(base['bequest_real_p50'], sym),
        _t('wd.m_bequest_p10'): _eur(base['bequest_real_p10'], sym),
        _t('wd.stress_col_ruin_age'): f"{base['ruin_age_median']:.0f}" if np.isfinite(base['ruin_age_median']) else '–',
    }]
    for key, row in out['rows'].items():
        if row is None:
            table.append({_t('wd.stress_col_crisis'): labels.get(key, key),
                          _t('wd.stress_col_period'): _t('wd.stress_no_data')})
            continue
        s, win, dd = row['sum'], row['win'], row['dd']
        n_ruin = int(s.get('n_ruined', 0))
        age = f"{s['ruin_age_median']:.0f}" + (' *' if n_ruin < RUIN_FEW_CASES else '') \
            if np.isfinite(s['ruin_age_median']) else '–'
        cut = f" · {_t('wd.stress_cash_cut', p=_pct(win['cash_cut'], 0))}" if win.get('cash_cut') else ''
        table.append({
            _t('wd.stress_col_crisis'): labels.get(key, key),
            _t('wd.stress_col_period'): (win['period'] or _t('wd.stress_months', n=win['months'])) + cut,
            _t('wd.stress_col_trough'): _pct(dd['trough'] if abs(dd['trough']) >= 0.005 else 0.0, 0),
            _t('wd.stress_col_end'): _pct(dd['end'] if abs(dd['end']) >= 0.005 else 0.0, 0),
            _t('wd.stress_col_infl'): _pct(float(np.mean(win['infl'])), 1),
            _t('wd.m_success'): _pct(s['p_success']),
            _t('wd.stress_col_delta'): f"{(s['p_success'] - base['p_success']) * 100:+.1f} Pp".replace('.', ','),
            _t('wd.m_bequest'): _eur(s['bequest_real_p50'], sym),
            _t('wd.m_bequest_p10'): _eur(s['bequest_real_p10'], sym),
            _t('wd.stress_col_ruin_age'): age,
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    if any(v is None for v in out['rows'].values()):
        st.caption(_t('wd.stress_no_data_hint'))

    done = [k for k, v in out['rows'].items() if v is not None]
    if done:
        pick = st.radio(_t('wd.stress_pick'), done, format_func=lambda k: labels.get(k, k), horizontal=True,
                        key=_KEY + 'stress_pick')
        p0, p1 = results[out['sc']]['pct'], out['rows'][pick]['pct']
        x0, x1 = state['age0'] + p0['year'], state['age0'] + p1['year']
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x0, y=p0['p50'], name=_t('wd.stress_line_normal'),
                                 line=dict(color='#1f78d1', width=3)))
        fig.add_trace(go.Scatter(x=x0, y=p0['p10'], name=_t('wd.stress_line_normal_p10'),
                                 line=dict(color='#1f78d1', width=1.5, dash='dot')))
        fig.add_trace(go.Scatter(x=x1, y=p1['p50'], name=_t('wd.stress_line_crisis', crisis=labels.get(pick, pick)),
                                 line=dict(color='#d93025', width=3)))
        fig.add_trace(go.Scatter(x=x1, y=p1['p10'], name=_t('wd.stress_line_crisis_p10'),
                                 line=dict(color='#d93025', width=1.5, dash='dot')))
        win = out['rows'][pick]['win']
        if not out['random']:
            s0 = state['age0'] + out['start']
            fig.add_vrect(x0=s0, x1=s0 + win['crisis_months'] / 12.0, fillcolor='rgba(217,48,37,0.10)',
                          line_width=0, layer='below', annotation_text=_t('wd.stress_band'),
                          annotation_position='top left', annotation_font_size=10)
        fig.update_layout(height=420, hovermode='x unified', margin=dict(t=30, b=40),
                          xaxis_title=_t('wd.x_age'),
                          yaxis=dict(title=_t('wd.y_wealth_real', cur=sym), rangemode='tozero'),
                          legend=dict(orientation='h', y=-0.2))
        st.plotly_chart(fig, use_container_width=True)
    st.caption(_t('wd.stress_caption'))


def _num1(v: float) -> str:
    """One decimal, with a comma in German."""
    txt = f"{v:.1f}"
    try:
        from tradinglib import i18n
        if str(i18n.current_language()).startswith('de'):
            txt = txt.replace('.', ',')
    except Exception:
        pass
    return txt


def _year_of(month: int) -> int:
    import datetime as _dt
    today = _dt.date.today()
    return today.year + (today.month - 1 + int(month)) // 12


def _expense_marks(rows: list) -> list:
    """[(month index, label, amount)] for every occurrence of the one-off expenses."""
    out = []
    for row in rows or []:
        for m, a in ws.expand_expenses([row]):
            out.append((m, str(row.get('label') or ''), a))
    return sorted(out)


def explain_lines(r: dict, sym: str = '€', fixed: bool = False, expenses: list | None = None) -> list[str]:
    """Plain-language reading of one scenario's result (one markdown line per point)."""
    s, cfg = r['sum'], r['cfg']
    st0 = r.get('start') or {}
    lines = []
    plan, avg = cfg.spending, s.get('spend_avg_p50', np.nan)
    lines.append(_t('wd.expl_plan', plan=_eur(plan, sym), avg=_eur(avg, sym)))
    rule = getattr(cfg, 'withdrawal_rule', 'fixed')
    if rule != 'fixed':
        lines.append(_t('wd.expl_rule', rule=_t(f'wd.rule.{rule}'), floor=_pct(cfg.spend_floor, 0),
                        ceil=_pct(cfg.spend_ceiling, 0), low=_eur(plan * cfg.spend_floor, sym)))
    persons = list(cfg.persons or [])
    pens = sum(p.pension for p in persons if p.age >= p.pension_age)
    later = [p for p in persons if p.age < p.pension_age and p.pension > 0]
    if pens > 0 or later:
        line = _t('wd.expl_pension', pens=_eur(pens, sym), port=_eur(max(plan - pens, 0.0), sym))
        for p in later:
            line += ' ' + _t('wd.expl_pension_later', amount=_eur(p.pension, sym), years=p.pension_age - p.age)
        lines.append(line)
    nf = ws.net_flow_summary(r['res'])
    if nf and nf['flow_p50'] < -1.0:
        lines.append(_t('wd.expl_pays_in', amount=_eur(-nf['flow_p50'], sym), years=nf['years']))
    elif nf and nf['flow_p50'] > 1.0:
        lines.append(_t('wd.expl_takes_out', amount=_eur(nf['flow_p50'], sym), years=nf['years']))
    if st0:
        lines.append(_t('wd.expl_start', capital=_eur(st0.get('capital', cfg.capital), sym),
                        cash=_pct(st0.get('quota', cfg.cash_quota), 0)))
        if st0.get('sold'):
            if st0.get('tax', 0.0) >= 1.0:
                lines.append(_t('wd.expl_switch', sold=_eur(st0['sold'], sym), tax=_eur(st0['tax'], sym)))
            else:
                lines.append(_t('wd.expl_switch_notax', sold=_eur(st0['sold'], sym)))
    if fixed:
        lines.append(_t('wd.expl_success_fixed', succ=_pct(s['p_success']), ruin=_pct(s['p_ruin'])))
    else:
        lines.append(_t('wd.expl_success', succ=_pct(s['p_success']), ruin=_pct(s['p_ruin']),
                        death=f"{s.get('last_death_age_median', np.nan):.0f}"))
        if s['p_ruin'] > 0 and np.isfinite(s.get('ruin_age_median', np.nan)):
            n_paths = int(s.get('n_paths') or len(r['res']['ruined']))
            n_ruin = int(s.get('n_ruined', round(s['p_ruin'] * n_paths)))
            line = _t('wd.expl_ruin_age', share=_pct(s['p_ruin']), n=n_ruin, total=n_paths,
                      age=f"{s['ruin_age_median']:.0f}")
            if n_ruin < RUIN_FEW_CASES:
                line += ' ' + _t('wd.expl_ruin_few')
            lines.append(line)
    if s['bequest_real_p10'] > 0:
        lines.append(_t('wd.expl_bequest', p50=_eur(s['bequest_real_p50'], sym), p10=_eur(s['bequest_real_p10'], sym)))
    else:
        lines.append(_t('wd.expl_bequest_zero', p50=_eur(s['bequest_real_p50'], sym)))
    worst = s.get('spend_worst_ratio_p10', np.nan)
    if np.isfinite(worst):
        if worst <= 0.01:
            lines.append(_t('wd.expl_worst_zero'))
        elif worst < 0.995:
            lines.append(_t('wd.expl_worst', worst=_pct(worst, 0), cut=_pct(s.get('spend_cut_years', 0.0), 0)))
        else:
            lines.append(_t('wd.expl_worst_full'))
    if not fixed and 'p_care_any' in s:
        lines.append(_t('wd.expl_care', care=_pct(s['p_care_any'], 0), years=_num1(s['care_years_mean'])))
    if 'wealth_tax_year_p50' in s:
        lines.append(_t('wd.expl_wealth_tax', amount=_eur(s['wealth_tax_year_p50'], sym)))
    if st0.get('quota', 0.0) >= 0.8:
        lines.append(_t('wd.expl_much_cash', cash=_pct(st0['quota'], 0)))
    marks = _expense_marks(expenses or [])
    if marks:
        examples = ', '.join(f"{_year_of(m)} {lbl} {_eur(a, sym)}".replace('  ', ' ')
                             for m, lbl, a in marks[:3])
        lines.append(_t('wd.expl_expenses', n=len(marks), total=_eur(sum(a for _, _, a in marks), sym),
                        examples=examples))
    lines.append(_t('wd.expl_limits'))
    return lines


def _render_results(state: dict, scen_opts: dict) -> None:
    results, sym = state['results'], state['sym']
    st.subheader(_t('wd.sec_results'))
    applied = st.session_state.pop(_KEY + 'applied_sust', None)
    if applied:
        amount, scen, tgt = applied
        st.success(_t('wd.sustain_applied', amount=_eur(amount, sym), scenario=scen_opts.get(scen, scen),
                      p=f"{tgt * 100:.0f}"))
    w0, w1 = state['window']
    st.caption(_t('wd.window', start=w0.strftime('%m/%Y'), end=w1.strftime('%m/%Y'),
                  n=len(state['M'])))
    if state['missing']:
        st.warning(_t('wd.missing', tickers=', '.join(state['missing'])))

    cols = st.columns(len(results))
    for col, (sc, r) in zip(cols, results.items()):
        s = r['sum']
        col.markdown(f"#### {scen_opts[sc]}")
        st0 = r.get('start')
        if st0:
            col.caption(_t('wd.start_caption', capital=_eur(st0['capital'], sym), cash=_pct(st0['quota'], 0))
                        + (' ' + _t('wd.start_tax', tax=_eur(st0['tax'], sym), sold=_eur(st0['sold'], sym))
                           if st0.get('sold') else ''))
        col.metric(_t('wd.m_success'), _pct(s['p_success']),
                   help=_t('wd.m_success_help_fixed') if state['fixed'] else _t('wd.m_success_help'))
        col.metric(_t('wd.m_bequest'), _eur(s['bequest_real_p50'], sym),
                   help=_t('wd.m_bequest_help'))
        col.metric(_t('wd.m_bequest_p10'), _eur(s['bequest_real_p10'], sym))
        if np.isfinite(s['ruin_age_median']):
            n_paths = int(s.get('n_paths') or len(r['res']['ruined']))
            n_ruin = int(s.get('n_ruined', round(s['p_ruin'] * n_paths)))
            col.metric(_t('wd.m_ruin_age', share=_pct(s['p_ruin'])),
                       f"{s['ruin_age_median']:.0f}" + (' *' if n_ruin < RUIN_FEW_CASES else ''),
                       help=_t('wd.m_ruin_age_help', n=n_ruin, total=n_paths)
                       + (' ' + _t('wd.expl_ruin_few') if n_ruin < RUIN_FEW_CASES else ''))
        if not state['fixed']:
            col.metric(_t('wd.m_last_age'), f"{s['last_death_age_median']:.0f}")
            col.metric(_t('wd.m_care'), _pct(s['p_care_any'], 0),
                       help=_t('wd.m_care_help', years=f"{s['care_years_mean']:.1f}"))
        col.metric(_t('wd.m_spend_avg'), _eur(s['spend_avg_p50'], sym),
                   help=_t('wd.m_spend_avg_help', p10=_eur(s['spend_avg_p10'], sym)))
        if state.get('rule', 'fixed') != 'fixed':
            col.metric(_t('wd.m_spend_worst'), _pct(s['spend_worst_ratio_p10'], 0),
                       help=_t('wd.m_spend_worst_help', p50=_pct(s['spend_worst_ratio_p50'], 0),
                               cut=_pct(s['spend_cut_years'], 0)))
        if 'wealth_tax_total_p50' in s:
            col.metric(_t('wd.m_wealth_tax'), _eur(s['wealth_tax_year_p50'], sym),
                       help=_t('wd.m_wealth_tax_help', total=_eur(s['wealth_tax_total_p50'], sym),
                               p90=_eur(s['wealth_tax_total_p90'], sym)))
        col.metric(_t('wd.m_inflation'), _pct(s['inflation_mean_p50']),
                   help=_t('wd.m_inflation_help', p90=_pct(s['inflation_mean_p90']),
                           price=f"{s['price_end_p50']:.2f}"))

    # plain-language reading per scenario
    with st.expander(_t('wd.expl_expander'), expanded=False):
        for sc, r in results.items():
            st.markdown(f"**{scen_opts.get(sc, sc)}**")
            st.markdown('\n'.join('- ' + ln for ln in explain_lines(r, sym, state['fixed'], state.get('expenses'))))
        st.caption(_t('wd.expl_sustain_hint'))

    # fan chart
    pick = st.radio(_t('wd.chart_scenario'), list(results), format_func=scen_opts.get,
                    horizontal=True, key=_KEY + 'chart_sc')
    real = st.toggle(_t('wd.chart_real'), value=True, key=_KEY + 'chart_real')
    res = results[pick]['res']
    pct = ws.wealth_percentiles(res, real=real)
    x = state['age0'] + pct['year']
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=pct['p90'], line=dict(width=0), showlegend=False, hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=x, y=pct['p10'], fill='tonexty', fillcolor='rgba(31,120,209,0.12)',
                             line=dict(width=0), name=_t('wd.band_10_90')))
    fig.add_trace(go.Scatter(x=x, y=pct['p75'], line=dict(width=0), showlegend=False, hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=x, y=pct['p25'], fill='tonexty', fillcolor='rgba(31,120,209,0.25)',
                             line=dict(width=0), name=_t('wd.band_25_75')))
    fig.add_trace(go.Scatter(x=x, y=pct['p50'], line=dict(color='#1f78d1', width=3), name=_t('wd.median')))
    fig.add_trace(go.Scatter(x=x, y=pct['alive'] * 100, yaxis='y2', name=_t('wd.alive_line'),
                             line=dict(color='#80868b', dash='dot'), hovertemplate='%{y:.1f} %'))
    # cumulative share of ALL households that ran out of money while alive (own scale:
    # it is usually a few percent and would vanish on the 0-100 % axis of the alive line)
    ruin = ws.ruin_share_by_year(res).reindex(pct['year']).ffill().fillna(0.0) * 100
    ruin_top = max(5.0, float(np.ceil(ruin.max() * 1.3)))
    fig.add_trace(go.Scatter(x=x, y=ruin.values, yaxis='y3', name=_t('wd.ruin_line'),
                             line=dict(color='#d93025', width=2.5), hovertemplate='%{y:.1f} %'))
    # few households left: the median there rests on a handful of (often wealthy) survivors
    thin = pct.loc[pct['alive'] < 0.10, 'year']
    if len(thin):
        fig.add_vrect(x0=state['age0'] + float(thin.min()), x1=state['age0'] + float(pct['year'].max()),
                      fillcolor='rgba(128,134,139,0.10)', line_width=0, layer='below',
                      annotation_text=_t('wd.few_households'), annotation_position='top left',
                      annotation_font_size=10)
    # single expenses as lines (label on hover); recurring ones are listed below the chart,
    # a line for every year would bury the chart
    rows = state.get('expenses') or []
    once = _expense_marks([r for r in rows if not int(r.get('every') or 0)])
    for month, label, amount in once:
        x_age = state['age0'] + month / 12.0
        fig.add_vline(x=x_age, line_dash='dot', line_color='#9334e6', opacity=0.7)
    if once:
        fig.add_trace(go.Scatter(
            x=[state['age0'] + m / 12.0 for m, _, _ in once], y=[0] * len(once), mode='markers',
            marker=dict(symbol='triangle-up', size=11, color='#9334e6'), name=_t('wd.exp_marker'),
            text=[f"{_year_of(m)}: {lbl} {_eur(a, sym)}".replace('  ', ' ') for m, lbl, a in once],
            hovertemplate='%{text}<extra></extra>'))
    fig.update_layout(
        height=480, hovermode='x unified', margin=dict(t=30, b=40),
        xaxis_title=_t('wd.x_age'),
        yaxis=dict(title=_t('wd.y_wealth_real' if real else 'wd.y_wealth_nominal', cur=sym), rangemode='tozero'),
        xaxis=dict(domain=[0.0, 0.90]),
        yaxis2=dict(title=dict(text=_t('wd.y_alive'), font=dict(color='#80868b')), overlaying='y',
                    side='right', anchor='x', range=[0, 105], showgrid=False, tickfont=dict(color='#80868b')),
        yaxis3=dict(title=dict(text=_t('wd.y_ruin'), font=dict(color='#d93025')), overlaying='y',
                    side='right', anchor='free', position=1.0, range=[0, ruin_top], showgrid=False,
                    tickfont=dict(color='#d93025')),
        legend=dict(orientation='h', y=-0.18))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(_t('wd.chart_caption'))
    recurring = [r for r in rows if int(r.get('every') or 0) > 0]
    if recurring:
        parts = []
        for r in recurring:
            until = r.get('until')
            span = _t('wd.exp_rec_until', start=r['year'], end=until) if until else _t('wd.exp_rec_from', start=r['year'])
            every = _t('wd.exp_rec_yearly') if int(r['every']) == 1 else _t('wd.exp_rec_every', n=int(r['every']))
            parts.append(f"{every}: {r.get('label') or ''} {_eur(float(r['amount']), sym)} {span}".replace('  ', ' '))
        st.caption(_t('wd.exp_rec_caption', items='; '.join(parts)))

    # spending actually financed
    sp = ws.spending_percentiles(res, real=real)
    xs = state['age0'] + sp['year']
    f2 = go.Figure()
    f2.add_trace(go.Scatter(x=xs, y=sp['p90'], line=dict(width=0), showlegend=False, hoverinfo='skip'))
    f2.add_trace(go.Scatter(x=xs, y=sp['p10'], fill='tonexty', fillcolor='rgba(52,168,83,0.18)',
                            line=dict(width=0), name=_t('wd.band_10_90')))
    f2.add_trace(go.Scatter(x=xs, y=sp['p50'], line=dict(color='#34a853', width=3), name=_t('wd.median')))
    f2.add_trace(go.Scatter(x=xs, y=sp['plan'], line=dict(color='#888', dash='dot'), name=_t('wd.spend_plan')))
    f2.update_layout(height=340, hovermode='x unified', margin=dict(t=30, b=40),
                     title=_t('wd.spend_chart_title' if real else 'wd.spend_chart_title_nominal'),
                     xaxis_title=_t('wd.x_age'),
                     yaxis=dict(title=_t('wd.y_spend' if real else 'wd.y_spend_nominal', cur=sym),
                                rangemode='tozero'),
                     legend=dict(orientation='h', y=-0.25))
    st.plotly_chart(f2, use_container_width=True)
    st.caption(_t('wd.spend_chart_caption' if real else 'wd.spend_chart_caption_nominal'))

    # asset classes over the lifetime
    _render_class_chart(state, pick, real, sym)

    # compare withdrawal rules for the selected scenario
    with st.expander(_t('wd.rules_expander'), expanded=False):
        rule_opts = _opt('wd.rule', ws.WITHDRAWAL_RULES)
        if st.button(_t('wd.rules_run', scenario=scen_opts[pick]), key=_KEY + 'rules_run'):
            import copy
            rows = []
            with st.spinner(_t('wd.rules_running')):
                for rl in ws.WITHDRAWAL_RULES:
                    c = copy.copy(results[pick]['cfg'])
                    c.withdrawal_rule = rl
                    s = ws.summarize(ws.simulate(c, state['M'].values))
                    rows.append({
                        _t('wd.rule_label'): rule_opts[rl],
                        _t('wd.m_success'): _pct(s['p_success']),
                        _t('wd.m_spend_avg'): _eur(s['spend_avg_p50'], sym),
                        _t('wd.rc_spend_p10'): _eur(s['spend_avg_p10'], sym),
                        _t('wd.m_spend_worst'): _pct(s['spend_worst_ratio_p10'], 0),
                        _t('wd.rc_cut_years'): _pct(s['spend_cut_years'], 0),
                        _t('wd.m_bequest'): _eur(s['bequest_real_p50'], sym),
                    })
            st.session_state[_KEY + 'rules_cmp'] = (pick, pd.DataFrame(rows))
        cmp_ = st.session_state.get(_KEY + 'rules_cmp')
        if cmp_ and cmp_[0] in results:
            st.dataframe(cmp_[1], hide_index=True, use_container_width=True)
        st.caption(_t('wd.rules_caption'))

    # historical crises inserted at a chosen point of the life
    with st.expander(_t('wd.stress_expander'), expanded=False):
        _render_stress(state, scen_opts, sym)

    # sustainable spending
    with st.expander(_t('wd.sustain_expander'), expanded=False):
        a, b = st.columns([1, 2])
        # new key: the old 'sust_t' held a fraction (0.05), this one whole percent
        target = a.slider(_t('wd.sustain_target'), 1, 30, 5, 1, format='%d %%', key=_KEY + 'sust_pct',
                          help=_t('wd.sustain_target_help')) / 100.0
        if a.button(_t('wd.sustain_run'), key=_KEY + 'sust_run'):
            out = {}
            with st.spinner(_t('wd.sustain_running')):
                for sc, r in results.items():
                    # small risks need more paths, otherwise a handful of paths decides
                    n_paths = 1500 if target < 0.03 else (1000 if target < 0.06 else 600)
                    out[sc] = ws.sustainable_spending(r['cfg'], state['M'].values, target, n_sims=n_paths)
            st.session_state[_KEY + 'sust'] = (target, out)
        sust = st.session_state.get(_KEY + 'sust')
        if sust:
            tgt, out = sust
            b.dataframe(pd.DataFrame([{_t('wd.scenario'): scen_opts[k],
                                       _t('wd.sustain_col', p=f"{tgt * 100:.0f}", cur=sym): _eur(v, sym)}
                                      for k, v in out.items() if k in results]),
                        hide_index=True, use_container_width=True)
            btn_cols = b.columns(max(len([k for k in out if k in results]), 1))
            for col, (k, v) in zip(btn_cols, [(k, v) for k, v in out.items() if k in results]):
                amount = float(round(v / 10.0) * 10.0)
                if col.button(_t('wd.sustain_use', amount=_eur(amount, sym), scenario=scen_opts[k]),
                              key=_KEY + 'use_sust_' + k, help=_t('wd.sustain_use_help')):
                    st.session_state[_KEY + 'pending_spend'] = amount
                    st.session_state[_KEY + 'pending_run'] = True
                    st.session_state[_KEY + 'applied_sust'] = (amount, k, tgt)
                    st.rerun()
        st.caption(_t('wd.sustain_caption'))

    # assets
    with st.expander(_t('wd.assets_expander'), expanded=False):
        stats = state['stats'].copy()
        meta = state['meta']
        tbl = pd.DataFrame({
            _t('wd.col_ticker'): stats.index,
            _t('wd.col_name'): [meta.loc[t, 'name'] if t in meta.index else '' for t in stats.index],
            _t('wd.col_class'): [state.get('classes', {}).get(t, '') for t in stats.index],
            **{_t('wd.col_weight', sc=scen_opts[sc]): [_pct(r['weights'].get(t, 0.0)) for t in stats.index]
               for sc, r in results.items()},
            _t('wd.col_coverage'): [_pct(v, 0) for v in stats['coverage']],
            _t('wd.col_cagr_hist'): [_pct(v) for v in stats['cagr_hist']],
            _t('wd.col_cagr_used'): [_pct(v) for v in stats['cagr_used']],
            _t('wd.col_vol'): [_pct(v) for v in stats['vol']],
            _t('wd.col_worst'): [_pct(v) for v in stats['worst_12m']],
        })
        st.dataframe(tbl, hide_index=True, use_container_width=True)
        st.caption(_t('wd.assets_caption'))

    st.info(_t('wd.disclaimer'))
