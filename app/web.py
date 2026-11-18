import json
from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META
from app.engine import evaluate
from app import store

templates=Jinja2Templates(directory=str(ROOT/'app/templates'))
templates.env.filters['jsontext']=lambda value: json.dumps(value,ensure_ascii=False,sort_keys=True) if value not in (None,'') else '—'

def _db_path():
    from app.main import DB_PATH
    return DB_PATH

def run_module(payload):
    """模块入口也写入完整批次，使 run_id 可在各页间复用同一结果。"""
    return store.save(_db_path(),evaluate(payload))

def render(request:Request,key):
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(_db_path(),run_id)
    else:
        state=store.save(_db_path(),evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    return templates.TemplateResponse(request=request,name=f'{key}.html',context={'meta':META,'key':key,'state':state,'view':state['views'][key]})
