import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from sklearn.linear_model import Ridge
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
from .models import TrialHistory
from .feedback_models import TrainingSample,TrainingSampleVersion
from sqlalchemy import select
STEEL={'P20':0,'H13':1,'S136':2}
def vector(cavities,slides,weight_kg,steel): return [cavities,slides,weight_kg,STEEL[steel]]
def _baseline_rows(s):
    rows=s.scalars(select(TrialHistory).order_by(TrialHistory.id)).all()
    return [vector(r.cavities,r.slides,r.weight_kg,r.steel) for r in rows],[r.actual_hours for r in rows],[{'source':'baseline','trial_code':r.trial_code,'target_hours':r.actual_hours} for r in rows]
def _feedback_rows(s):
    # 每次训练固定当时 active 的样本版本；撤回无 active 版本，更正后旧版本已 superseded 不会混入
    versions=s.scalars(select(TrainingSampleVersion).where(TrainingSampleVersion.state=='active').order_by(TrainingSampleVersion.id)).all()
    xs=[vector(v.cavities,v.slides,v.weight_kg,v.steel) for v in versions]
    ys=[v.target_hours for v in versions]
    basis=[{'source':'feedback','sample_id':v.sample_id,'version':v.version,'estimate_code':v.basis.get('estimate_code'),
            'target_hours':v.target_hours,'actual_hours':v.actual_hours,'setup_hours':v.setup_hours,
            'ingested_at':v.basis.get('ingested_at'),'review_id':v.basis.get('review_id')} for v in versions]
    return xs,ys,basis
def predict_trial(s,mould,setup):
    bx,by,bb=_baseline_rows(s);fx,fy,fb=_feedback_rows(s)
    x=np.array(bx+fx);y=np.array(by+fy);basis=bb+fb
    if len(y)<30: raise ValueError('完成试模样本少于 30 条，不能估价')
    a,b,c,d=train_test_split(x,y,test_size=.2,random_state=42)
    model=make_pipeline(PolynomialFeatures(degree=2,interaction_only=True,include_bias=False),StandardScaler(),Ridge(alpha=3.0))
    model.fit(a,c)
    pred=max(.5,float(model.predict([vector(mould.cavities,mould.slides,mould.weight_kg,mould.steel)])[0]))+setup
    mae=float(mean_absolute_error(d,model.predict(b)))
    names=model[0].get_feature_names_out(['模腔','滑块','重量','钢材'])
    coeff=sorted(zip(names,model[-1].coef_),key=lambda x:abs(x[1]),reverse=True)
    return round(pred,2),{'mae_hours':round(mae,3),'training_rows':len(a),'holdout_rows':len(b),'seed':42,'alpha':3,
        'features':[{'name':n,'coefficient':round(float(v),3)} for n,v in coeff],
        'validation':[{'actual':round(float(t),2),'predicted':round(float(p),2)} for t,p in zip(d,model.predict(b))],
        'basis':{'baseline_rows':len(by),'feedback_rows':len(fy),'frozen_at':_now(),'samples':basis},
        'note':'系数来自标准化后的交叉特征，仅解释统计关系；预测包含本单准备工时。训练样本固定取数当时的基线试模与回灌有效版本，后续更正/撤回不影响本次估价依据。'}
def _now():
    from datetime import datetime,timezone
    return datetime.now(timezone.utc).isoformat()
