"""弃标处置：保留原成交，登记重拍尝试，核算应追差额。

弃标回报与重拍尝试随批次上下文（context）持久化，并随输入快照一起重跑。
本模块只读取称重、成交与更正结果，不改写原始记录；问题写入
``disposal_issues``，不混入称重隔离行，保证原对账行守恒。

约定：
* 同一 ``report_id`` 的回报只生成一个处置；内容相同的重复回报静默忽略，
  同一票号后来的不同回报只登记原因、不再生成第二个处置。
* 重拍只是冲抵原成交，不是第二笔成交：入账额 = 重拍成交 + 应追差额 +
  待追认更正 = max(原成交, 重拍成交)，绝不把原成交与重拍成交重复求和。
* 重拍切换失败时不采纳该笔重拍；原成交与上一笔已生效的重拍处置原样保留。
* 弃标早于复秤或扣杂更正时，差额按当前生效版本重算，并保留弃标时版本、
  金额与更正差额，便于追认。
"""
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.coerce import instant, money

PENDING_RESALE = 'PENDING_RESALE'
RESOLD = 'RESOLD'
RESOLD_RETAINED = 'RESOLD_RETAINED'
SWITCH_FAILED = 'SWITCH_FAILED'

STATUS_LABELS = {
    PENDING_RESALE: '待重拍·原成交保留',
    RESOLD: '已重拍·原成交留档',
    RESOLD_RETAINED: '切换失败·保留旧处置',
    SWITCH_FAILED: '切换失败·原成交保留',
}

FILLED_STATUS = {'filled', 'fill', 'success', 'succeeded', 'ok', '成交', '已成交', '成功'}
FAILED_STATUS = {'failed', 'fail', 'void', 'cancelled', 'canceled', '失败', '未成交', '流拍', '取消'}
BASIS_LABELS = {'weighed': '重拍净重复算', 'billable': '重拍计价重重算', 'declared': '申报成交额'}

SWITCH_OK = {'ok', 'success', 'succeeded', 'committed', 'switched', '切换成功', '成功', '已切换', 'true'}
SWITCH_BAD = {'failed', 'fail', 'rollback', 'rolled_back', 'rejected', '切换失败', '失败', '回滚', '未切换', 'false'}


def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _dec(value):
    if value in (None, ''):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _time(value):
    try:
        return instant(value)
    except Exception:
        return None


def _order(item, id_field, time_field):
    stamp = _time(item.get(time_field))
    return (0 if stamp else 1, stamp or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get(id_field) or ''), _canon(item))


LABELS = {
    'REPORT_CONFLICT': '弃标回报冲突', 'MISSING_DISPOSAL_ID': '处置编号缺失',
    'BAD_DISPOSAL_INPUT': '处置输入无效', 'MISSING_TICKET': '弃标回报缺票号',
    'DEAL_NOT_FOUND': '没有可替代的成交', 'TICKET_ALREADY_DISPOSED': '同票已有弃标处置',
    'BAD_REPORT_VERSION': '弃标版本无效', 'VERSION_MISMATCH': '弃标版本不存在',
    'ABANDONMENT_EVIDENCE_MISSING': '弃标证据缺失', 'CORRECTION_AFTER_ABANDONMENT': '弃标后有更正',
    'ORPHAN_RESALE': '重拍未关联回报', 'RESALE_STATUS_UNKNOWN': '重拍状态无法识别',
    'RESALE_EVIDENCE_MISSING': '重拍证据缺失', 'RESALE_SWITCH_FAILED': '重拍切换失败',
    'RESALE_AMOUNT_MISMATCH': '重拍金额与重算不符', 'RESALE_INCOMPLETE': '重拍计价不完整',
    'INVALID_RESALE_AMOUNT': '重拍金额越界',
}


def _issue(issues, code, detail, report_id='', attempt_id='', severity='blocking'):
    issues.append({'report_id': str(report_id or ''), 'attempt_id': str(attempt_id or ''),
                   'code': code, 'label': LABELS.get(code, code), 'detail': detail,
                   'severity': severity})


