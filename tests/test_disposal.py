from copy import deepcopy
from decimal import Decimal

import pytest

from app.engine import evaluate


def run(context):
    return evaluate({'overrides': context})


def clean_without_disposals():
    return evaluate({'dataset': 'clean',
                     'overrides': {'abandonment_reports': [], 'resale_attempts': []}})


def by_ticket(state):
    return {item['ticket']: item for item in state['disposals']}


def report(report_id='AB-T', ticket='00002', version=1, **extra):
    item = {'report_id': report_id, 'ticket': ticket, 'version': version,
            'reported_at': '2026-11-17T06:20:00+08:00', 'buyer': '顺发号',
            'reason': '弃标', 'evidence': {'form': f'弃标确认单 {report_id}'}}
    item.update(extra)
    return item


def attempt(attempt_id='RS-T-1', report_id='AB-T', amount='1000.00', **extra):
    item = {'attempt_id': attempt_id, 'report_id': report_id, 'status': 'filled',
            'switch_status': 'ok', 'buyer': '联丰行', 'amount': amount,
            'effective_at': '2026-11-17T07:15:00+08:00'}
    item.update(extra)
    return item


def test_default_disposals_real(sample):
    tickets = [item['ticket'] for item in sample['disposals']]
    assert tickets == ['00001', '00002', '00003', '00005', '00006', '00010']
    summary = sample['disposal_summary']
    assert summary['count'] == 6
    assert summary['pending'] == 1
    assert summary['switch_failed'] == 1


def test_original_and_resale_both_visible(sample):
    item = by_ticket(sample)['00002']
    assert item['original_amount'] == '1075.02'
    assert item['resale_amount'] == '1000.00'
    assert item['recoverable'] == '75.02'
    assert item['difference'] == '75.02'
    assert item['original']['net_kg'] == '92'


def test_resale_not_settled_twice(sample):
    # 重拍是冲抵而非第二笔成交：每票入账额取原成交与重拍的较大者。
    for item in sample['disposals']:
        expected = max(Decimal(item['original_amount']),
                       Decimal(item['resale_amount'] or '0'))
        assert Decimal(item['settled_amount']) == expected
    base = evaluate({'overrides': {'abandonment_reports': [], 'resale_attempts': []}})
    assert sample['balance']['amount'] == base['balance']['amount']


def test_superseded_amount_matches_originals(sample):
    resold = [d for d in sample['disposals'] if d['resale_amount']]
    assert Decimal(sample['balance']['superseded_amount']) == sum(
        (Decimal(d['original_amount']) for d in resold), Decimal(0))


def test_same_report_rerun_single_disposal(sample):
    context = {'abandonment_reports': [report()], 'resale_attempts': [attempt()]}
    first = run(context)
    repeated = run({'abandonment_reports': [report(), report()],
                    'resale_attempts': [attempt(), attempt()]})
    assert len(first['disposals']) == 1
    assert len(repeated['disposals']) == 1
    assert repeated['disposals'][0]['disposal_id'] == first['disposals'][0]['disposal_id']
    assert repeated['disposal_summary']['duplicates_ignored'] == 1
    assert repeated['disposal_summary']['attempt_duplicates_ignored'] == 1
    assert repeated['disposals'][0]['recoverable'] == first['disposals'][0]['recoverable']


def test_same_ticket_second_report_no_second_disposal(sample):
    state = run({'abandonment_reports': [
        report('AB-A', '00002'), report('AB-B', '00002')], 'resale_attempts': []})
    assert len(state['disposals']) == 1
    assert state['disposals'][0]['report_id'] == 'AB-A'
    assert any(issue['code'] == 'TICKET_ALREADY_DISPOSED' for issue in state['disposal_issues'])


def test_resale_switch_failure_keeps_original(sample):
    state = run({'abandonment_reports': [report()],
                 'resale_attempts': [attempt(switch_status='failed', amount='900.00')]})
    item = state['disposals'][0]
    assert item['status'] == 'SWITCH_FAILED'
    assert item['resale'] is None
    assert item['original_amount'] == '1075.02'
    assert item['settled_amount'] == '1075.02'
    assert item['attempts'][0]['retained'] is True
    assert item['attempts'][0]['active'] is False
    assert Decimal(state['balance']['amount']) == Decimal(
        evaluate({'overrides': {'abandonment_reports': [], 'resale_attempts': []}})['balance']['amount'])


