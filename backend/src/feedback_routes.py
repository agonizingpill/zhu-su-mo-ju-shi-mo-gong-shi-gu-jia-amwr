from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from .db import db_session
from .auth import current,writers,admin
from .feedback_models import TrainingSample
from .feedback_schemas import EvidenceInput,EvidenceConfirm,SampleCorrection,SampleWithdraw
from . import feedback_service as svc
router=APIRouter()
@router.get('/training-samples')
def samples(s=Depends(db_session),u=Depends(current)):return svc.list_view(s)
@router.get('/training-samples/{sid}/versions')
def sample_versions(sid:int,s=Depends(db_session),u=Depends(current)):return svc.versions(s,sid)
@router.post('/training-samples/backfill')
def backfill(s=Depends(db_session),u=Depends(writers)):
    # 续跑扫描：只补已签结未建档的缺口并补投待回灌任务，重试不重复入库
    x=svc.scan_and_enqueue(s,u);s.commit();return x
@router.post('/training-samples/{sid}/ingest')
def ingest(sid:int,s=Depends(db_session),u=Depends(writers)):
    # worker 通道的同步兜底：与队列消费同一幂等入口，重复执行不重复入库
    x=svc.ingest(s,sid)
    if x is None:raise HTTPException(404,'训练样本不存在')
    s.commit();return svc.view(s,x)
@router.post('/training-samples/{sid}/evidence')
def evidence(sid:int,p:EvidenceInput,s=Depends(db_session),u=Depends(writers)):
    x=svc.supplement(s,sid,p,u);s.commit();return svc.view(s,x)
@router.post('/training-samples/{sid}/confirm-evidence')
def confirm_evidence(sid:int,p:EvidenceConfirm,s=Depends(db_session),u=Depends(admin)):
    # 补证经负责人确认后进入回灌；确认只解决训练依据，不改结算
    x=svc.confirm_evidence(s,sid,p,u);s.commit();return svc.view(s,x)
@router.post('/training-samples/{sid}/withdraw')
def withdraw(sid:int,p:SampleWithdraw,s=Depends(db_session),u=Depends(admin)):
    x=svc.withdraw(s,sid,p,u);s.commit();return svc.view(s,x)
@router.post('/training-samples/{sid}/correct')
def correct(sid:int,p:SampleCorrection,s=Depends(db_session),u=Depends(admin)):
    # 更正生成新活动版本，旧版本 superseded 保留；已在跑的训练仍用旧版本
    x=svc.correct(s,sid,p,u);s.commit();return svc.view(s,x)
