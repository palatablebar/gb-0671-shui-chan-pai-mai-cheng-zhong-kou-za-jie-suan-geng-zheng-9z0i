REASONS = {
    'COLUMN_COUNT':'列数错误', 'MISSING_ID':'编号缺失', 'DUPLICATE_CONFLICT':'重复编号冲突',
    'MISSING_VALUE':'数值缺失', 'BAD_NUMBER':'数值格式错误', 'BAD_TIME':'时间格式错误',
    'BAD_UNIT':'单位不受支持', 'UNKNOWN_REFERENCE':'引用对象不存在', 'INVALID_RANGE':'范围错误',
    'VERSION_CONFLICT':'版本冲突', 'EMPTY_FILE':'空文件', 'BLANK_FILE':'全空行',
    'HEADER_ONLY':'仅有表头', 'STALE_VERSION':'复核版本已更新', 'NOT_FOUND':'批次不存在',
    'REPORT_CONFLICT':'弃标回报冲突', 'MISSING_DISPOSAL_ID':'处置编号缺失',
    'BAD_DISPOSAL_INPUT':'处置输入无效', 'MISSING_TICKET':'弃标回报缺票号',
    'DEAL_NOT_FOUND':'没有可替代的成交', 'TICKET_ALREADY_DISPOSED':'同票已有弃标处置',
    'BAD_REPORT_VERSION':'弃标版本无效', 'VERSION_MISMATCH':'弃标版本不存在',
    'ABANDONMENT_EVIDENCE_MISSING':'弃标证据缺失', 'CORRECTION_AFTER_ABANDONMENT':'弃标后有更正',
    'ORPHAN_RESALE':'重拍未关联回报', 'RESALE_STATUS_UNKNOWN':'重拍状态无法识别',
    'RESALE_EVIDENCE_MISSING':'重拍证据缺失', 'RESALE_SWITCH_FAILED':'重拍切换失败',
    'RESALE_AMOUNT_MISMATCH':'重拍金额与重算不符', 'RESALE_INCOMPLETE':'重拍计价不完整',
    'INVALID_RESALE_AMOUNT':'重拍金额越界',
}
def reject(state, record, code, detail):
    state['quarantine'].append({'line':record.get('_line',0),'id':record.get('id',''),
       'code':code,'detail':detail,'raw':{k:v for k,v in record.items() if not k.startswith('_')}})