def test_switch_failure_keeps_previous_active_resale(sample):
    item = by_ticket(sample)['00006']
    assert item['status'] == 'RESOLD_RETAINED'
    assert item['resale']['attempt_id'] == 'RS-0006-1'
    assert item['resale_amount'] == '1000.00'
    assert item['recoverable'] == '121.76'
    active = [a for a in item['attempts'] if a['active']]
    assert [a['attempt_id'] for a in active] == ['RS-0006-1']
    failed = next(a for a in item['attempts'] if a['attempt_id'] == 'RS-0006-2')
    assert failed['retained'] is True and failed['switch_ok'] is False


def test_evidence_and_weighing_preserved(sample):
    item = by_ticket(sample)['00001']
    assert item['original']['lines'] == [42, 43]
    weighing = item['original']['weighing']
    assert {row['version'] for row in weighing} == {1, 2}
    assert all('weight_kg' in row for row in weighing)
    assert item['abandonment']['evidence']['form'] == '弃标确认单 AB-0001'
    assert item['abandonment']['raw']['report_id'] == 'AB-0001'
    assert [v['version'] for v in item['original']['versions']] == [1, 2]


def test_original_deal_untouched_by_disposal(sample):
    original = by_ticket(sample)['00001']['original']
    assert original['amount'] == '1285.35'
    assert sample['final'][0]['amount'] == '1285.35'
    assert sample['final'][0]['due'] == '116.85'


def test_correction_after_abandonment_reported(sample):
    item = by_ticket(sample)['00001']
    assert item['reported_version'] == 1
    assert item['version'] == 2
    assert item['abandoned_amount'] == '1168.50'
    assert item['correction_delta'] == '116.85'
    assert item['correction_pending'] == '85.35'
    assert item['recoverable'] == '0.00'
    assert any(issue['code'] == 'CORRECTION_AFTER_ABANDONMENT'
               for issue in sample['disposal_issues'])


def test_resale_amount_recomputed_from_weighing(sample):
    context = {'abandonment_reports': [report()],
               'resale_attempts': [attempt(amount=None, net_kg='90', price='12.00',
                                           rate='0.05')]}
    item = evaluate({'overrides': context})['disposals'][0]
    assert item['resale_amount'] == '1026.00'
    assert item['recoverable'] == '49.02'
    assert item['resale']['basis'] == '重拍净重复算'


def test_declared_amount_mismatch_not_switched(sample):
    context = {'abandonment_reports': [report()],
               'resale_attempts': [attempt(amount='1000.00', net_kg='90', price='12.00',
                                           rate='0.05')]}
    state = evaluate({'overrides': context})
    item = state['disposals'][0]
    assert item['resale'] is None
    assert item['status'] == 'SWITCH_FAILED'
    assert any(issue['code'] == 'RESALE_AMOUNT_MISMATCH' for issue in state['disposal_issues'])


def test_unknown_ticket_isolated(sample):
    state = run({'abandonment_reports': [report('AB-X', '99999')], 'resale_attempts': []})
    assert state['disposals'] == []
    assert any(issue['code'] == 'DEAL_NOT_FOUND' for issue in state['disposal_issues'])


def test_without_reports_nothing_changes(clean):
    state = clean_without_disposals()
    assert state['disposals'] == []
    assert state['balance']['amount'] == '23708.91'
    assert state['disposal_summary']['count'] == 0


def test_reconcile_rows_show_original_resale_and_claim(sample):
    rows = {row['ticket']: row for row in sample['settlement_rows']}
    assert rows['00002']['original_amount'] == '1075.02'
    assert rows['00002']['resale_amount'] == '1000.00'
    assert rows['00002']['recoverable'] == '75.02'
    assert rows['00002']['booked_amount'] == '1075.02'
    assert rows['00005']['status_label'].startswith('待重拍')


def test_resale_surplus_no_claim(clean):
    state = run({'abandonment_reports': [report()],
                 'resale_attempts': [attempt(amount='1200.00')]})
    item = state['disposals'][0]
    assert item['surplus'] == '124.98'
    assert item['recoverable'] == '0.00'
    assert item['claim_remaining'] == '0.00'
    assert item['settled_amount'] == '1200.00'
    assert item['due'] == '1200.00'