def _has_evidence(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(_has_evidence(item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return str(value).strip() != ''


def _status_text(attempt):
    return str(attempt.get('status') or attempt.get('result') or '').strip().lower()


def _is_filled(attempt):
    return _status_text(attempt) in FILLED_STATUS


def _switch_ok(attempt):
    for key in ('switch_status', 'switch_result', 'switch', 'switched', 'commit_status'):
        if key not in attempt or attempt[key] in (None, ''):
            continue
        value = attempt[key]
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in SWITCH_BAD:
            return False
        if text in SWITCH_OK:
            return True
    return True


def _parse_version(value):
    if value in (None, ''):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return 'bad'
    if number != number.to_integral_value() or number < 1:
        return 'bad'
    return int(number)


def _merge_records(existing, incoming, id_field, issues, label):
    """按编号合并；同号同值视为重复，同号不同值保留首条并登记冲突。"""
    seen = {}
    result = []
    duplicates = 0
    conflicts = 0
    for item in list(existing or []) + list(incoming or []):
        if not isinstance(item, dict):
            _issue(issues, 'BAD_DISPOSAL_INPUT', f'{label}须为对象')
            continue
        key = str(item.get(id_field) or '').strip()
        if not key:
            _issue(issues, 'MISSING_DISPOSAL_ID', f'{label}缺少编号')
            continue
        canon = _canon(item)
        if key in seen:
            if seen[key] == canon:
                duplicates += 1
            else:
                conflicts += 1
                _issue(issues, 'REPORT_CONFLICT', f'{label} {key} 内容冲突，保留首条',
                       report_id=key if id_field == 'report_id' else '',
                       attempt_id=key if id_field == 'attempt_id' else '', severity='warning')
            continue
        seen[key] = canon
        result.append(item)
    return result, duplicates, conflicts


def _resale_amount(attempt, original, issues, report_id):
    """返回 (成交额文本, 计价依据)；无法确认时返回 (None, None)。"""
    attempt_id = attempt.get('attempt_id')
    declared = _dec(attempt.get('amount'))
    price = _dec(attempt.get('price', original['price']))
    rate = _dec(attempt.get('rate', original['rate']))
    billable = _dec(attempt.get('billable_kg'))
    net = _dec(attempt.get('net_kg', attempt.get('weight_kg')))

    if billable is not None and price is not None:
        if billable < 0 or price < 0:
            _issue(issues, 'INVALID_RESALE_AMOUNT', '重拍计价重量或单价为负', report_id, attempt_id, severity='warning')
            return None, None
        computed, basis = billable * price, 'billable'
    elif net is not None and price is not None:
        if net < 0 or price < 0 or rate is None or not Decimal(0) <= rate < Decimal(1):
            _issue(issues, 'INVALID_RESALE_AMOUNT', '重拍净重、单价或扣杂率越界', report_id, attempt_id, severity='warning')
            return None, None
        billable = (net * (Decimal(1) - rate)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
        computed, basis = billable * price, 'weighed'
    else:
        computed, basis = None, None

    computed_text = money(computed) if computed is not None else None
    declared_text = money(declared) if declared is not None else None
    if declared_text is not None and computed_text is not None:
        if abs(Decimal(declared_text) - Decimal(computed_text)) > Decimal('0.01'):
            _issue(issues, 'RESALE_AMOUNT_MISMATCH',
                   f'申报成交额 {declared_text} 与计价重算 {computed_text} 不一致，切换未执行',
                   report_id, attempt_id, severity='warning')
            return None, None
    if declared_text is not None:
        if Decimal(declared_text) < 0:
            _issue(issues, 'INVALID_RESALE_AMOUNT', '重拍成交额为负', report_id, attempt_id, severity='warning')
            return None, None
        return declared_text, (basis if computed_text is not None else 'declared')
    if computed_text is not None:
        return computed_text, basis
    _issue(issues, 'RESALE_INCOMPLETE', '重拍成交缺少金额，且无净重/单价可重算', report_id, attempt_id, severity='warning')
    return None, None


def _attempt_row(attempt, active, retained, amount, basis, note=''):
    status = _status_text(attempt)
    filled = _is_filled(attempt)
    switch_ok = _switch_ok(attempt) if filled else None
    if not filled:
        label = '未成交'
    elif switch_ok is False:
        label = '切换失败'
    elif retained:
        label = '未采纳'
    else:
        label = '已切换'
    return {
        'attempt_id': str(attempt.get('attempt_id') or ''),
        'buyer': str(attempt.get('buyer') or ''),
        'status': status,
        'status_label': label,
        'filled': filled,
        'switch_ok': switch_ok,
        'active': bool(active),
        'retained': bool(retained),
        'amount': amount,
        'basis': BASIS_LABELS.get(basis, basis),
        'net_kg': str(attempt.get('net_kg', attempt.get('weight_kg', '')) or '') or None,
        'price': str(attempt.get('price') or '') or None,
        'rate': str(attempt.get('rate') or '') or None,
        'effective_at': str(attempt.get('effective_at') or attempt.get('switched_at') or ''),
        'reason': str(attempt.get('reason') or attempt.get('note') or note),
        'evidence': deepcopy(attempt.get('evidence')),
        'raw': deepcopy(attempt),
    }


def _original_snapshot(deal, weighing, versions):
    return {
        'ticket': deal.get('ticket'),
        'version': deal.get('version'),
        'fish': deal.get('fish'),
        'gross_kg': deal.get('gross_kg'),
        'tare_kg': deal.get('tare_kg'),
        'net_kg': deal.get('net_kg'),
        'billable_kg': deal.get('billable_kg'),
        'price': deal.get('price'),
        'rate': deal.get('rate'),
        'amount': deal.get('amount'),
        'paid': deal.get('paid'),
        'due': deal.get('due'),
        'lines': list(deal.get('lines') or []),
        'weighing': deepcopy(weighing),
        'versions': deepcopy(versions),
    }


def _disposal_id(report_id, ticket, version):
    key = f'{report_id}|{ticket}|{version}'.encode()
    return 'DSP-' + hashlib.sha256(key).hexdigest()[:12]


def _load_context_lists(state, issues, duplicate_counter):
    context = state.get('context') or {}
    raw_reports = context.get('abandonment_reports') or context.get('disposal_reports') or []
    raw_attempts = context.get('resale_attempts') or context.get('resale_reports') or []
    if not isinstance(raw_reports, list) or not isinstance(raw_attempts, list):
        _issue(issues, 'BAD_DISPOSAL_INPUT', '弃标回报与重拍记录须为列表')
        raw_reports, raw_attempts = [], []

    reports, dup, _ = _merge_records([], raw_reports, 'report_id', issues, '弃标回报')
    duplicate_counter['reports'] += dup
    attempts, dup, _ = _merge_records([], raw_attempts, 'attempt_id', issues, '重拍记录')
    duplicate_counter['attempts'] += dup

    # 回报正文以首条为准，但同号回报后来补登记的重拍尝试仍按 attempt_id 合并，
    # 避免重跑时把追加的成交回执丢掉。
    embedded = []
    for report in raw_reports:
        if not isinstance(report, dict):
            continue
        for key in ('resale_attempts', 'attempts'):
            for attempt in report.get(key) or []:
                if isinstance(attempt, dict):
                    embedded.append({**attempt, 'report_id': attempt.get('report_id') or report.get('report_id')})
    if embedded:
        attempts, dup, _ = _merge_records(attempts, embedded, 'attempt_id', issues, '重拍记录')
        duplicate_counter['attempts'] += dup
    return reports, attempts


def build_disposals(state, issues=None, duplicate_counter=None):
    """从 state['context'] 生成处置；不修改称重、成交与更正结果。"""
    issues = issues if issues is not None else []
    duplicate_counter = duplicate_counter if duplicate_counter is not None else {'reports': 0, 'attempts': 0}
    reports, attempts = _load_context_lists(state, issues, duplicate_counter)

    by_report = {}
    for attempt in attempts:
        key = str(attempt.get('report_id') or '').strip()
        if not key:
            _issue(issues, 'ORPHAN_RESALE', f'重拍 {attempt.get("attempt_id", "")} 未关联弃标回报',
                   attempt_id=attempt.get('attempt_id'), severity='warning')
            continue
        by_report.setdefault(key, []).append(attempt)

    deals = {}
    for row in state.get('final') or state.get('settlements') or []:
        if isinstance(row, dict) and row.get('ticket') is not None:
            deals.setdefault(str(row['ticket']), row)

    version_map = {}
    for row in state.get('settlements') or []:
        version_map.setdefault(str(row.get('ticket')), {})[row.get('version')] = row
    weighing_map = {}
    for row in state.get('records') or []:
        weighing_map.setdefault(str(row.get('ticket')), []).append(deepcopy(row))

    disposals = []
    claimed = set()
    for report in sorted(reports, key=lambda item: _order(item, 'report_id', 'reported_at')):
        report_id = str(report.get('report_id') or '').strip()
        ticket = str(report.get('ticket') or '').strip()
        if not ticket:
            _issue(issues, 'MISSING_TICKET', '弃标回报缺少票号', report_id, severity='warning')
            continue
        deal = deals.get(ticket)
        if deal is None:
            _issue(issues, 'DEAL_NOT_FOUND', f'票号 {ticket} 没有可替代的成交记录', report_id, severity='warning')
            continue
        if ticket in claimed:
            _issue(issues, 'TICKET_ALREADY_DISPOSED', f'票号 {ticket} 已有弃标处置，本回报不再重复生成',
                   report_id, severity='warning')
            continue
        current_version = deal.get('version')
        reported_version = _parse_version(report.get('version'))
        if reported_version == 'bad':
            _issue(issues, 'BAD_REPORT_VERSION', '弃标回报版本号无效', report_id, severity='warning')
            continue
        known = version_map.get(ticket, {})
        if reported_version is None:
            reported_version = current_version
        if reported_version not in known:
            _issue(issues, 'VERSION_MISMATCH',
                   f'弃标回报版本 {reported_version} 不存在于票号 {ticket} 的成交版本',
                   report_id, severity='warning')
            continue
        if not _has_evidence(report.get('evidence')):
            _issue(issues, 'ABANDONMENT_EVIDENCE_MISSING', '弃标回报未附证据，原称重记录仍保留',
                   report_id, severity='warning')
        abandoned_amount = Decimal(str(known[reported_version].get('amount') or deal['amount']))
        original_amount = Decimal(str(deal['amount']))
        correction_after = reported_version != current_version
        if correction_after:
            _issue(issues, 'CORRECTION_AFTER_ABANDONMENT',
                   f'弃标时版本 v{reported_version}，当前生效 v{current_version}；差额按生效版重算',
                   report_id, severity='warning')
        claimed.add(ticket)

        history = []
        active = None
        active_amount = None
        active_basis = None
        active_index = -1
        last_failed = -1
        filled_seen = False
        for index, attempt in enumerate(sorted(by_report.get(report_id, []),
                                               key=lambda item: _order(item, 'attempt_id', 'effective_at'))):
            attempt_id = str(attempt.get('attempt_id') or '').strip()
            if not _is_filled(attempt):
                if _status_text(attempt) not in FAILED_STATUS:
                    _issue(issues, 'RESALE_STATUS_UNKNOWN',
                           f'重拍 {attempt_id} 状态无法识别，按未成交处理',
                           report_id, attempt_id, severity='warning')
                history.append(_attempt_row(attempt, False, False, None, None, '未成交，原成交保留'))
                continue
            filled_seen = True
            if not _has_evidence(attempt.get('evidence')):
                _issue(issues, 'RESALE_EVIDENCE_MISSING', '重拍缺少成交证据，原凭证仍保留',
                       report_id, attempt_id, severity='warning')
            amount, basis = _resale_amount(attempt, deal, issues, report_id)
            switch_ok = _switch_ok(attempt)
            if not switch_ok:
                _issue(issues, 'RESALE_SWITCH_FAILED',
                       f'重拍 {attempt_id} 切换失败，保留原成交与既有处置',
                       report_id, attempt_id, severity='warning')
                history.append(_attempt_row(attempt, False, True, amount, basis,
                                            '切换失败，未采纳；原成交与旧处置保留'))
                last_failed = index
                continue
            if amount is None:
                history.append(_attempt_row(attempt, False, True, None, None,
                                            '成交额无法确认，未采纳；原成交与旧处置保留'))
                last_failed = index
                continue
            if active is not None:
                history[active_index]['active'] = False
                history[active_index]['status_label'] = '已被后次重拍替代'
            active = attempt
            active_amount = amount
            active_basis = basis
            active_index = len(history)
            history.append(_attempt_row(attempt, True, False, amount, basis, '生效重拍'))

        if active is not None:
            resale_amount = Decimal(active_amount)
            difference = original_amount - resale_amount
            shortfall = max(difference, Decimal(0))
            # 应追以弃标时已认的成交额为限，且不超过当前成交的真实缺口；
            # 弃标后复秤/扣杂更正抬高的部分只列为待追认，不当作已确认债权。
            recoverable = min(max(abandoned_amount - resale_amount, Decimal(0)), shortfall)
            correction_pending = shortfall - recoverable
            surplus = max(-difference, Decimal(0))
            # 重拍是冲抵而非第二笔成交：入账额取原成交与重拍的较大者，
            # 相当于「重拍 + 应追 + 待追认更正 = 原成交」，绝不重复求和。
            effective = max(original_amount, resale_amount)
            status = RESOLD_RETAINED if last_failed > active_index else RESOLD
            resale = {
                'attempt_id': str(active.get('attempt_id') or ''),
                'buyer': str(active.get('buyer') or ''),
                'amount': active_amount,
                'basis': BASIS_LABELS.get(active_basis, active_basis),
                'effective_at': str(active.get('effective_at') or active.get('switched_at') or ''),
                'evidence': deepcopy(active.get('evidence')),
                'raw': deepcopy(active),
            }
        else:
            resale_amount = difference = recoverable = correction_pending = surplus = None
            effective = original_amount
            status = SWITCH_FAILED if filled_seen and last_failed >= 0 else PENDING_RESALE
            resale = None

        paid = Decimal(str(deal.get('paid') or '0'))
        # 与原买家的净额：已付款先抵已确认应追，余额退还；抵不完的部分继续追收。
        if active is not None:
            claim_remaining = max(recoverable - paid, Decimal(0))
            refund = max(paid - recoverable, Decimal(0))
            # 该票实际还要收进来的现金 = 新买家重拍款 + 原买家补足款。
            cash_due = resale_amount + claim_remaining
        else:
            claim_remaining = None
            refund = None
            cash_due = None
        disposal = {
            'disposal_id': _disposal_id(report_id, ticket, current_version),
            'report_id': report_id,
            'ticket': ticket,
            'version': current_version,
            'reported_version': reported_version,
            'status': status,
            'status_label': STATUS_LABELS[status],
            'provisional': correction_after,
            'original': _original_snapshot(deal, weighing_map.get(ticket, []),
                                           [known[key] for key in sorted(known)]),
            'abandonment': {
                'report_id': report_id,
                'reported_at': str(report.get('reported_at') or ''),
                'buyer': str(report.get('buyer') or ''),
                'reason': str(report.get('reason') or report.get('note') or ''),
                'version': reported_version,
                'amount': money(abandoned_amount),
                'evidence': deepcopy(report.get('evidence')),
                'raw': deepcopy(report),
            },
            'attempts': history,
            'resale': resale,
            'original_amount': money(original_amount),
            'abandoned_amount': money(abandoned_amount),
            'correction_delta': money(original_amount - abandoned_amount),
            'original_due': money(original_amount - paid),
            'resale_amount': active_amount,
            'difference': money(difference) if difference is not None else None,
            'shortfall': money(max(difference, Decimal(0))) if difference is not None else None,
            'excess': money(max(-difference, Decimal(0))) if difference is not None else None,
            'recoverable': money(recoverable) if recoverable is not None else None,
            'correction_pending': money(correction_pending) if correction_pending is not None else None,
            'surplus': money(surplus) if surplus is not None else None,
            'settled_amount': money(effective),
            'paid': money(paid),
            'refund': money(refund) if refund is not None else None,
            'claim_remaining': money(claim_remaining) if claim_remaining is not None else None,
            'cash_due': money(cash_due) if cash_due is not None else None,
            'due': money(effective - paid),
        }
        disposals.append(disposal)

    return disposals, issues, duplicate_counter


def summarize(disposals, issues, duplicate_counter):
    original_amount = sum((Decimal(item['original_amount']) for item in disposals), Decimal(0))
    resale_amount = sum((Decimal(item['resale_amount']) for item in disposals if item['resale_amount']), Decimal(0))
    recoverable = sum((Decimal(item['recoverable']) for item in disposals if item['recoverable']), Decimal(0))
    correction_pending = sum((Decimal(item['correction_pending']) for item in disposals if item['correction_pending']), Decimal(0))
    surplus = sum((Decimal(item['surplus']) for item in disposals if item['surplus']), Decimal(0))
    booked = sum((Decimal(item['settled_amount']) for item in disposals), Decimal(0))
    refund = sum((Decimal(item['refund']) for item in disposals if item['refund']), Decimal(0))
    claim_remaining = sum((Decimal(item['claim_remaining']) for item in disposals if item['claim_remaining']), Decimal(0))
    cash_due = sum((Decimal(item['cash_due']) for item in disposals if item['cash_due']), Decimal(0))
    counts = {status: 0 for status in STATUS_LABELS}
    for item in disposals:
        counts[item['status']] += 1
    return {
        'count': len(disposals),
        'resold': counts[RESOLD],
        'retained': counts[RESOLD_RETAINED],
        'pending': counts[PENDING_RESALE],
        'switch_failed': counts[SWITCH_FAILED],
        'original_amount': money(original_amount),
        'resale_amount': money(resale_amount),
        'recoverable': money(recoverable),
        'correction_pending': money(correction_pending),
        'surplus': money(surplus),
        'settled_amount': money(booked),
        'refund': money(refund),
        'claim_remaining': money(claim_remaining),
        'cash_due': money(cash_due),
        'duplicates_ignored': duplicate_counter['reports'],
        'attempt_duplicates_ignored': duplicate_counter['attempts'],
        'issues': len(issues),
        'blocking': sum(1 for item in issues if item.get('severity') == 'blocking'),
    }


def apply(state):
    disposals, issues, duplicates = build_disposals(state)
    summary = summarize(disposals, issues, duplicates)
    state['disposals'] = disposals
    state['disposal_issues'] = issues
    state['disposal_summary'] = summary
    rows = []
    for item in disposals:
        latest = item['attempts'][-1] if item['attempts'] else None
        rows.append({
            'disposal_id': item['disposal_id'],
            'ticket': item['ticket'],
            'version': item['version'],
            'status_label': item['status_label'],
            'original_amount': item['original_amount'],
            'resale_amount': item['resale_amount'] or '—',
            'recoverable': item['recoverable'] if item['recoverable'] is not None else '—',
            'correction_pending': item['correction_pending'] if item['correction_pending'] is not None else '—',
            'difference': item['difference'] if item['difference'] is not None else '—',
            'refund': item['refund'] if item['refund'] is not None else '—',
            'claim_remaining': item['claim_remaining'] if item['claim_remaining'] is not None else '—',
            'refund': item['refund'] if item['refund'] is not None else '—',
            'cash_due': item['cash_due'] if item['cash_due'] is not None else '—',
            'buyer': (item['resale'] or {}).get('buyer') or item['abandonment']['buyer'],
            'attempt_id': (item['resale'] or {}).get('attempt_id') or (latest or {}).get('attempt_id', ''),
            'reported_at': item['abandonment']['reported_at'],
        })
    state['views']['disposal'] = {
        'title': '弃标处置',
        'rows': rows,
        'metrics': {
            '处置票': summary['count'],
            '原成交_CNY': summary['original_amount'],
            '重拍_CNY': summary['resale_amount'],
            '应追_CNY': summary['recoverable'],
        },
    }
