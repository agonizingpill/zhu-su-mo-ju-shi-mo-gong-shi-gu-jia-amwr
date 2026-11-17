"""Actuals fed back into training: one sample per signed review, with versioned evidence.

开工冻结工艺规格与准备工时；签结后净耗训练目标=实际工时-准备工时，非正隔离。
旧实绩缺依据进入待补证；撤回与更正在版本表留痕，训练只取当时 active 版本。
"""
from sqlalchemy import String,Float,Integer,ForeignKey,JSON,CheckConstraint,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class TrainingSample(Base):
    __tablename__='training_samples'
    __table_args__=(
        UniqueConstraint('review_id',name='uq_training_sample_review'),
        UniqueConstraint('execution_id',name='uq_training_sample_execution'),
        CheckConstraint('target_hours IS NULL OR target_hours > 0'),
        CheckConstraint('setup_hours >= 0 AND actual_hours > 0'),
        Index('ix_training_samples_status','status'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    review_id:Mapped[int]=mapped_column(ForeignKey('cost_reviews.id'))
    execution_id:Mapped[int]=mapped_column(ForeignKey('trial_executions.id'))
    estimate_code:Mapped[str]=mapped_column(String(40))
    # 开工/签结时冻结的工艺规格与工时依据，之后模具或估单再改不影响样本
    cavities:Mapped[int]=mapped_column(Integer)
    slides:Mapped[int]=mapped_column(Integer)
    steel:Mapped[str]=mapped_column(String(20))
    weight_kg:Mapped[float]=mapped_column(Float)
    setup_hours:Mapped[float]=mapped_column(Float)
    actual_hours:Mapped[float]=mapped_column(Float)
    target_hours:Mapped[float|None]=mapped_column(Float,nullable=True)
    status:Mapped[str]=mapped_column(String(20),default='pending')
    evidence_note:Mapped[str]=mapped_column(String(600),default='')
    evidence_confirmed_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    reason:Mapped[str]=mapped_column(String(600),default='')
    created_by:Mapped[str]=mapped_column(String(120))
    withdrawn_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    snapshot:Mapped[dict]=mapped_column(JSON)
class TrainingSampleVersion(Base):
    __tablename__='training_sample_versions'
    __table_args__=(
        UniqueConstraint('sample_id','version',name='uq_sample_version'),
        CheckConstraint('target_hours > 0 AND version >= 1'),
        Index('ix_sample_versions_state','state'),
    )
    id:Mapped[int]=mapped_column(primary_key=True)
    sample_id:Mapped[int]=mapped_column(ForeignKey('training_samples.id'))
    version:Mapped[int]=mapped_column(Integer,default=1)
    state:Mapped[str]=mapped_column(String(20),default='active')
    cavities:Mapped[int]=mapped_column(Integer)
    slides:Mapped[int]=mapped_column(Integer)
    steel:Mapped[str]=mapped_column(String(20))
    weight_kg:Mapped[float]=mapped_column(Float)
    setup_hours:Mapped[float]=mapped_column(Float)
    actual_hours:Mapped[float]=mapped_column(Float)
    target_hours:Mapped[float]=mapped_column(Float)
    change_note:Mapped[str]=mapped_column(String(600),default='')
    created_by:Mapped[str]=mapped_column(String(120))
    basis:Mapped[dict]=mapped_column(JSON)
