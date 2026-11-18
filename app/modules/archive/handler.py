from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/archive')
def page(request:Request):
    return render(request,'archive')
@router.post('/api/archive')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
