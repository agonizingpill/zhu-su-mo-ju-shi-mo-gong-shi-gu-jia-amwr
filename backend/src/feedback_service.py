"""实绩回灌：签结建样本、worker 入库幂等消费、补证/撤回/更正与版本留痕。"""
import os,logging
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select,func
from .models import Estimate,Mould
from .execution_models import TrialExecution,CostReview
from .feedback_models import TrainingSample,TrainingSampleVersion
from .services import item,serialize,record
FEEDBACK_QUEUE='training-feedback'
def _queue():
    """Redis 不可用时退化为进程内队列，本地/测试与 compose 行为一致。"""
    try:
        import redis
        q=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True);q.ping();return q
    except Exception:
        logging.getLogger('feedback').warning('redis unavailable, fallback in-process queue')
        return _MemoryQueue()
class _MemoryQueue:
    def __init__(self): self.items=[]
    def rpush(self,k,v): self.items.append((k,str(v)))
    def blpop(self,keys,timeout=2):
        wanted=tuple(keys) if isinstance(keys,(list,tuple)) else (keys,)
        for i,(k,_) in enumerate(self.items):
            if k in wanted:return self.items.pop(i)
        return None
    def ping(self): return True
queue=_queue()
def enqueue(sample_id:int):
    # 回灌入队失败不拖垮签结；扫描端点会兜底补投，重试不重复入库
    try: queue.rpush(FEEDBACK_QUEUE,int(sample_id))
    except Exception: logging.exception('enqueue feedback sample %s failed',sample_id)
def now(): return datetime.now(timezone.utc).isoformat()
def view(s,x):
    d=serialize(x)
    d['active_version']=s.scalar(select(func.max(TrainingSampleVersion.version)).where(TrainingSampleVersion.sample_id==x.id,TrainingSampleVersion.state=='active'))
    d['version_count']=s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==x.id))
    return d
def _frozen_basis(x,e,m):
    return {'estimate_code':e.code,'mould_code':m.code,'mould_id':m.id,'review_id':None,'execution_id':x.id,
            'cavities':m.cavities,'slides':m.slides,'steel':m.steel,'weight_kg':m.weight_kg,
            'setup_hours':e.setup_hours,'actual_hours':x.actual_hours,'predicted_hours':x.snapshot['predicted_hours'],
            'rate':x.snapshot['rate'],'frozen_at':now()}
def create_from_review(s,r,x,e,m,u,missing_evidence=False):
    """签结或回灌扫描时建立样本。同一结算/实绩只建一条，重复调用安全。"""
    existing=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r.id))
    if existing:return existing
    basis=_frozen_basis(x,e,m);basis['review_id']=r.id
    sample=TrainingSample(review_id=r.id,execution_id=x.id,estimate_code=e.code,
        cavities=m.cavities,slides=m.slides,steel=m.steel,weight_kg=m.weight_kg,
        setup_hours=e.setup_hours,actual_hours=x.actual_hours,
        status='pending_evidence' if missing_evidence else 'pending',
        evidence_note='',created_by=u.email if u else 'system',
        snapshot=basis)
    s.add(sample);s.flush()
    record(s,u or _system(),'实绩回灌建档 '+e.code+('（待补证）' if missing_evidence else ''))
    if not missing_evidence:enqueue(sample.id)
    return sample
class _System: email='system'
def _system(): return _System()
def scan_and_enqueue(s,u):
    """扫描已签结但未建档的结算，补建样本并补投队列；中断后重跑只处理缺口。"""
    created,enqueued=0,0
    rows=s.scalars(select(CostReview).where(CostReview.status=='signed').order_by(CostReview.id)).all()
    for r in rows:
        x=item(s,TrialExecution,r.execution_id);e=item(s,Estimate,x.estimate_id);m=item(s,Mould,e.mould_id)
        sample=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r.id))
        if sample is None:
            # 旧实绩：开工时没有冻结规格快照即视为缺依据，进待补证，不猜测来源
            missing=not x.snapshot.get('frozen_spec')
            sample=create_from_review(s,r,x,e,m,u,missing_evidence=missing);created+=1
        if sample.status in ('pending',):
            enqueue(sample.id);enqueued+=1
    record(s,u,'回灌扫描续跑 新增%d 补投%d'%(created,enqueued))
    return {'created':created,'enqueued':enqueued}
