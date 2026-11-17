ROLES = ('admin', 'estimator', 'viewer')
ESTIMATE_STATES = ('draft', 'calculating', 'estimated', 'requested', 'committed', 'failed')
STEELS = ('P20', 'H13', 'S136')

EXECUTION_STATES = ('running', 'completed', 'settled')
REVIEW_STATES = ('draft', 'signed')

MAINTENANCE_STATES=('planned','approved','completed')
RESIN_STATES=('available','quarantine')
MATERIAL_STATES=('issued','returned')
DELIVERY_STATES=('dispatched','received','accepted','rectification','closed')

# 实绩回灌训练样本：待回灌/待补证/已入库/隔离/已撤回；样本版本：活动/已被更正取代
SAMPLE_STATES=('pending','pending_evidence','ingested','quarantined','withdrawn')
SAMPLE_VERSION_STATES=('active','superseded')
