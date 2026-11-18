from decimal import Decimal
from app.coerce import money


def settlement_rows(state):
    """逐票结算行：弃标票以重拍+应追替换原成交，原成交只留档不再相加。"""
    disposals = {str(item['ticket']): item for item in state.get('disposals') or []}
    rows = []
    for row in state.get('final') or []:
        disposal = disposals.get(str(row.get('ticket')))
        base = {
            **row,
            'disposal_id': '',
            'status_label': '正常成交',
            'original_amount': row['amount'],
            'resale_amount': '—',
            'recoverable': '—',
            'correction_pending': '—',
            'booked_amount': row['amount'],
            'disposed': False,
        }
        if disposal:
            base.update({
                'disposal_id': disposal['disposal_id'],
                'status_label': disposal['status_label'],
                'original_amount': disposal['original_amount'],
                'resale_amount': disposal['resale_amount'] or '—',
                'recoverable': disposal['recoverable'] if disposal['recoverable'] is not None else '—',
                'correction_pending': (disposal['correction_pending']
                                       if disposal.get('correction_pending') is not None else '—'),
                'booked_amount': disposal['settled_amount'],
                'amount': disposal['settled_amount'],
                'due': disposal['due'],
                'disposed': True,
            })
        rows.append(base)
    return rows


def balance(state):
    c = state['counts']
    accepted = len(state['records'])
    quarantined = len(state['quarantine'])
    explained = accepted + quarantined + c['blank'] + c['duplicate'] + c['repeated_header']
    rows = settlement_rows(state)
    total = sum((Decimal(r['booked_amount']) for r in rows), Decimal(0))
    paid = sum((Decimal(r['paid']) for r in rows), Decimal(0))
    due = sum((Decimal(r['booked_amount']) - Decimal(r['paid']) for r in rows), Decimal(0))
    # 原生效票自身仍须自洽：弃标处置只做替换，不改原始成交三账。
    originals_consistent = all(
        Decimal(r['amount']) == Decimal(r['paid']) + Decimal(r['due'])
        for r in state.get('final') or []
    )
    disposals = state.get('disposals') or []
    resold = [d for d in disposals if d.get('resale_amount')]
    summary = state.get('disposal_summary') or {}
    original_total = sum((Decimal(r['amount']) for r in state.get('final') or []), Decimal(0))
    resale_total = sum((Decimal(d['resale_amount']) for d in resold), Decimal(0))
    recoverable = sum((Decimal(d['recoverable'] or '0') for d in resold), Decimal(0))
    superseded = sum((Decimal(d['original_amount']) for d in resold), Decimal(0))
    refund = sum((Decimal(d['refund'] or '0') for d in resold), Decimal(0))
    claim_remaining = sum((Decimal(d['claim_remaining'] or '0') for d in resold), Decimal(0))
    return {
        'input': c['input'], 'accepted': accepted, 'quarantined': quarantined,
        'blank': c['blank'], 'duplicate': c['duplicate'], 'repeated_header': c['repeated_header'],
        'row_balanced': explained == c['input'],
        'amount': money(total), 'paid': money(paid), 'due': money(due),
        'money_balanced': total == paid + due and originals_consistent,
        'originals_consistent': originals_consistent,
        'blocked': len(state['version_conflicts']) + int(summary.get('blocking') or 0),
        'original_amount': money(original_total),
        'resale_amount': money(resale_total),
        'recoverable': money(recoverable),
        'refund_to_abandoners': money(refund),
        'claim_remaining': money(claim_remaining),
        'superseded_amount': money(superseded),
        'disposed': len(disposals),
        'resold': len(resold),
        'pending_disposal': int(summary.get('pending') or 0),
        'switch_failed': int(summary.get('switch_failed') or 0),
    }


def apply(state):
    state['balance'] = balance(state)
    rows = settlement_rows(state)
    state['settlement_rows'] = rows
    state['views']['reconcile'] = {
        'title': '应付四账',
        'rows': rows,
        'metrics': {
            '应付_CNY': state['balance']['amount'],
            '已付_CNY': state['balance']['paid'],
            '待付_CNY': state['balance']['due'],
            '应追_CNY': state['balance']['recoverable'],
        },
    }