def ingest(s,sample_id:int):
    """worker 消费：行锁取样本，撤回/隔离/已入库均不重复写版本。"""
    sample=s.scalar(select(TrainingSample).where(TrainingSample.id==sample_id).with_for_update())
    if sample is None:return None
    # 终态守卫：撤回后任何在途/重放的旧任务都不得复活样本；隔离与已入库同样幂等跳过
    if sample.status in ('ingested','quarantined','withdrawn','pending_evidence'):return sample
    target=round(sample.actual_hours-sample.setup_hours,4)
    if target<=0:
        sample.status='quarantined';sample.target_hours=None
        sample.reason='训练目标=实际工时-准备工时=%s，非正值隔离不入库'%target
        record(s,_system(),'回灌非正目标隔离 '+sample.estimate_code);return sample
    sample.target_hours=target;sample.status='ingested'
    version=(s.scalar(select(func.max(TrainingSampleVersion.version)).where(TrainingSampleVersion.sample_id==sample.id)) or 0)+1
    s.add(TrainingSampleVersion(sample_id=sample.id,version=version,state='active',
        cavities=sample.cavities,slides=sample.slides,steel=sample.steel,weight_kg=sample.weight_kg,
        setup_hours=sample.setup_hours,actual_hours=sample.actual_hours,target_hours=target,
        change_note='签结实绩回灌入库',created_by=sample.created_by,basis=_version_basis(sample,version)))
    record(s,_system(),'实绩回灌已入库 '+sample.estimate_code+' v%d'%version)
    return sample
def _version_basis(sample,version):
    snap=dict(sample.snapshot);snap.update({'sample_id':sample.id,'version':version,'target_hours':sample.target_hours,
        'ingested_at':now(),'source':'signed_actual'})
    return snap
def supplement(s,sid,data,u):
    sample=_locked(s,sid)
    if sample.status!='pending_evidence':raise HTTPException(409,'仅待补证样本可登记补证依据')
    sample.evidence_note=data.evidence_note.strip()
    record(s,u,'登记回灌补证依据 '+sample.estimate_code);return sample
def confirm_evidence(s,sid,data,u):
    sample=_locked(s,sid)
    if sample.status!='pending_evidence':raise HTTPException(409,'该样本不在待补证状态')
    note=(data.evidence_note or '').strip() or sample.evidence_note
    if len(note)<4:raise HTTPException(409,'补证依据不足，负责人不能确认')
    # 补证只解决训练依据，不改结算、不改实绩数值
    sample.evidence_note=note;sample.evidence_confirmed_by=u.email;sample.status='pending';sample.reason=''
    enqueue(sample.id);record(s,u,'负责人确认补证 '+sample.estimate_code);return sample
def withdraw(s,sid,data,u):
    sample=_locked(s,sid)
    if sample.status=='withdrawn':raise HTTPException(409,'样本已撤回，不可重复撤回')
    if sample.status not in ('pending','pending_evidence','ingested','quarantined'):raise HTTPException(409,'当前状态不可撤回')
    for v in s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==sample.id,TrainingSampleVersion.state=='active')):
        v.state='superseded';v.change_note=(v.change_note+' / 样本撤回：'+data.reason.strip())[:600]
    sample.status='withdrawn';sample.withdrawn_by=u.email;sample.reason=data.reason.strip();sample.target_hours=None
    record(s,u,'撤回训练样本 '+sample.estimate_code);return sample
def correct(s,sid,data,u):
    sample=_locked(s,sid)
    if sample.status not in ('ingested','quarantined','pending'):raise HTTPException(409,'仅待回灌、已入库或隔离样本可由负责人更正；待补证样本请先走补证确认')
    target=round(data.actual_hours-data.setup_hours,4)
    if target<=0:raise HTTPException(409,'更正后训练目标=实际工时-准备工时=%s 非正，请撤回而非更正'%target)
    sample.cavities=data.cavities;sample.slides=data.slides;sample.steel=data.steel;sample.weight_kg=data.weight_kg
    sample.setup_hours=data.setup_hours;sample.actual_hours=data.actual_hours;sample.target_hours=target
    sample.reason=''
    if sample.status=='pending':
        # 尚未入库：直接改冻结依据，入库时按更正后数值生成 v1，不产生多余版本
        record(s,u,'更正待回灌样本 '+sample.estimate_code);return sample
    sample.status='ingested'
    last=s.scalar(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==sample.id).order_by(TrainingSampleVersion.version.desc()))
    for v in s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==sample.id,TrainingSampleVersion.state=='active')):
        v.state='superseded'
    version=(last.version if last else 0)+1
    s.add(TrainingSampleVersion(sample_id=sample.id,version=version,state='active',
        cavities=data.cavities,slides=data.slides,steel=data.steel,weight_kg=data.weight_kg,
        setup_hours=data.setup_hours,actual_hours=data.actual_hours,target_hours=target,
        change_note=data.change_note.strip(),created_by=u.email,basis=_version_basis(sample,version)))
    record(s,u,'更正训练样本 '+sample.estimate_code+' v%d（旧版保留）'%version);return sample
def _locked(s,sid):
    x=s.scalar(select(TrainingSample).where(TrainingSample.id==sid).with_for_update())
    if not x:raise HTTPException(404,'训练样本不存在')
    return x
def versions(s,sid):
    item(s,TrainingSample,sid)
    return [serialize(v) for v in s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==sid).order_by(TrainingSampleVersion.version))]
def list_view(s):
    out=[]
    for x in s.scalars(select(TrainingSample).order_by(TrainingSample.id.desc())):
        d=view(s,x);out.append(d)
    return out
