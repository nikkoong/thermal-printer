from __future__ import annotations

from thermal.config import Config


def test_api_url_rewrites_wildcard_hosts() -> None:
    assert Config(api_host="0.0.0.0", api_port=9000).api_url == "http://127.0.0.1:9000"
    assert Config(api_host="::", api_port=9000).api_url == "http://127.0.0.1:9000"
    assert Config(api_host="", api_port=9000).api_url == "http://127.0.0.1:9000"
    assert Config(api_host="192.168.1.5", api_port=9000).api_url == "http://192.168.1.5:9000"
