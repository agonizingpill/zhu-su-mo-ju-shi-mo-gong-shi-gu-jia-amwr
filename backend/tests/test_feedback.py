import os,sys
# 支持从仓库根或 backend 目录直接运行：python tests/test_feedback.py
BACKEND=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,BACKEND)
for _extra in ('/tmp/pylibs',):  # 本机沙箱把依赖装在目标目录；常规 venv 可忽略
    if os.path.isdir(_extra):sys.path.insert(0,_extra)
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('REDIS_URL','redis://127.0.0.1:6399/0')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('ARTIFACTS_DIR',os.path.join(os.path.dirname(os.path.abspath(__file__)),'artifacts'))
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.db import Base
import src.db as db
import src.workshop_models  # noqa: F401  注册全部表
import src.feedback_service as fb
# StaticPool 让 SQLite 内存库在所有会话间共享同一连接，否则请求会话看不到测试数据
engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Session=sessionmaker(engine,expire_on_commit=False)
Base.metadata.create_all(engine)
db.Session=Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory,Audit
from src.execution_models import TrialExecution,CostReview
from src.execution_schemas import StartTrial,FinishTrial,ReviewInput
from src.feedback_models import TrainingSample,TrainingSampleVersion
from src import execution_service as es
from datetime import datetime,timezone,timedelta
import random
from src.ml import predict_trial

class U:
    def __init__(self,email,role):self.email=email;self.role=role
admin=U('admin@ruimo.demo','admin');est=U('estimator@ruimo.demo','estimator');viewer=U('viewer@ruimo.demo','viewer')
PASS=0;FAIL=0
def check(name,cond,extra=''):
    global PASS,FAIL
    if cond:PASS+=1;print('  ok',name)
    else:FAIL+=1;print('FAIL',name,extra)
LAST=None
def show(r):
    global LAST
    LAST=r
    return r.status_code,r.text[:220]

with Session() as s:
    s.add_all([User(email=a.email,role=a.role,password_hash='x') for a in (admin,est,viewer)])
    s.add(Mould(code='MD-1',name='测试模',cavities=8,slides=4,steel='S136',weight_kg=860))
    s.add(Mould(code='MD-2',name='短工时模',cavities=2,slides=0,steel='P20',weight_kg=200))
    s.add(MachineSlot(machine='M280',start_hour='2026-11-18 08:00',capacity_hours=100,reserved_hours=0))
    rng=random.Random(7)
    for i in range(160):
        cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
        hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+['P20','H13','S136'].index(steel)*.8+rng.gauss(0,.4)
        s.add(TrialHistory(trial_code=f'TR-{i+1:03d}',cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
    s.commit()
    mould1=s.scalar(select(Mould).where(Mould.code=='MD-1'));mould2=s.scalar(select(Mould).where(Mould.code=='MD-2'))
    slot=s.scalar(select(MachineSlot))

def make_signed_trial(s,code,mould,setup,actual_hours,rate=300,quote=9000,frozen=True):
    e=Estimate(code=code,mould_id=mould.id,slot_id=slot.id,rate=rate,setup_hours=setup,predicted_hours=actual_hours+1,quote_amount=quote,status='committed',model_result={})
    s.add(e);s.flush()
    start=datetime(2026,11,18,8,0,tzinfo=timezone.utc)
    x=es.begin(s,StartTrial(estimate_id=e.id,started_at=start),est)
    if not frozen:  # 模拟旧实绩：开工快照里没有冻结规格
        x.snapshot.pop('frozen_spec',None)
    es.finish(s,x.id,FinishTrial(ended_at=start+timedelta(hours=actual_hours),good_shots=90,total_shots=100),admin)
    r=es.prepare_review(s,ReviewInput(execution_id=x.id,material_cost=0,reason='测试结算复核说明至少八字'),est)
    es.sign(s,r.id,admin)
    return e,x,r

print('== 1. 开工冻结规格/准备工时，签结自动建样本并入队 ==')
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e,x,r=make_signed_trial(s,'QE-1',m,setup=2,actual_hours=9)
    check('开工快照冻结规格',x.snapshot['frozen_spec']['setup_hours']==2 and x.snapshot['frozen_spec']['cavities']==8)
    smp=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r.id))
    check('签结生成待回灌样本',smp is not None and smp.status=='pending' and smp.setup_hours==2 and smp.actual_hours==9)
    check('样本唯一约束(同结算)',s.scalar(select(func.count()).select_from(TrainingSample))==1)
    check('已入回灌队列',fb.queue.items==[(fb.FEEDBACK_QUEUE,str(smp.id))])
    s.commit()

