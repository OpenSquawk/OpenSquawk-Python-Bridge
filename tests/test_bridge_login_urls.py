"""Where the login and sign-up buttons send the user.

Sign-in moved to single sign-on against the website while the app itself lives
on its own origin, so the two are no longer the same host.
"""

import urllib.parse

import bridge_app

from tests.test_bridge_server import _api


def _opened(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge_app.webbrowser, "open", lambda url, **kw: seen.append(url))
    return seen


def test_login_enters_through_the_login_route_with_the_pairing_code(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    opened = _opened(monkeypatch)

    result = api.login()

    url = opened[0]
    assert result["url"] == url
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == bridge_app.LOGIN_URL
    # The pairing page is the destination, reached *after* the SSO round trip.
    redirect = urllib.parse.parse_qs(parsed.query)["redirect"][0]
    assert redirect == f"/bridge/connect?token={api.token}"
    assert api.polling is True


def test_login_follows_a_self_hosted_server(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.set_server("osq.example.com")
    opened = _opened(monkeypatch)

    api.login()

    assert opened[0].startswith("https://osq.example.com/login?redirect=")


def test_signup_goes_to_the_website_not_the_app_origin(tmp_path, monkeypatch):
    """Accounts are created on the website; the app origin has no sign-up."""
    api = _api(tmp_path, monkeypatch)
    opened = _opened(monkeypatch)

    assert api.open_signup()["url"] == bridge_app.WEBSITE_URL
    assert opened == [bridge_app.WEBSITE_URL]


def test_signup_stays_on_a_self_hosted_server(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.set_server("osq.example.com")
    opened = _opened(monkeypatch)

    assert api.open_signup()["url"] == "https://osq.example.com"
    assert opened == ["https://osq.example.com"]
