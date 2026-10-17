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

        # Bookkeeping for _is_user_change(): how often the selector was built
        # (once per script run) and which values the last render() displayed.
        self._init_key = f"{self.instance_name}_init_n"
        self._shown_key = f"{self.instance_name}_shown"
        self._shown_at_key = f"{self.instance_name}_shown_at"
        st.session_state[self._init_key] = st.session_state.get(self._init_key, 0) + 1

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

    def _is_user_change(self, key: str) -> bool:
        """True only for a real click on a checkbox the previous run displayed.

        Callbacks run before the next script run. Streamlit can fire on_change
        without a click (e.g. a rerun after a code change with runOnSave, or a
        widget re-created after a run that did not render it) -- and with the
        value "off". With per-click persistence every such "off" removed one
        stored indicator: overlay/oszilator shrank to one entry each while the
        no-plot lists grew to nearly everything (2026-10-17). Indicators now
        only persist through an explicit submit button (see
        _render_indicator_form); this guard still protects the Interval/Period
        callbacks. A change only counts when the previous run rendered this
        selector (shown_at == current init count) and the value differs from
        what that run displayed.
        """
        if st.session_state.get(self._shown_at_key) != st.session_state.get(self._init_key):
            return False
        shown = st.session_state.get(self._shown_key) or {}
        if key not in shown:
            return False
        return bool(st.session_state.get(key, False)) != shown[key]

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

    def _save_indicator_list(self, list_id: str) -> None:
        """Write the whole Overlay/Oszilator selection and its Plot flags.

        Only ever called from the "save as default" submit button — an explicit
        click. The per-checkbox writers that used to persist every toggle are
        gone: Streamlit re-fires on_change without a click, and each spurious
        "off" removed one stored indicator (2026-10-17). A full rewrite is safe
        here because a button press cannot fire spuriously.

        An empty selection is stored as []; the next session then falls back to
        the factory defaults (see _resolve_list).
        """
        if self.sys_conf is None:
            return
        conf_key = {'Overlay': 'overlay', 'Oszilator': 'oszilator'}.get(list_id)
        if not conf_key:
            return
        options = self._options_for(list_id)
        selected = [o.split(' - ')[0] for o in options
                    if st.session_state.get(f"{list_id}_{o}_{self.instance_id}", False)]
        no_plot = [o.split(' - ')[0] for o in options
                   if not st.session_state.get(f"plot_{list_id}_{o}_{self.instance_id}", True)]
        self.sys_conf.set_value(conf_key, selected)
        self.sys_conf.set_value(f'{conf_key}_no_plot', no_plot)

    def _apply_cb(self, save_list=None):
        """Submit callback: keep the submitted selection (and optionally save it)."""
        st.session_state[self._touched_key] = True
        if save_list:
            self._save_indicator_list(save_list)

    def _open_cfg_cb(self, list_id, short_name):
        """⚙ submit callback: apply the form and open the settings dialog after it."""
        st.session_state[self._touched_key] = True
        st.session_state[f"{self.instance_name}_open_cfg"] = (list_id, short_name)

    @staticmethod
    def _plugin_param_schema(plugin_cls) -> dict:
        """Style params (background) merged with the indicator's own params."""
        from tradinglib.indicator._indicator import _Indicator
        plugin_params = getattr(plugin_cls, 'params', {})
        # An indicator with its own color params does not need the generic picker.
        has_own_colors = any(spec.get('type') == 'color' for spec in plugin_params.values())
        base_style = {k: v for k, v in _Indicator.style_params.items()
                      if not (has_own_colors and k == 'line_color')}
        return {**base_style, **plugin_params}

    def _render_indicator_form(self, list_id, options, shown) -> None:
        """Overlay/Oszilator checkboxes inside a form — no rerun per click.

        Every click used to rerun the whole page (chart and indicators), so
        setting up a view took one full reload per checkbox. Inside the form
        nothing happens until a submit button: "apply" for this session,
        "apply + save" to make it the default. The ⚙ button is a submit button
        too (forms allow no plain buttons); it applies the form and opens the
        indicator dialog. Plot is shown for every indicator, because a form
        cannot show or hide widgets while it is being edited.
        """
        from tradinglib.i18n import t
        inst = self.instance_id
        with st.form(f"form_{list_id}_{inst}", border=False):
            for option in options:
                unique_key = f"{list_id}_{option}_{inst}"
                plot_key = f"plot_{list_id}_{option}_{inst}"
                short_name = option.split(' - ')[0]
                plugin_cls = (MultiCheckboxSelector.indicators.get_class(short_name)
                              if self.sys_conf else None)
                if plugin_cls:
                    c1, c2, c3 = st.columns([0.60, 0.15, 0.25])
                else:
                    (c1, c3), c2 = st.columns([0.70, 0.30]), None
                shown[unique_key] = bool(c1.checkbox(option, key=unique_key))
                if c2 is not None:
                    c2.form_submit_button("⚙", key=f"cfg_{unique_key}", help=f"Configure {short_name}",
                                          on_click=self._open_cfg_cb, args=(list_id, short_name))
                shown[plot_key] = bool(c3.checkbox("Plot", key=plot_key))
            b1, b2 = st.columns(2)
            b1.form_submit_button(t('selector.apply'), key=f"apply_{list_id}_{inst}",
                                  on_click=self._apply_cb, use_container_width=True)
            if self.sys_conf is not None:
                b2.form_submit_button(t('selector.apply_save'), key=f"save_{list_id}_{inst}",
                                      on_click=self._apply_cb, args=(list_id,),
                                      use_container_width=True, help=t('selector.apply_save_help'))

        # ⚙ pressed in this form: open the dialog outside the form.
        flag = f"{self.instance_name}_open_cfg"
        pending = st.session_state.get(flag)
        if pending and pending[0] == list_id and self.sys_conf is not None:
            st.session_state.pop(flag, None)
            plugin_cls = MultiCheckboxSelector.indicators.get_class(pending[1])
            if plugin_cls:
                self.sys_conf.render_plugin_params(pending[1], self._plugin_param_schema(plugin_cls))

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

        # Values this render displays -- the reference for _is_user_change().
        shown = {}

        # Iterate over each list and generate checkboxes inside expanders
        for index, (list_id, options) in enumerate(self.lists):
            col = columns[index % num_columns]  # Assign columns dynamically

            with col:
                with st.expander(f"{list_id}:"):
                    if list_id == 'Period' and max_valid_period:
                        st.caption(f"max. {max_valid_period} · Intervall {current_interval}")
                    if list_id in ('Overlay', 'Oszilator'):
                        self._render_indicator_form(list_id, options, shown)
                        continue

                    # Callback for radio-button behaviour (Interval / Period) + config persistence
                    def _make_single_select_cb(lid, opt, inst_id, opts, selector_self):
                        def _cb():
                            genuine = selector_self._is_user_change(f"{lid}_{opt}_{inst_id}")
                            if genuine:
                                st.session_state[selector_self._touched_key] = True
                            if st.session_state.get(f"{lid}_{opt}_{inst_id}"):
                                for o in opts:
                                    if o != opt:
                                        st.session_state[f"{lid}_{o}_{inst_id}"] = False
                            if genuine:
                                selector_self._save_to_config(lid)
                        return _cb

                    for option in options:
                        unique_key = f"{list_id}_{option}_{self.instance_id}"
                        cb_kwargs = {}
                        if list_id in ('Interval', 'Period'):
                            cb_kwargs['on_change'] = _make_single_select_cb(
                                list_id, option, self.instance_id, options, self)
                        # Periods that would exceed MAX_BARS candles for the
                        # current interval are locked (not selectable).
                        if list_id == 'Period' and option in invalid_periods:
                            cb_kwargs['disabled'] = True
                            cb_kwargs['help'] = (
                                f"> {self.MAX_BARS} bars at interval {current_interval} — "
                                f"choose a smaller period or a larger interval"
                            )
                        shown[unique_key] = bool(st.checkbox(option, key=unique_key, **cb_kwargs))

        st.session_state[self._shown_key] = shown
        st.session_state[self._shown_at_key] = st.session_state.get(self._init_key)
                        
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