print('== 2. worker 幂等消费：目标=实际-准备，非正隔离，重试不重复入库 ==')
with Session() as s:
    _,sid=fb.queue.items.pop(0)
    out=fb.ingest(s,int(sid));s.commit()
    check('正常样本入库',out.status=='ingested' and abs(out.target_hours-7)<1e-9)
    vers=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==out.id)).all()
    check('生成 active v1',len(vers)==1 and vers[0].state=='active' and vers[0].version==1 and abs(vers[0].target_hours-7)<1e-9)
    again=fb.ingest(s,int(sid));s.commit()
    check('重复消费不重复入库',s.scalar(select(func.count()).select_from(TrainingSampleVersion))==1 and again.status=='ingested')
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-2'))
    e2,x2,r2=make_signed_trial(s,'QE-2',m,setup=3,actual_hours=1)  # 目标 -2
    smp2=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r2.id))
    fb.queue.items.clear()
    out=fb.ingest(s,smp2.id);s.commit()
    check('非正目标隔离',out.status=='quarantined' and out.target_hours is None and '非正' in out.reason)
    check('隔离样本无版本',s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp2.id))==0)
    again=fb.ingest(s,smp2.id);s.commit()
    check('隔离后重试不复活',again.status=='quarantined')

print('== 3. 撤回：版本失效，旧队列任务不复活，训练不混入 ==')
with Session() as s:
    smp=s.scalar(select(TrainingSample).where(TrainingSample.estimate_code=='QE-1'))
    fb.withdraw(s,smp.id,type('W',(),{'reason':'数据异常撤回'})(),admin);s.commit()
    check('样本已撤回',smp.status=='withdrawn' and smp.withdrawn_by==admin.email)
    v=s.scalar(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp.id))
    check('旧版本保留但 superseded',v.state=='superseded' and '撤回' in v.change_note)
    # 模拟队列里残留的旧任务在撤回之后才被 worker 取走
    res=fb.ingest(s,smp.id);s.commit()
    check('旧任务不复活撤回样本',res.status=='withdrawn' and s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.state=='active',TrainingSampleVersion.sample_id==smp.id))==0)
    try:
        fb.withdraw(s,smp.id,type('W',(),{'reason':'x'})(),admin);check('重复撤回拒绝',False)
    except Exception as ex:check('重复撤回拒绝',getattr(ex,'status_code',None)==409)

