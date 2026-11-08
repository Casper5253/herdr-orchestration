import copy
import datetime as dt
import io
import json
from pathlib import Path
from http.client import IncompleteRead
from urllib.error import HTTPError

import pytest

from hx import opencode_go, usage


NOW = dt.datetime(2026, 11, 8, 5, 30, tzinfo=dt.timezone.utc)
FIXTURE = Path(__file__).parent / 'fixtures' / 'opencode-go-usage.json'


@pytest.fixture
def auth(tmp_path):
    path = tmp_path / 'auth.json'
    path.write_text(json.dumps({'opencode-go': {'type': 'api', 'key': 'fixture-only-value'}}))
    return path


def test_recorded_quota_is_measured_without_inventing_dollar_caps():
    windows = opencode_go.parse_usage(json.loads(FIXTURE.read_text()))
    assert windows['5h']['utilisation'] == .05
    assert windows['7d']['utilisation'] == .08
    assert windows['30d']['utilisation'] == .04
    assert windows['5h']['resets_at'] == '2026-11-08T07:22:11.000Z'
    assert all(window['cap'] is None and window['spend'] is None for window in windows.values())


def test_rate_limited_status_is_exhausted_even_when_percentage_rounds_below_full():
    payload = json.loads(FIXTURE.read_text())
    payload['usage']['rolling'].update(status='rate-limited', percent=99)
    assert opencode_go.parse_usage(payload)['5h']['utilisation'] == 1


@pytest.mark.parametrize('mutate', [
    lambda p: p['usage'].pop('weekly'),
    lambda p: p['usage']['weekly'].update(percent=float('nan')),
    lambda p: p['usage']['weekly'].update(percent=-1),
    lambda p: p['usage']['weekly'].update(percent=True),
    lambda p: p['usage']['weekly'].update(status='unknown'),
    lambda p: p['usage']['weekly'].update(resetsAt='not-a-date'),
    lambda p: p['usage']['weekly'].update(resetsAt='2026-11-09T00:00:00'),
])
def test_invalid_or_partial_quota_is_not_a_measured_zero(mutate):
    payload = copy.deepcopy(json.loads(FIXTURE.read_text()))
    mutate(payload)
    with pytest.raises(ValueError):
        opencode_go.parse_usage(payload)


def test_fetch_uses_fixed_provider_endpoint_timeout_and_redirect_refusal(auth, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'https://opencode.ai/zen/go/v1/usage'
            assert request.get_method() == 'GET'
            assert request.has_header('Authorization')
            assert request.has_header('User-agent')
            assert timeout == 2
            return io.BytesIO(FIXTURE.read_bytes())

    def opener(*handlers):
        assert len(handlers) == 1
        assert handlers[0].redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid') is None
        return Opener()

    monkeypatch.setattr(opencode_go, 'build_opener', opener)
    assert opencode_go.fetch_usage(auth)['5h']['utilisation'] == .05


@pytest.mark.parametrize('failure', [TimeoutError, OSError, ValueError, IncompleteRead])
def test_fetch_failures_are_silent_and_do_not_expose_credentials(auth, monkeypatch, capsys, failure):
    class Opener:
        def open(self, request, timeout):
            raise failure(b'sensitive response must not be reported')
    monkeypatch.setattr(opencode_go, 'build_opener', lambda *args: Opener())
    assert opencode_go.fetch_usage(auth) is None
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_missing_auth_never_calls_network(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode_go, 'build_opener', lambda *args: pytest.fail('network without login'))
    assert opencode_go.fetch_usage(tmp_path / 'missing.json') is None


@pytest.mark.parametrize('body', [b'not json', b'x' * 65537, b'{"usage":{}}'],
                         ids=['invalid-json', 'oversized', 'partial'])
def test_invalid_api_body_falls_back(auth, monkeypatch, body):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(body)
    monkeypatch.setattr(opencode_go, 'build_opener', lambda *args: Opener())
    assert opencode_go.fetch_usage(auth) is None


@pytest.mark.parametrize('login', [[], {}, {'opencode-go': {'type': 'oauth'}},
                                   {'opencode-go': {'type': 'api', 'key': ''}}])
def test_invalid_login_never_calls_network(tmp_path, monkeypatch, login):
    path = tmp_path / 'auth.json'
    path.write_text(json.dumps(login))
    monkeypatch.setattr(opencode_go, 'build_opener', lambda *args: pytest.fail('network without API login'))
    assert opencode_go.fetch_usage(path) is None


def test_http_failure_never_prints_response_headers_or_body(auth, monkeypatch, capsys):
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, 'sensitive response', {}, io.BytesIO(b'sensitive body'))
    monkeypatch.setattr(opencode_go, 'build_opener', lambda *args: Opener())
    assert opencode_go.fetch_usage(auth) is None
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_live_usage_overrides_local_estimate_but_retains_its_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode_go, 'fetch_usage', lambda *args: opencode_go.parse_usage(json.loads(FIXTURE.read_text())))
    result = usage.opencode_usage(usage.load_policy(), db_path=tmp_path/'missing.db', now=NOW)
    assert result['tag'] == result['pool']['tag'] == 'measured'
    assert result['pool']['windows']['5h']['utilisation'] == .05
    assert result['available'] is True
    assert result['models_tag'] == 'unknown'
    assert result['local_estimate']['tag'] == 'unknown'
    assert 'error' not in result


def test_api_failure_preserves_local_estimate(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path/'opencode.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE message (time_created INTEGER, data TEXT)')
        conn.execute('INSERT INTO message VALUES (?,?)', (int(NOW.timestamp()*1000), json.dumps({
            'role':'assistant','modelID':'deepseek-v4.1-flash','cost':3,
        })))
    monkeypatch.setattr(opencode_go, 'fetch_usage', lambda *args: None)
    result = usage.opencode_usage(usage.load_policy(), db_path=db, now=NOW)
    assert result['tag'] == 'estimated'
    assert result['pool']['windows']['5h']['utilisation'] == .25


@pytest.mark.parametrize('percent, expected', [(8, 'available'), (100, 'exhausted')])
def test_router_uses_measured_quota_without_dollar_caps(tmp_path, monkeypatch, percent, expected):
    from hx.route import _opencode_pool_status
    payload = json.loads(FIXTURE.read_text())
    payload['usage']['weekly']['percent'] = percent
    monkeypatch.setattr(opencode_go, 'fetch_usage', lambda *args: opencode_go.parse_usage(payload))
    policy = usage.load_policy()
    result = usage.opencode_usage(policy, db_path=tmp_path / 'missing.db', now=NOW)
    assert _opencode_pool_status(result, policy)[0] == expected

