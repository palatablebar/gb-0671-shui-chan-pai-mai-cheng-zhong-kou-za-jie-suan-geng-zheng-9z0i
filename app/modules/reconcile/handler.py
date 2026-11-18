from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/reconcile')
def page(request:Request):
    return render(request,'reconcile')
@router.post('/api/reconcile')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