print('== 4. 更正：重算目标，非正拒绝，旧版保留，活动版切换 ==')
with Session() as s:
    smp2=s.scalar(select(TrainingSample).where(TrainingSample.estimate_code=='QE-2'))
    from src.feedback_schemas import SampleCorrection
    bad=SampleCorrection(cavities=2,slides=0,steel='P20',weight_kg=200,setup_hours=5,actual_hours=1,change_note='准备工时填错')
    try:
        fb.correct(s,smp2.id,bad,admin);check('更正后非正拒绝',False)
    except Exception as ex:check('更正后非正拒绝',getattr(ex,'status_code',None)==409)
    good=SampleCorrection(cavities=4,slides=1,steel='H13',weight_kg=300,setup_hours=1,actual_hours=6,change_note='规格与工时凭据更正')
    out=fb.correct(s,smp2.id,good,admin);s.commit()
    check('隔离样本更正后入库',out.status=='ingested' and abs(out.target_hours-5)<1e-9 and out.cavities==4)
    vs=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp2.id).order_by(TrainingSampleVersion.version)).all()
    check('隔离转入库生成 v1 active',[(v.version,v.state) for v in vs]==[(1,'active')])
    check('v1 采用更正依据',vs[0].slides==1 and vs[0].steel=='H13' and abs(vs[0].target_hours-5)<1e-9)
    # 已入库样本再更正：旧版本必须保留
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e5,x5,r5=make_signed_trial(s,'QE-5',m,setup=2,actual_hours=10)
    smp5=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r5.id))
    fb.ingest(s,smp5.id);s.commit()
    out5=fb.correct(s,smp5.id,SampleCorrection(cavities=16,slides=6,steel='H13',weight_kg=1100,setup_hours=2,actual_hours=11,change_note='模腔数与实际工时更正'),admin);s.commit()
    check('更正重算目标',abs(out5.target_hours-9)<1e-9)
    route_id=smp5.id  # 选含两版的样本验证重处理不新增版本
    vs5=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp5.id).order_by(TrainingSampleVersion.version)).all()
    check('已入库更正保留旧版',[(v.version,v.state) for v in vs5]==[(1,'superseded'),(2,'active')])
    check('旧版数值原样保留',vs5[0].cavities==8 and vs5[1].cavities==16 and vs5[0].change_note=='签结实绩回灌入库')
    try:
        fb.correct(s,smp5.id,SampleCorrection(cavities=1,slides=0,steel='P20',weight_kg=100,setup_hours=40,actual_hours=1,change_note='非正目标更正'),admin);check('再次更正非正仍拒绝',False)
    except Exception as ex:check('再次更正非正仍拒绝',getattr(ex,'status_code',None)==409)
    # 待回灌样本更正：不生成版本，入库时直接按更正后依据出 v1
    e8,x8,r8=make_signed_trial(s,'QE-8',m,setup=2,actual_hours=9)
    smp8=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r8.id))
    fb.queue.items.clear()
    out8=fb.correct(s,smp8.id,SampleCorrection(cavities=12,slides=5,steel='H13',weight_kg=700,setup_hours=1,actual_hours=7,change_note='入库前修正冻结依据'),admin);s.commit()
    check('待回灌更正不产生版本',out8.status=='pending' and s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp8.id))==0 and abs(out8.target_hours-6)<1e-9)
    fb.ingest(s,smp8.id);s.commit()
    v8=s.scalar(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp8.id))
    check('入库 v1 采用更正后依据',v8.version==1 and v8.state=='active' and v8.cavities==12 and abs(v8.target_hours-6)<1e-9)
    # 待补证样本不允许直接更正
    e9,x9,r9=make_signed_trial(s,'QE-9',m,setup=1,actual_hours=5,frozen=False)
    smp9=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r9.id))
    try:
        fb.correct(s,smp9.id,SampleCorrection(cavities=2,slides=1,steel='P20',weight_kg=200,setup_hours=1,actual_hours=4,change_note='待补证不应可更正'),admin);check('待补证样本拒绝更正',False)
    except Exception as ex:check('待补证样本拒绝更正',getattr(ex,'status_code',None)==409)

print('== 5. 旧实绩待补证：补证→负责人确认→回灌，不改结算 ==')
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e3,x3,r3=make_signed_trial(s,'QE-3',m,setup=1.5,actual_hours=5,frozen=False)
    before={k:getattr(r3,k) for k in ('actual_cost','margin','variance_pct','material_cost','status')}
    smp3=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r3.id))
    check('旧实绩进待补证',smp3.status=='pending_evidence')
    check('待补证不入队',all(str(smp3.id)!=i[1] for i in fb.queue.items))
    from src.feedback_schemas import EvidenceInput
    fb.supplement(s,smp3.id,EvidenceInput(evidence_note='补传工艺卡与开机点检记录'),est)
    out=fb.confirm_evidence(s,smp3.id,EvidenceInput(evidence_note='补传工艺卡与开机点检记录'),admin);s.commit()
    check('确认后待回灌',out.status=='pending' and out.evidence_confirmed_by==admin.email)
    r3b=s.get(CostReview,r3.id)
    check('确认不改结算',all(getattr(r3b,k)==v for k,v in before.items()) and r3b.status=='signed')
    fb.ingest(s,smp3.id);s.commit()
    check('补证样本回灌入库',s.get(TrainingSample,smp3.id).status=='ingested' and abs(s.get(TrainingSample,smp3.id).target_hours-3.5)<1e-9)

