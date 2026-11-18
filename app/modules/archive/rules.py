import hashlib, json

def _seal_payload(state):
    # 封存摘要覆盖生效成交与弃标处置：重拍切换或应追差额变化后旧封存不再有效。
    return {
        'final': state['final'],
        'disposals': [{key: item.get(key) for key in
                       ('disposal_id','ticket','version','status','original_amount','resale_amount',
                        'recoverable','difference','settled_amount','paid','due','refund','claim_remaining')}
                      for item in state.get('disposals') or []],
        'issues': state.get('disposal_issues') or [],
    }

def archive_decision(state):
    ready=state['balance']['row_balanced'] and state['balance']['money_balanced'] and not state['balance']['blocked']
    seal=hashlib.sha256(json.dumps(_seal_payload(state),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    disposals=state.get('disposals') or []
    summary=state.get('disposal_summary') or {}
    # 弃标处置只做替换，原始成交、称重与证据随封存保留；未完成的处置在归档页单列。
    return {'seal':seal,'status':'可归档' if ready else '待处理','tickets':len(state['final']),
            'reasons':len(state['quarantine']),'disposals':len(disposals),
            'pending_disposal':int(summary.get('pending') or 0),
            'switch_failed':int(summary.get('switch_failed') or 0),
            'recoverable':summary.get('recoverable') or '0.00'}

def apply(state):
    state['archive']=archive_decision(state)
    state['views']['archive']={'title':'结算封存','rows':state['version_audit'],'metrics':{'封存票':state['archive']['tickets'],'隔离证据':len(state['quarantine']),'弃标处置':state['archive']['disposals'],'状态':state['archive']['status']}}
