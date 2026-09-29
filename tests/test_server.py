from http.client import HTTPConnection
import json
import threading
from unittest.mock import Mock

import pandas as pd
import pytest

import chart
from server import StockServer


@pytest.fixture
def web_server(tmp_path, monkeypatch):
    monkeypatch.setattr(chart.yf, "Ticker", Mock(side_effect=AssertionError("不可連線到 Yahoo")))
    server = StockServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(server, method, path, body=None, headers=None):
    connection = HTTPConnection(*server.server_address, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    status, result, response_headers = response.status, response.read().decode("utf-8"), dict(response.getheaders())
    connection.close()
    return status, result, response_headers


def search(server, symbol, extra_headers=None):
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    return request(server, "POST", "/api/search", json.dumps({"symbol": symbol}), headers)


def stock():
    frame = pd.DataFrame({"Open": [99.], "High": [103.], "Low": [98.], "Close": [101.], "Volume": [1000.]},
                         index=pd.DatetimeIndex(["2023-01-03"]))
    return chart.Stock("2330.TW", frame, "Taiwan Semiconductor")


def test_search_creates_normalized_chart_and_updated_ui(web_server, monkeypatch):
    fetch = Mock(return_value=stock())
    monkeypatch.setattr(chart, "fetch_stock", fetch)
    code, result, _ = search(web_server, " 2330 ")
    assert code == 200
    body = json.loads(result)
    assert body == {"symbol": "2330.TW", "name": "Taiwan Semiconductor", "url": "/2330.TW_chart.html"}
    fetch.assert_called_once_with("2330", "2y")
    assert (web_server.directory / "2330.TW_chart.html").is_file()
    code, page, headers = request(web_server, "GET", body["url"])
    assert code == 200 and headers["Content-Type"].startswith("text/html")
    assert 'id="stock-search-form"' in page and 'id="search-history"' in page
    assert "__SEARCH__" not in page


def test_missing_symbol_keeps_existing_charts(web_server, monkeypatch):
    chart.write_html(chart.build_payload(stock()), web_server.directory)
    path = web_server.directory / "2330.TW_chart.html"
    before = path.read_bytes()
    monkeypatch.setattr(chart, "fetch_stock", Mock(side_effect=ValueError("not found")))
    code, body, _ = search(web_server, "INVALIDXYZ")
    assert code == 422 and "INVALIDXYZ" in json.loads(body)["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("body", ['{"symbol":"../bad"}', '{"symbol":42}', '[]', '{'])
def test_bad_search_requests(web_server, body):
    code, result, _ = request(web_server, "POST", "/api/search", body, {"Content-Type": "application/json"})
    assert code == 400 and "error" in json.loads(result)


def test_cross_origin_post_rejected(web_server):
    assert search(web_server, "MU", {"Origin": "https://unrelated.example"})[0] == 403


@pytest.mark.parametrize("path", ["/chart.py", "/.venv/pyvenv.cfg", "/../README.md", "/%2e%2e/chart.py"])
def test_source_files_and_traversal_are_not_served(web_server, path):
    assert request(web_server, "GET", path)[0] == 404


def test_first_run_has_search_even_without_saved_charts(web_server):
    code, page, _ = request(web_server, "GET", "/")
    assert code == 200 and 'id="stock-search-form"' in page


def test_home_redirects_to_existing_chart(web_server):
    chart.write_html(chart.build_payload(stock()), web_server.directory)
    code, _, headers = request(web_server, "GET", "/")
    assert code == 302 and headers["Location"] == "/2330.TW_chart.html"


def test_same_origin_request_with_market_options(web_server, monkeypatch):
    web_server.period = "5y"
    web_server.colors = "us"
    fetch = Mock(return_value=stock())
    monkeypatch.setattr(chart, "fetch_stock", fetch)
    port = web_server.server_address[1]
    code, _, _ = search(web_server, "2330", {"Origin": f"http://127.0.0.1:{port}"})
    assert code == 200
    fetch.assert_called_once_with("2330", "5y")
    from server import saved_payload
    payload = saved_payload(web_server.directory / "2330.TW_chart.html")
    assert payload["colors"]["mode"] == "us"
