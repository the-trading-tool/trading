"""config.yaml guard: concurrent cookie logins must never read a truncated file."""
import threading

import yaml

from tradinglib import auth_config_io as aci


def _config(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.dump({'credentials': {'usernames': {'kurt': {'logged_in': False}}},
                               'cookie': {'name': 'c', 'key': 'k', 'expiry_days': 30}}),
                    encoding='utf-8')
    return str(path)


def test_update_keeps_other_keys(tmp_path):
    path = _config(tmp_path)
    aci.update_config(path, 'credentials', {'usernames': {'kurt': {'logged_in': True}}})
    config = aci.read_config(path)
    assert config['credentials']['usernames']['kurt']['logged_in'] is True
    assert config['cookie']['expiry_days'] == 30
    assert [p.name for p in tmp_path.iterdir()] == ['config.yaml']       # no temp left


def test_installed_helpers_survive_parallel_logins(tmp_path):
    aci.install()
    from streamlit_authenticator.utilities.helpers import Helpers
    path = _config(tmp_path)
    errors = []

    def login(n):
        try:
            for i in range(30):
                creds = Helpers.read_config_file(path)['credentials']
                creds['usernames']['kurt']['logged_in'] = bool((n + i) % 2)
                Helpers.update_config_file(path, 'credentials', creds)
        except Exception as exc:                       # pragma: no cover - the failure
            errors.append(exc)

    threads = [threading.Thread(target=login, args=(n,)) for n in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors, errors
    assert isinstance(aci.read_config(path), dict)


def test_install_is_idempotent():
    aci.install()
    aci.install()
    from streamlit_authenticator.utilities.helpers import Helpers
    assert Helpers.update_config_file.__func__.__name__ == '<lambda>'
