import io
import json
import zipfile

from fastapi.testclient import TestClient

from app import main, store


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'disposal.sqlite'))
    return TestClient(main.app)


def test_disposal_page_renders_result(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    response = c.get('/disposal')
    assert response.status_code == 200
    body = response.text
    for text in ('弃标处置结果', '原成交（留档', '重拍结果', '应追差额', 'RS-0006-1'):
        assert text in body


def test_disposal_page_follows_run_id(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/disposal', json={}).json()
    run_id = run['manifest']['run_id']
    page = c.get('/disposal?run_id=' + run_id)
    assert page.status_code == 200
    assert '?run_id=' + run_id in page.text
    assert run['disposal_summary']['count'] == 6


def test_rerun_same_disposal_no_duplicates(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    first = c.post('/api/disposal', json={}).json()
    again = c.post('/api/runs/' + first['manifest']['run_id'] + '/rerun', json={}).json()
    assert again['manifest']['run_id'] == first['manifest']['run_id']
    assert len(again['disposals']) == len(first['disposals'])
    assert [d['disposal_id'] for d in again['disposals']] == [d['disposal_id'] for d in first['disposals']]
    assert len(c.get('/api/runs').json()) == 1


def test_duplicate_report_payload_no_new_disposal(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    context = json.loads((main.ROOT / 'sample_data/context.json').read_text())
    first = c.post('/api/disposal', json={'overrides': context}).json()
    repeated = c.post('/api/disposal', json={'overrides': {
        **context,
        'abandonment_reports': context['abandonment_reports'] + context['abandonment_reports'],
        'resale_attempts': context['resale_attempts'] + context['resale_attempts'],
    }}).json()
    assert len(repeated['disposals']) == len(first['disposals'])
    assert repeated['disposal_summary']['duplicates_ignored'] == len(context['abandonment_reports'])
    assert repeated['manifest']['run_id'] != first['manifest']['run_id']


def test_archive_bundle_contains_disposals_and_evidence(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/archive', json={}).json()
    data = zipfile.ZipFile(io.BytesIO(c.get('/api/export/' + run['manifest']['run_id']).content))
    disposals = json.loads(data.read('disposals.json'))
    assert disposals['summary']['count'] == 6
    assert len(disposals['disposals']) == 6
    assert len(disposals['settlement_rows']) == 20
    ticket = next(item for item in disposals['disposals'] if item['ticket'] == '00001')
    assert ticket['original']['weighing']
    assert ticket['abandonment']['evidence']['form'] == '弃标确认单 AB-0001'
    assert ticket['original_amount'] == '1285.35'
    assert ticket['abandoned_amount'] == '1168.50'
    assert ticket['resale_amount'] == '1200.00'
    assert ticket['recoverable'] == '0.00'
    assert ticket['correction_pending'] == '85.35'
    assert ticket['settled_amount'] == '1285.35'


def test_reconcile_and_disposal_agree(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={}).json()
    disposals = {d['ticket']: d for d in run['disposals']}
    rows = {r['ticket']: r for r in run['settlement_rows']}
    for ticket, row in rows.items():
        if ticket in disposals:
            assert row['booked_amount'] == disposals[ticket]['settled_amount']
            assert row['original_amount'] == disposals[ticket]['original_amount']
        else:
            assert row['disposed'] is False
            assert row['booked_amount'] == row['amount']
    assert run['balance']['money_balanced'] is True


def test_seal_changes_when_resale_switch_changes(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    context = json.loads((main.ROOT / 'sample_data/context.json').read_text())
    first = c.post('/api/archive', json={'overrides': context}).json()
    changed = c.post('/api/archive', json={'overrides': {
        **context,
        'resale_attempts': [dict(item, switch_status='failed') if item['attempt_id'] == 'RS-0001-1'
                            else item for item in context['resale_attempts']],
    }}).json()
    assert changed['archive']['seal'] != first['archive']['seal']
    ticket = next(d for d in changed['disposals'] if d['ticket'] == '00001')
    assert ticket['resale'] is None
    assert ticket['settled_amount'] == '1285.35'


def test_bad_disposal_payload_http(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    assert c.post('/api/disposal', json={'overrides': {'abandonment_reports': {}}}).status_code == 422
