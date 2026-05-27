import streamlit as st
import uuid
from tradinglib.indicator import indicator
                
class MultiCheckboxSelector:

    indicators = indicator.IndicatorLoader('./tradinglib/indicator')
    lists = [
            ('Interval', ['1m','5m','15m','30m','1h','4h','1d','3d','1wk','2wk','1mo','2mo']),
            ('Period',  ['1d','2d','1wk','2wk', '3wk','1mo', '2mo', '3mo','6mo', '1y', '2y','3y','10y','20y','50y']),
            ('Overlay', indicators.get_overlay_indicators()),
            ('Oszilator',  indicators.get_oszilator_indicators()),
        ]

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
        self.instance_name = instance_name  # Eindeutiger Name für die Session
        self.sys_conf = sys_conf

        # UUID einmalig in der Session speichern
        if f"{self.instance_name}_uuid" not in st.session_state:
            st.session_state[f"{self.instance_name}_uuid"] = uuid.uuid4().hex[:8]

        self.instance_id = st.session_state[f"{self.instance_name}_uuid"]

        # Build the set of default-selected short names from sys_conf (Overlay + Oszilator).
        # Only used for first-time initialization (when the session_state key is absent).
        defaults: set[str] = set()
        if sys_conf is not None:
            import json
            for conf_key in ('overlay', 'oszilator'):
                raw = sys_conf.get_value(conf_key, '')
                if not raw:
                    continue
                parsed = None
                try:
                    parsed = json.loads(raw)
                except Exception:
                    pass
                if parsed is None:
                    try:
                        parsed = eval(raw)  # noqa: S307 — legacy format in config DB
                    except Exception:
                        pass
                if isinstance(parsed, list):
                    defaults.update(str(n).lower() for n in parsed)

        # Session State Initialisierung für jede Option, falls sie noch nicht existiert.
        # Keys that already exist (e.g. user toggled them) are left untouched.
        for list_id, options in self.lists:
            for option in options:
                key = f"{list_id}_{option}_{self.instance_id}"
                if key not in st.session_state:
                    short = option.split(' - ')[0].lower()
                    st.session_state[key] = (list_id in ('Overlay', 'Oszilator')
                                             and short in defaults)

    def render(self):


        # Dynamische Spaltenanzahl: Maximal 3 pro Reihe für bessere Lesbarkeit
        num_columns = min(4, len(self.lists))
        col_row = self.region.empty()
        columns = col_row.columns(num_columns, gap='small')

        # Durch jede Liste iterieren und Checkboxen innerhalb von Expandern generieren
        for index, (list_id, options) in enumerate(self.lists):
            col = columns[index % num_columns]  # Spalten dynamisch zuweisen

            with col:
                with st.expander(f"{list_id}:"):
                    selected_options = []
                    for option in options:
                        # Einzigartige Keys mit Instanz-ID
                        unique_key = f"{list_id}_{option}_{self.instance_id}"
                        short_name = option.split(' - ')[0]

                        # Show ⚙ config button for any indicator with a known class
                        # (all indicators now get style_params in addition to their own params)
                        plugin_cls = None
                        if self.sys_conf and list_id in ('Overlay', 'Oszilator'):
                            plugin_cls = MultiCheckboxSelector.indicators.get_class(short_name)

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
                            c1, c2 = st.columns([0.82, 0.18])
                            checked = c1.checkbox(option, key=unique_key)
                            if c2.button("⚙", key=f"cfg_{unique_key}", help=f"Configure {short_name}"):
                                self.sys_conf.render_plugin_params(short_name, merged_params)
                        else:
                            checked = st.checkbox(option, key=unique_key)

                        # Speichern der Auswahl nur bei Änderung
                        if checked and not st.session_state[unique_key]:
                            st.session_state[unique_key] = True
                        elif not checked and st.session_state[unique_key]:
                            st.session_state[unique_key] = False

                        if st.session_state[unique_key]:
                            selected_options.append(option)
                        
    #                st.write(f"Ausgewählte Optionen ({list_id}): {', '.join(selected_options) or 'Keine'}")

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

    def render_pine_export(self, region=None) -> None:
        """Renders Pine Script export buttons for the currently selected indicators."""
        from tradinglib.pine_exporter import render_export_buttons
        r          = region if region is not None else st
        overlays   = self.get_selected_options('Overlay')
        oscillators = self.get_selected_options('Oszilator')
        if not overlays and not oscillators:
            return
        r.divider()
        r.caption("Pine Script Export für TradingView")
        render_export_buttons(overlays, oscillators, self.sys_conf, r)
