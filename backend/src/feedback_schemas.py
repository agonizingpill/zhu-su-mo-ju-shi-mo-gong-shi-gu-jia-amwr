from pydantic import BaseModel,Field
from typing import Literal
class EvidenceInput(BaseModel):
    evidence_note:str=Field(min_length=4,max_length=600)
class EvidenceConfirm(BaseModel):
    evidence_note:str=Field(min_length=4,max_length=600)
class SampleCorrection(BaseModel):
    # 更正冻结依据（工艺规格/准备工时），目标工时由后端重算，非正拒绝；旧版保留
    cavities:int=Field(ge=1,le=64)
    slides:int=Field(ge=0,le=32)
    steel:Literal['P20','H13','S136']
    weight_kg:float=Field(gt=0,le=20000)
    setup_hours:float=Field(ge=0,le=48)
    actual_hours:float=Field(gt=0,le=168)
    change_note:str=Field(min_length=4,max_length=600)
class SampleWithdraw(BaseModel):
    reason:str=Field(min_length=4,max_length=600)
