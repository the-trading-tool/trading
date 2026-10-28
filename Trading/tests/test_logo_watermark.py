"""Logo as watermark behind the main area: settings and generated CSS."""
import base64
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib import banner_page as bp


class _Conf:
    def __init__(self, **values):
        self.values = values

    def get_value(self, key, default=None):
        return self.values.get(key, default)


def test_standard_ist_wasserzeichen_ohne_seitenleiste():
    assert bp.logo_settings(_Conf()) == ('watermark', 40, 6)


def test_werte_werden_begrenzt_und_unbekanntes_faellt_zurueck():
    conf = _Conf(logo_mode='banner', logo_watermark_size='200', logo_watermark_opacity='abc')
    assert bp.logo_settings(conf) == ('watermark', 90, 6)
    assert bp.logo_settings(_Conf(logo_mode='both', logo_watermark_size=5,
                                  logo_watermark_opacity=0)) == ('both', 10, 1)


def test_css_enthaelt_gueltiges_svg_mit_deckkraft():
    css = bp.watermark_css(35, 8)
    assert css.startswith('[data-testid="stMain"]{') and 'background-size:auto 35vh' in css
    data = re.search(r'base64,([A-Za-z0-9+/=]+)', css).group(1)
    root = ET.fromstring(base64.b64decode(data).decode('utf-8'))
    group = root.find('{http://www.w3.org/2000/svg}g')
    assert group is not None and group.get('opacity') == '0.08'
    assert len(list(group)) > 5                                   # the whole logo sits inside


def test_texte_in_beiden_sprachen():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    keys = ['cfg.logo_mode', 'cfg.logo_mode_help', 'cfg.logo_size', 'cfg.logo_size_help',
            'cfg.logo_opacity', 'cfg.logo_opacity_help'] + [f'cfg.logo_mode_opt.{m}' for m in bp.LOGO_MODES]
    for lang in ('de', 'en'):
        with open(os.path.join(here, 'locales', f'{lang}.json'), encoding='utf-8') as f:
            texts = json.load(f)
        missing = [k for k in keys if k not in texts]
        assert not missing, (lang, missing)
