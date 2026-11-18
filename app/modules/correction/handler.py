from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/correction')
def page(request:Request):
    return render(request,'correction')
@router.post('/api/correction')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
