from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/auction')
def page(request:Request):
    return render(request,'auction')
@router.post('/api/auction')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