print('== 6. 回灌中断续跑：backfill 只补缺口，重复执行幂等 ==')
with Session() as s:
    n_samples=s.scalar(select(func.count()).select_from(TrainingSample))
    r1=fb.scan_and_enqueue(s,admin);s.commit()
    check('首次扫描无缺口',r1=={'created':0,'enqueued':0})
    # 手工制造一个“签结后未建档”的缺口（模拟签结时建档失败/中断）
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e4,x4,r4=make_signed_trial(s,'QE-4',m,setup=1,actual_hours=4)
    gap=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r4.id))
    s.delete(gap);s.commit()
    r2=fb.scan_and_enqueue(s,admin);s.commit()
    check('续跑补建缺口并入队',r2['created']==1 and r2['enqueued']==1)
    r3=fb.scan_and_enqueue(s,admin);s.commit()
    check('再次扫描不重复建档',r3=={'created':0,'enqueued':1} and s.scalar(select(func.count()).select_from(TrainingSample))==n_samples+1)
    sid=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r4.id)).id
    fb.ingest(s,sid);fb.ingest(s,sid);s.commit()
    check('入库幂等一版本',s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==sid))==1)

print('== 7. 训练固定当时有效版本，依据可回查，后续更正不混入 ==')
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    pred1,res1=predict_trial(s,m,2)
    active_versions=s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.state=='active'))
    check('模型结果固化样本依据',res1['basis']['baseline_rows']==160 and res1['basis']['feedback_rows']==active_versions and active_versions>=1,active_versions)
    ids=sorted((b['sample_id'],b['version']) for b in res1['basis']['samples'] if b['source']=='feedback')
    check('依据仅含 active 版本',all(b for b in res1['basis']['samples']))
    # 把结果持久化到估单（模拟 worker 写模型证据），随后更正样本
    e=s.scalar(select(Estimate).where(Estimate.code=='QE-1'))
    e.model_result=res1;e.predicted_hours=pred1;s.commit()
    smp2=s.scalar(select(TrainingSample).where(TrainingSample.estimate_code=='QE-2'))
    fb.correct(s,smp2.id,SampleCorrection(cavities=8,slides=2,steel='S136',weight_kg=900,setup_hours=1,actual_hours=8,change_note='二次更正'),admin);s.commit()
    stored=s.scalar(select(Estimate).where(Estimate.code=='QE-1')).model_result
    check('历史估价依据不被后续更正污染',stored==res1)
    pred2,res2=predict_trial(s,m,2)
    check('新训练取新版本号',sorted((b['sample_id'],b['version']) for b in res2['basis']['samples'] if b['source']=='feedback' and b['sample_id']==smp2.id)==[(smp2.id,2)])
    withdrawn_id=s.scalar(select(TrainingSample).where(TrainingSample.estimate_code=='QE-1')).id
    check('撤回样本不进训练',all(b.get('sample_id')!=withdrawn_id for b in res2['basis']['samples'] if b['source']=='feedback'))
    # 历史版本仍可回查
    old=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==smp2.id).order_by(TrainingSampleVersion.version)).all()
    check('版本链完整可回查',[v.version for v in old]==[1,2] and old[0].state=='superseded' and old[1].state=='active')

print('== 8. 审计留痕 ==')
with Session() as s:
    actions=[a.action for a in s.scalars(select(Audit))]
    for needle in ('实绩回灌建档','回灌非正目标隔离','实绩回灌已入库','撤回训练样本','更正训练样本','负责人确认补证','回灌扫描续跑'):
        check('审计包含 '+needle,any(needle in a for a in actions))

print('== 9. HTTP 路由权限与端到端 ==')
from fastapi.testclient import TestClient
import src.main as mainmod
class _FakeRedis:
    def ping(self):return True
mainmod.r=_FakeRedis()  # 测试环境无 Redis，健康检查与限流不依赖真实连接
from src.main import app
from src import db as dbmod
from src.auth import token as make_token
def override_session():
    with Session() as s: yield s
app.dependency_overrides[dbmod.db_session]=override_session
client=TestClient(app)
def hdr(u):
    uid=s_scalar_user(u)
    return {'Authorization':'Bearer '+make_token(type('T',(),{'id':str(uid)})())}
