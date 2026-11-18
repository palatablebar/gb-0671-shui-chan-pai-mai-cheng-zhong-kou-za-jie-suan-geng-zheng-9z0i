from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/pairing')
def page(request:Request):
    return render(request,'pairing')
@router.post('/api/pairing')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
