import io, json, zipfile
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine']},ensure_ascii=False,indent=2))
        if result.get('disposals') is not None:
            archive.writestr('disposals.json',json.dumps({
                'summary':result.get('disposal_summary',{}),
                'disposals':result['disposals'],
                'issues':result.get('disposal_issues',[]),
                'settlement_rows':result.get('settlement_rows',[]),
            },ensure_ascii=False,indent=2))
    return buffer.getvalue()