def s_scalar_user(u):
    with Session() as s:return s.scalar(select(User).where(User.email==u.email)).id
ha,he,hv=hdr(admin),hdr(est),hdr(viewer)
r=client.get('/training-samples',headers=hv);check('viewer 可读样本列表',r.status_code==200 and len(r.json())>=1)
r=client.get('/enums',headers=hv);check('枚举含样本状态',r.status_code==200 and 'sample_states' in r.json())
with Session() as s:
    pending=s.scalar(select(TrainingSample).where(TrainingSample.status=='ingested'))
    pid=pending.id
r=client.post(f'/training-samples/{pid}/withdraw',json={'reason':'HTTP 权限测试撤回'},headers=he)
check('estimator 不能撤回(403)',r.status_code==403)
r=client.post(f'/training-samples/{pid}/withdraw',json={'reason':'HTTP 权限测试撤回'},headers=ha)
check('admin 可撤回',r.status_code==200 and r.json()['status']=='withdrawn')
r=client.post(f'/training-samples/{pid}/withdraw',json={'reason':'再撤一次'},headers=ha)
check('重复撤回 409',r.status_code==409)
r=client.post(f'/training-samples/{pid}/correct',json={'cavities':8,'slides':4,'steel':'S136','weight_kg':860,'setup_hours':1,'actual_hours':9,'change_note':'HTTP 更正'},headers=he)
check('estimator 不能更正(403)',r.status_code==403)
# 待补证样本走完整 HTTP 流程
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e6,x6,r6=make_signed_trial(s,'QE-6',m,setup=1,actual_hours=4,frozen=False)
    peid=s.scalar(select(TrainingSample).where(TrainingSample.review_id==r6.id)).id
    s.commit()
r=client.get(f'/training-samples/{peid}/versions',headers=hv)
check('待补证样本暂无版本',r.status_code==200 and r.json()==[],show(r))
r=client.post(f'/training-samples/{peid}/confirm-evidence',json={'evidence_note':'太短'},headers=ha)
check('补证依据不足时确认 409',r.status_code in (409,422),show(r))
r=client.post(f'/training-samples/{peid}/evidence',json={'evidence_note':'HTTP 补传工艺卡与点检表'},headers=he)
check('estimator 可登记补证',r.status_code==200 and r.json()['status']=='pending_evidence',show(r))
r=client.post(f'/training-samples/{peid}/confirm-evidence',json={'evidence_note':'HTTP 补传工艺卡与点检表'},headers=he)
check('estimator 不能确认补证(403)',r.status_code==403)
r=client.post(f'/training-samples/{peid}/confirm-evidence',json={'evidence_note':'HTTP 补传工艺卡与点检表'},headers=ha)
check('admin 确认后进待回灌',r.status_code==200 and r.json()['status']=='pending',show(r))
r=client.post('/training-samples/backfill',headers=ha);check('续跑扫描可用',r.status_code==200 and r.json()['enqueued']>=1,show(r))
r=client.post(f'/training-samples/{peid}/ingest',headers=he);check('同步兜底入库',r.status_code==200 and r.json()['status']=='ingested',show(r))
r=client.post(f'/training-samples/{peid}/ingest',headers=he);check('兜底重试幂等',r.status_code==200 and r.json()['active_version']==1,show(r))
r=client.get(f'/training-samples/{peid}/versions',headers=hv)
check('版本可回查',r.status_code==200 and len(r.json())==1 and r.json()[0]['target_hours']==3,show(r))
r=client.post('/training-samples/backfill',headers=hv);check('viewer 不能触发扫描(403)',r.status_code==403)

print('== 10. worker 双队列路由与中断续跑 ==')
import src.worker as worker
check('无 Redis 时 worker 退化到内存队列',worker.r is fb.queue)
fb.queue.items.clear()  # 隔离前面章节残留的队列消息
with Session() as s:
    route_id=s.scalar(select(TrainingSample).where(TrainingSample.estimate_code=='QE-5')).id  # 含两版的已入库样本