def test_pending_resale_keeps_original_due(clean):
    state = run({'abandonment_reports': [report()], 'resale_attempts': []})
    item = state['disposals'][0]
    assert item['status'] == 'PENDING_RESALE'
    assert item['resale'] is None
    assert item['settled_amount'] == '1075.02'
    assert item['due'] == '1075.02'
    assert state['balance']['pending_disposal'] == 1


def test_disposal_does_not_mutate_weighing_or_settlements(sample):
    base = evaluate()
    assert sample['records'] == base['records']
    assert sample['settlements'] == base['settlements']
    assert sample['final'] == base['final']
    assert sample['version_audit'] == base['version_audit']


def test_report_conflict_keeps_first(sample):
    state = run({'abandonment_reports': [report(buyer='甲'), report(buyer='乙')],
                 'resale_attempts': []})
    assert len(state['disposals']) == 1
    assert state['disposals'][0]['abandonment']['buyer'] == '甲'
    assert any(issue['code'] == 'REPORT_CONFLICT' for issue in state['disposal_issues'])


def test_orphan_attempt_ignored(sample):
    orphan = attempt()
    orphan.pop('report_id')
    state = run({'abandonment_reports': [], 'resale_attempts': [orphan]})
    assert state['disposals'] == []
    assert any(issue['code'] == 'ORPHAN_RESALE' for issue in state['disposal_issues'])


def test_attempt_order_independent(sample):
    context = {'abandonment_reports': [report()],
               'resale_attempts': [attempt('RS-B', amount='1000.00',
                                           effective_at='2026-11-17T08:00:00+08:00'),
                                   attempt('RS-A', amount='950.00',
                                           effective_at='2026-11-17T07:00:00+08:00')]}
    forward = run(context)['disposals'][0]
    context['resale_attempts'] = list(reversed(context['resale_attempts']))
    reverse = run(context)['disposals'][0]
    assert forward['resale']['attempt_id'] == 'RS-B'
    assert forward['attempts'] == reverse['attempts']
    assert forward['recoverable'] == reverse['recoverable'] == '75.02'


def test_recoverable_plus_pending_plus_resale_equals_original(sample):
    for item in sample['disposals']:
        if not item['resale_amount']:
            continue
        total = (Decimal(item['resale_amount']) + Decimal(item['recoverable'])
                 + Decimal(item['correction_pending']) + Decimal(item['surplus']))
        assert total == Decimal(item['original_amount']), item['ticket']


def test_pending_never_exceeds_real_shortfall(sample):
    for item in sample['disposals']:
        if not item['resale_amount']:
            continue
        assert Decimal(item['recoverable']) <= Decimal(item['shortfall'])
        assert Decimal(item['recoverable']) + Decimal(item['correction_pending']) == Decimal(item['shortfall'])


def test_summary_matches_rows(sample):
    summary = sample['disposal_summary']
    rows = sample['disposals']
    assert Decimal(summary['recoverable']) == sum(
        (Decimal(item['recoverable'] or '0') for item in rows), Decimal(0))
    assert Decimal(summary['original_amount']) == sum(
        (Decimal(item['original_amount']) for item in rows), Decimal(0))


def test_embedded_attempts_added_on_duplicate_report(sample):
    first = report(resale_attempts=[attempt('RS-E1', amount='1000.00')])
    added = report(resale_attempts=[attempt('RS-E2', amount='900.00',
                                            effective_at='2026-11-17T09:00:00+08:00')])
    state = run({'abandonment_reports': [first, added], 'resale_attempts': []})
    item = state['disposals'][0]
    assert [a['attempt_id'] for a in item['attempts']] == ['RS-E1', 'RS-E2']
    assert item['resale']['attempt_id'] == 'RS-E2'
    assert item['recoverable'] == '175.02'


def test_resale_computed_from_reweighed_net(sample):
    item = by_ticket(sample)['00010']
    assert item['abandoned_amount'] == '1168.50'
    assert item['resale']['basis'] == '重拍净重复算'
    assert item['resale_amount'] == '1031.32'
    assert item['shortfall'] == '137.18'
    assert item['recoverable'] == '137.18'
    assert item['settled_amount'] == '1168.50'
