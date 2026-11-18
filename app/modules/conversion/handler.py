from fastapi import APIRouter, Request, Body
from app.web import render, run_module
router=APIRouter()
@router.get('/conversion')
def page(request:Request):
    return render(request,'conversion')
@router.post('/api/conversion')
def calculate(payload:dict=Body(default={})):
    return run_module(payload)