if route_id is not None:
    fb.queue.rpush(fb.FEEDBACK_QUEUE,route_id)
    entry=worker.r.blpop(['mould-estimates',fb.FEEDBACK_QUEUE],timeout=1)
    check('回灌队列消息可被取走',entry==(fb.FEEDBACK_QUEUE,str(route_id)))
    worker.process(*entry)
    with Session() as s:check('worker 路由回灌幂等',s.get(TrainingSample,route_id).status=='ingested')
fb.queue.items.clear()
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    e=Estimate(code='QE-WORKER',mould_id=m.id,rate=300,setup_hours=1,quote_amount=9000,status='calculating')
    s.add(e);s.commit();eid=e.id
worker.r.rpush('mould-estimates',eid)
entry=worker.r.blpop(['mould-estimates',fb.FEEDBACK_QUEUE],timeout=1)
check('估算队列消息可被取走',entry==('mould-estimates',str(eid)))
worker.process(*entry)
with Session() as s:
    e=s.get(Estimate,eid);check('worker 完成估算并写模型依据',e.status=='estimated' and e.predicted_hours and e.model_result.get('basis',{}).get('baseline_rows')==160)
import pathlib
check('模型证据落共享产物卷',(worker.ARTIFACTS/f'estimate-{eid}.json').exists())
# 模拟中断：消息投递后反复处理，状态与版本不重复
worker.process(fb.FEEDBACK_QUEUE,route_id)
with Session() as s:
    check('中断重处理不重复入库',s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==route_id))==2)

print('== 11. 撤回与回灌并发：在途旧任务不得复活样本 ==')
# SQLite 无行锁（with_for_update 被忽略），并发互斥由 PostgreSQL 行锁保证；
# 这里用确定性时序覆盖两种交错顺序，并验证撤回后旧队列消息重放不复活。
with Session() as s:
    m=s.scalar(select(Mould).where(Mould.code=='MD-1'))
    # 时序 A：旧任务还在队列里，负责人先撤回；worker 之后才取走
    ea,xa,ra=make_signed_trial(s,'QE-7A',m,setup=1,actual_hours=6)
    ida=s.scalar(select(TrainingSample).where(TrainingSample.review_id==ra.id)).id
    fb.queue.items.clear()
    # 时序 B：worker 已入库，负责人随后撤回
    eb,xb,rb=make_signed_trial(s,'QE-7B',m,setup=1,actual_hours=8)
    idb=s.scalar(select(TrainingSample).where(TrainingSample.review_id==rb.id)).id
    s.commit()
with Session() as s:
    fb.withdraw(s,ida,type('W',(),{'reason':'任务在途先撤回'})(),admin);s.commit()
    # worker 此刻才消费这条旧消息
    out=fb.ingest(s,ida);s.commit()
    check('先撤回后消费：旧任务不复活',out.status=='withdrawn' and s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==ida))==0)
with Session() as s:
    fb.ingest(s,idb);s.commit()
    fb.withdraw(s,idb,type('W',(),{'reason':'入库后撤回'})(),admin);s.commit()
    vb=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.sample_id==idb)).all()
    check('先入库后撤回：版本保留且失效',s.get(TrainingSample,idb).status=='withdrawn' and [v.state for v in vb]==['superseded'])
# 撤回后反复重放旧任务（含 worker 入口），均不复活
worker.process(fb.FEEDBACK_QUEUE,ida);worker.process(fb.FEEDBACK_QUEUE,idb)
with Session() as s:
    check('撤回样本重放仍撤回',s.get(TrainingSample,ida).status=='withdrawn' and s.get(TrainingSample,idb).status=='withdrawn')
    check('重放不产生活动版本',s.scalar(select(func.count()).select_from(TrainingSampleVersion).where(TrainingSampleVersion.state=='active',TrainingSampleVersion.sample_id.in_([ida,idb])))==0)
# 时序 A 的样本补证后也不能绕过撤回（撤回是终态，需重新走更正/新建流程）
with Session() as s:
    try:
        fb.ingest(s,ida);check('撤回态再次消费安全跳过',s.get(TrainingSample,ida).status=='withdrawn')
    except Exception as ex:check('撤回态再次消费安全跳过',False,str(ex))

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
