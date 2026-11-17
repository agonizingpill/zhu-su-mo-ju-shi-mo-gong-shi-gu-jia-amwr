import os,time,json,logging
from pathlib import Path
import redis
from .db import Session
from .models import Estimate,Mould
from .ml import predict_trial
from . import feedback_service as fb
try:
    r=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True);r.ping()
except Exception:
    r=fb.queue  # 本地/测试无 Redis 时使用进程内退化队列
ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/artifacts'))
try:
    ARTIFACTS.mkdir(exist_ok=True)
except PermissionError:
    # 本地无命名卷时回退临时目录；compose 环境仍写共享命名卷 /artifacts
    ARTIFACTS=Path(os.environ.get('ARTIFACTS_FALLBACK','/tmp/mould-artifacts'));ARTIFACTS.mkdir(exist_ok=True)
def process(queue_name,payload):
    """单条作业处理，供主循环调用；回灌与估算各自幂等，失败回滚不吞消息以外的状态。"""
    with Session() as s:
        try:
            if queue_name==fb.FEEDBACK_QUEUE:
                # 幂等消费：行锁校验状态，中断重投不会重复入库，撤回后旧任务不复活样本
                fb.ingest(s,int(payload))
            else:
                eid=int(payload);e=s.get(Estimate,eid)
                if not e or e.status!='calculating':return
                try:
                    e.predicted_hours,e.model_result=predict_trial(s,s.get(Mould,e.mould_id),e.setup_hours)
                    e.status='estimated'
                    (ARTIFACTS/f'estimate-{eid}.json').write_text(json.dumps(e.model_result,ensure_ascii=False))
                except Exception as ex:
                    logging.exception('estimate failed');e.status='failed';e.model_result={'error':str(ex)}
            s.commit()
        except Exception:
            logging.exception('job failed queue=%s payload=%s',queue_name,payload);s.rollback()
def main():
    while True:
        Path('/tmp/worker-heartbeat').touch()
        entry=r.blpop(['mould-estimates',fb.FEEDBACK_QUEUE],timeout=2)
        if not entry:continue
        process(*entry)
if __name__=='__main__':main()
