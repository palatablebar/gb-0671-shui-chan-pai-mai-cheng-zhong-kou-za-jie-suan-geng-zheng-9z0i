from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/intake')
def page(request:Request):
    return render(request,'intake')
@router.post('/api/intake')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
