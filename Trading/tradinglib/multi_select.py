import ast
import os
import re
import streamlit as st
import uuid
from tradinglib.indicator import indicator

class MultiCheckboxSelector:

    # Bar budget: more candles than this overloads the frontend (Plotly) and makes
    # nonsensical combinations like 1m/10y unselectable in the first place. 800 ~= 3 years
    # of daily data (3*252) + buffer -> at interval 1d, 3y is therefore the largest
    # valid period.
    MAX_BARS = 800

    indicators = indicator.IndicatorLoader(os.path.dirname(os.path.abspath(indicator.__file__)))
    lists = [
            ('Interval', ['1m','5m','15m','30m','1h','4h','1d','3d','1wk','2wk','1mo','2mo']),
            ('Period',  ['1d','2d','1wk','2wk', '3wk','1mo', '2mo', '3mo','6mo', '1y', '2y','3y','10y','20y','50y']),
            ('Overlay', indicators.get_overlay_indicators()),
            ('Oszilator',  indicators.get_oszilator_indicators()),
        ]

    # Relative column widths for render(): Overlay/Oszilator get twice the
    # width of Interval/Period (12.5% / 12.5% / 25% / 25% of the row).
    COLUMN_WEIGHTS = {'Interval': 1, 'Period': 1, 'Overlay': 2, 'Oszilator': 2}

    def __init__(self, list_data = None, instance_name = '1', region = st, sys_conf = None):
        """
        list_data: List of tuples with (list ID, options list)
        instance_name: A unique name for the instance (e.g. 'selector1')
        sys_conf: SystemConfig instance — required to show ⚙ buttons for plugins with params
                  and to pre-select stored overlay/oscillator defaults on first load.
        """
        if not list_data == None:
            self.lists = list_data

        self.region = region
        self.instance_name = instance_name  # Unique name for the session
        self.sys_conf = sys_conf

        # Store UUID once per session
        if f"{self.instance_name}_uuid" not in st.session_state:
            st.session_state[f"{self.instance_name}_uuid"] = uuid.uuid4().hex[:8]

        self.instance_id = st.session_state[f"{self.instance_name}_uuid"]

        # ------------------------------------------------------------------ #
        # Resolve defaults from sys_conf for all four selectors.            #
        # Kept separate per list so the post-init guarantee can use the     #
        # actual user-configured values, not just hardcoded factory ones.   #
        # ------------------------------------------------------------------ #
        import json as _json

        _FALLBACK_OVERLAY   = ['heikin', 'bar', 'sup']
        _FALLBACK_OSZILATOR = ['macd']

        def _resolve_list(conf_key: str, fallback: list) -> set:
            """Read conf_key from sys_conf and return a set of lowercased names.
            Falls back to *fallback* when the key is absent or the value is empty."""
            if sys_conf is None:
                return {s.lower() for s in fallback}
            raw = sys_conf.get_value(conf_key, None)
            if not raw:          # None, [], or '' → use factory fallback
                return {s.lower() for s in fallback}
            # get_value already json-decodes, so raw may already be a Python list.
            if isinstance(raw, list):
                parsed = raw
            else:
                parsed = None
                try:
                    parsed = _json.loads(raw)
                except Exception:
                    pass
                if not isinstance(parsed, list):
                    try:
                        parsed = ast.literal_eval(raw)
                    except Exception:
                        parsed = None
            if isinstance(parsed, list) and parsed:
                return {str(n).lower() for n in parsed}
            return {s.lower() for s in fallback}   # value was empty list → factory

        defaults_overlay   = _resolve_list('overlay',   _FALLBACK_OVERLAY)
        defaults_oszilator = _resolve_list('oszilator',  _FALLBACK_OSZILATOR)
        defaults           = defaults_overlay | defaults_oszilator   # combined for the init loop

        # Load persisted "no plot" sets from config.db
        _no_plot_sets = {
            'overlay_no_plot':   _resolve_list('overlay_no_plot',   []),
            'oszilator_no_plot': _resolve_list('oszilator_no_plot', []),
        }

        # Read defaults for Interval and Period from sys_conf.
        # Use `or fallback` so an empty string stored in the DB never wins.
        default_interval = (sys_conf.get_value('interval', '1d') or '1d') if sys_conf else '1d'
        default_period   = (sys_conf.get_value('period',   '1mo') or '1mo') if sys_conf else '1mo'

        # ------------------------------------------------------------------ #
        # "Touched" guard: as long as the user has not actively toggled a     #
        # checkbox in this session, re-assert the config defaults on EVERY    #
        # rerun. This prevents a stale/partial widget state (e.g. left over   #
        # from a previous navigation) from silently dropping default          #
        # overlays/oscillators — the bug where the chart sometimes rendered   #
        # without Heikin/oscillators despite a clean config.                  #
        # Once the user changes a selection the callbacks set the flag and    #
        # their choice is preserved for the rest of the session.              #
        # ------------------------------------------------------------------ #
        self._touched_key = f"{self.instance_name}_touched"
        force_defaults = not st.session_state.get(self._touched_key, False)

        # ------------------------------------------------------------------ #
        # Session-State initialisation. Keys missing → set them; additionally #
        # re-assert them every run while the selector is still untouched.     #
        # ------------------------------------------------------------------ #
        for list_id, options in self.lists:
            for option in options:
                key = f"{list_id}_{option}_{self.instance_id}"
                if key not in st.session_state or force_defaults:
                    if list_id == 'Interval':
                        st.session_state[key] = (option == default_interval)
                    elif list_id == 'Period':
                        st.session_state[key] = (option == default_period)
                    else:
                        short = option.split(' - ')[0].lower()
                        st.session_state[key] = (list_id in ('Overlay', 'Oszilator')
                                                 and short in defaults)

                # "Plot" flag for Overlay/Oszilator: read from config.db, default True
                if list_id in ('Overlay', 'Oszilator'):
                    plot_key = f"plot_{list_id}_{option}_{self.instance_id}"
                    if plot_key not in st.session_state or force_defaults:
                        short = option.split(' - ')[0].lower()
                        conf_key = 'overlay_no_plot' if list_id == 'Overlay' else 'oszilator_no_plot'
                        no_plot_set = _no_plot_sets.get(conf_key, set())
                        st.session_state[plot_key] = short not in no_plot_set

        # ------------------------------------------------------------------ #
        # Post-init guarantee: fix stale all-False state from old buggy init.#
        # Uses the DB-derived sets so the user's own config is honoured.     #
        # ------------------------------------------------------------------ #
        _ensure = [
            ('Interval',  default_interval,    None),
            ('Period',    default_period,       None),
            ('Overlay',   None,  defaults_overlay),
            ('Oszilator', None,  defaults_oszilator),
        ]
        for lid, single_fallback, fallback_set in _ensure:
            opts = next((o for l, o in self.lists if l == lid), [])
            if not any(st.session_state.get(f"{lid}_{o}_{self.instance_id}", False) for o in opts):
                if single_fallback is not None:
                    # Radio-style: exactly one option selected
                    for o in opts:
                        st.session_state[f"{lid}_{o}_{self.instance_id}"] = (o == single_fallback)
                elif fallback_set:
                    # Multi-select: pre-tick the DB-derived (or factory) items
                    for o in opts:
                        short = o.split(' - ')[0].lower()
                        st.session_state[f"{lid}_{o}_{self.instance_id}"] = (short in fallback_set)

    def _save_to_config(self, list_id: str) -> None:
        """Persist current selections for list_id to the config DB so other sessions pick them up."""
        if self.sys_conf is None:
            return
        config_key_map = {'Overlay': 'overlay', 'Oszilator': 'oszilator', 'Interval': 'interval', 'Period': 'period'}
        config_key = config_key_map.get(list_id)
        if not config_key:
            return
        for l_id, options in self.lists:
            if l_id != list_id:
                continue
            selections = [
                option.split(' - ')[0]
                for option in options
                if st.session_state.get(f"{l_id}_{option}_{self.instance_id}", False)
            ]
            if list_id in ('Interval', 'Period'):
                # Never persist an empty value — keep the existing DB entry intact
                # so OHLCQueryPlanner always receives a valid interval/period string.
                if selections:
                    self.sys_conf.set_value(config_key, selections[0])
            else:
                # Never overwrite a non-empty DB value with an empty list.
                if selections:
                    self.sys_conf.set_value(config_key, selections)
            break

    def _save_no_plot_flag(self, list_id: str, option: str, is_plot: bool) -> None:
        """Persist a single Plot-toggle to config.db as a targeted delta.

        Updates only the toggled indicator in the stored no-plot set instead of
        rewriting the whole set from session_state. The full-set rewrite (V1's
        _save_no_plot_to_config) was the source of the "Overlay-Default-Korruption":
        Streamlit re-fires on_change when widgets are recreated after a non-render
        rerun, at which point the session_state set is partial and would overwrite
        the good config value. A single-option delta against the config-stored set
        is idempotent under such spurious re-fires, so it is safe to auto-persist.
        """
        if self.sys_conf is None:
            return
        conf_key = {'Overlay': 'overlay_no_plot', 'Oszilator': 'oszilator_no_plot'}.get(list_id)
        if not conf_key:
            return
        short = option.split(' - ')[0]
        raw = self.sys_conf.get_value(conf_key, [])
        current = [str(n) for n in raw] if isinstance(raw, list) else []
        # Rebuild preserving order; compare case-insensitively so a single name
        # never ends up duplicated with differing case.
        rest = [n for n in current if n.lower() != short.lower()]
        if not is_plot:
            rest.append(short)
        self.sys_conf.set_value(conf_key, rest)

    # ------------------------------------------------------------------ #
    # Interval x Period Guard: verhindert Overload-Kombinationen, indem   #
    # zu grosse Perioden fuer das gewaehlte Intervall gesperrt werden und #
    # die Auswahl auf die groesste gueltige Periode geklemmt wird.        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _estimate_bars(interval: str, period: str) -> float:
        """Rough number of candles that interval x period produces (trading-calendar
        approximation, same constants as OHLCQueryPlanner: 480 min/day, 252
        trading days/year). Pure estimate for the UI, not an exact counter."""
        def _parse(s):
            m = re.match(r"(\d+)([a-z]+)", s or "")
            if not m:
                return 1, (s or "")
            return int(m.group(1)), m.group(2)

        iv, iu = _parse(interval)
        pv, pu = _parse(period)
        if iv <= 0:
            iv = 1
        candles_per_day = {
            'm':  480.0 / iv,        # 8h trading = 480 minutes
            'h':  8.0 / iv,
            'd':  1.0 / iv,
            'wk': 1.0 / (5 * iv),
            'mo': 1.0 / (21 * iv),
        }.get(iu, 1.0)
        trading_days = {
            'd':  pv,
            'wk': 5 * pv,
            'mo': 21 * pv,
            'y':  252 * pv,
        }.get(pu, pv)
        return trading_days * candles_per_day

    def _options_for(self, list_id):
        """Return the option list configured for list_id (or [])."""
        return next((o for l, o in self.lists if l == list_id), [])

    def _selected_single(self, list_id):
        """Return the currently selected option for a radio-style list, or None."""
        for o in self._options_for(list_id):
            if st.session_state.get(f"{list_id}_{o}_{self.instance_id}", False):
                return o
        return None

    def _invalid_periods(self, interval):
        """Periods that would exceed MAX_BARS at the given interval."""
        return {o for o in self._options_for('Period')
                if self._estimate_bars(interval, o) > self.MAX_BARS}

    def _apply_period_limit(self):
        """Clamp the period selection to the largest valid period
        (<= MAX_BARS candles) for the currently selected interval.

        Called at the start of render() -- before widgets are instantiated --
        so a clamped value never collides with an already rendered (then locked)
        widget. Deliberately does NOT persist to config.db: the selection is
        deterministically reproducible from interval+period, and we avoid writes
        during a pure load (cf. Overlay-Default-Korruption)."""
        periods = self._options_for('Period')
        if not periods:
            return
        interval = self._selected_single('Interval') or '1d'
        valid = [o for o in periods if self._estimate_bars(interval, o) <= self.MAX_BARS]
        if not valid:
            valid = periods[:1]  # Fallback: smallest period is always allowed
        if self._selected_single('Period') in valid:
            return
        # Period list is sorted ascending -> last valid = largest.
        target = valid[-1]
        for o in periods:
            st.session_state[f"Period_{o}_{self.instance_id}"] = (o == target)

    def render(self):
        """Render all selector columns as checkboxes inside expanders with optional ⚙ config buttons."""
        st.markdown("""
        <style>
        [data-testid="stExpander"] [data-testid="stCheckbox"] {
            margin-top: -6px !important;
            margin-bottom: -6px !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        [data-testid="stExpander"] [data-testid="stCheckbox"] > label {
            padding-top: 1px !important;
            padding-bottom: 1px !important;
            min-height: 1.4rem !important;
            line-height: 1.4rem !important;
            font-size: 0.85rem !important;
        }
        [data-testid="stExpander"] [data-testid="stVerticalBlockBorderWrapper"] {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        [data-testid="stExpanderDetails"] {
            max-height: 380px;
            overflow-y: auto;
        }
        </style>
        """, unsafe_allow_html=True)

        # Interval x Period Guard: first clamp the selection to a valid period
        # (before widgets are created), then determine the periods that are too
        # large for the current interval -> these are disabled below.
        self._apply_period_limit()
        current_interval = self._selected_single('Interval') or '1d'
        invalid_periods = self._invalid_periods(current_interval)
        valid_periods = [o for o in self._options_for('Period') if o not in invalid_periods]
        max_valid_period = valid_periods[-1] if valid_periods else ''

        # Dynamic column count: maximum 3 per row for better readability
        num_columns = min(4, len(self.lists))
        col_row = self.region.empty()
        # Use the configured width ratios (Overlay/Oszilator wider than
        # Interval/Period) when every visible list has a known weight;
        # otherwise fall back to equal-width columns.
        _visible_ids = [list_id for list_id, _ in self.lists[:num_columns]]
        if all(lid in self.COLUMN_WEIGHTS for lid in _visible_ids):
            columns = col_row.columns([self.COLUMN_WEIGHTS[lid] for lid in _visible_ids], gap='small')
        else:
            columns = col_row.columns(num_columns, gap='small')

        # Iterate over each list and generate checkboxes inside expanders
        for index, (list_id, options) in enumerate(self.lists):
            col = columns[index % num_columns]  # Assign columns dynamically

            with col:
                with st.expander(f"{list_id}:"):
                    if list_id == 'Period' and max_valid_period:
                        st.caption(f"max. {max_valid_period} · Intervall {current_interval}")
                    # Callback for radio-button behaviour (Interval / Period) + config persistence
                    def _make_single_select_cb(lid, opt, inst_id, opts, selector_self):
                        def _cb():
                            st.session_state[selector_self._touched_key] = True
                            if st.session_state.get(f"{lid}_{opt}_{inst_id}"):
                                for o in opts:
                                    if o != opt:
                                        st.session_state[f"{lid}_{o}_{inst_id}"] = False
                            selector_self._save_to_config(lid)
                        return _cb

                    def _make_save_cb(lid, selector_self):
                        # Overlay/Oszilator toggles are session-scoped overrides:
                        # mark the selector as user-touched (so the init guard stops
                        # forcing config defaults) but DO NOT auto-persist to
                        # config.db. Auto-persisting from here corrupted the stored
                        # defaults, because Streamlit re-fires on_change when widgets
                        # are recreated after a non-render rerun, writing a partial
                        # selection. The configured defaults are owned by the
                        # ⚙ settings dialog only.
                        def _cb():
                            st.session_state[selector_self._touched_key] = True
                        return _cb

                    def _make_plot_cb(lid, opt, p_key, selector_self):
                        # Unlike the overlay/oscillator *selection* toggles (which stay
                        # session-scoped), the Plot flag is persisted — but via a
                        # corruption-safe single-option delta, see _save_no_plot_flag.
                        def _cb():
                            st.session_state[selector_self._touched_key] = True
                            selector_self._save_no_plot_flag(
                                lid, opt, st.session_state.get(p_key, True))
                        return _cb

                    selected_options = []
                    for option in options:
                        # Unique keys with instance ID
                        unique_key = f"{list_id}_{option}_{self.instance_id}"
                        short_name = option.split(' - ')[0]

                        # Show ⚙ config button for any indicator with a known class
                        # (all indicators now get style_params in addition to their own params)
                        plugin_cls = None
                        if self.sys_conf and list_id in ('Overlay', 'Oszilator'):
                            plugin_cls = MultiCheckboxSelector.indicators.get_class(short_name)

                        single_select = list_id in ('Interval', 'Period')
                        cb_kwargs = {}
                        if single_select:
                            cb_kwargs['on_change'] = _make_single_select_cb(
                                list_id, option, self.instance_id, options, self)
                        elif self.sys_conf:
                            cb_kwargs['on_change'] = _make_save_cb(list_id, self)

                        is_indicator = list_id in ('Overlay', 'Oszilator')
                        plot_key = f"plot_{list_id}_{option}_{self.instance_id}" if is_indicator else None

                        if plugin_cls:
                            from tradinglib.indicator._indicator import _Indicator
                            plugin_params = getattr(plugin_cls, 'params', {})
                            # If the indicator already defines its own color params, skip the
                            # generic 'line_color' from style_params to avoid a redundant picker.
                            has_own_colors = any(
                                spec.get('type') == 'color'
                                for spec in plugin_params.values()
                            )
                            base_style = {
                                k: v for k, v in _Indicator.style_params.items()
                                if not (has_own_colors and k == 'line_color')
                            }
                            # Merge style params (background) with indicator-specific params (foreground)
                            merged_params = {
                                **base_style,
                                **plugin_params,
                            }
                            c1, c2, c3 = st.columns([0.60, 0.15, 0.25])
                            checked = c1.checkbox(option, key=unique_key, **cb_kwargs)
                            if c2.button("⚙", key=f"cfg_{unique_key}", help=f"Configure {short_name}"):
                                self.sys_conf.render_plugin_params(short_name, merged_params)
                            if checked:
                                c3.checkbox("Plot", key=plot_key,
                                            on_change=_make_plot_cb(list_id, option, plot_key, self))
                        elif is_indicator:
                            c1, c2 = st.columns([0.70, 0.30])
                            checked = c1.checkbox(option, key=unique_key, **cb_kwargs)
                            if checked:
                                c2.checkbox("Plot", key=plot_key,
                                            on_change=_make_plot_cb(list_id, option, plot_key, self))
                        else:
                            # Periods that would exceed MAX_BARS candles for the
                            # current interval are locked (not selectable).
                            is_over_budget = (list_id == 'Period' and option in invalid_periods)
                            if is_over_budget:
                                cb_kwargs['disabled'] = True
                                cb_kwargs['help'] = (
                                    f"> {self.MAX_BARS} bars at interval {current_interval} — "
                                    f"choose a smaller period or a larger interval"
                                )
                            checked = st.checkbox(option, key=unique_key, **cb_kwargs)

                        # Save selection only on change
                        if checked and not st.session_state[unique_key]:
                            st.session_state[unique_key] = True
                        elif not checked and st.session_state[unique_key]:
                            st.session_state[unique_key] = False

                        if st.session_state[unique_key]:
                            selected_options.append(option)
                        
    #                st.write(f"Selected options ({list_id}): {', '.join(selected_options) or 'None'}")

    def get_selected_options(self, list_id=None):
        """
        Returns a list of the selected options.
        - If list_id is specified, only the selected options in this list are returned.
        - Without a parameter, it returns all selected options.        """
        selected = {}
        for l_id, options in self.lists:
            selected_options = []
            for option in options:
                key = f"{l_id}_{option}_{self.instance_id}"
                if st.session_state.get(key, False):
                    selected_options.append(option.split(' - ')[0])

            if list_id is None or l_id == list_id:
                selected[l_id] = selected_options

        return selected if list_id is None else selected.get(list_id, [])

    def get_plot_options(self, list_id=None):
        """
        Returns selected indicators that have the "Plot" checkbox enabled.
        Only meaningful for Overlay and Oszilator lists.
        """
        result = {}
        for l_id, options in self.lists:
            if l_id not in ('Overlay', 'Oszilator'):
                continue
            plot_list = []
            for option in options:
                sel_key = f"{l_id}_{option}_{self.instance_id}"
                plot_key = f"plot_{l_id}_{option}_{self.instance_id}"
                if st.session_state.get(sel_key, False) and st.session_state.get(plot_key, True):
                    plot_list.append(option.split(' - ')[0])
            if list_id is None or l_id == list_id:
                result[l_id] = plot_list
        return result if list_id is None else result.get(list_id, [])

    def render_pine_export(self, region=None) -> None:
        """Renders Pine Script export buttons for the currently selected indicators."""
        from tradinglib.i18n import t
        _p_expander = (region or st).expander(t("pine.expander_title"))
        with _p_expander:
            from tradinglib.pine_exporter import render_export_buttons
            r          = region if region is not None else st
            overlays   = self.get_selected_options('Overlay')
            oscillators = self.get_selected_options('Oszilator')
            if not overlays and not oscillators:
                return
            r.divider()
            r.caption(t("pine.sidebar_caption"))
            render_export_buttons(overlays, oscillators, self.sys_conf, r)
