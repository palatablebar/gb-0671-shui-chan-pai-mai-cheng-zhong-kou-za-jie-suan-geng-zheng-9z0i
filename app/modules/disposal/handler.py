from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/disposal')
def page(request:Request):
    return render(request,'disposal')
@router.post('/api/disposal')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
