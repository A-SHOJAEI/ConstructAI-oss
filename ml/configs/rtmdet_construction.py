"""
MMDetection config for RTMDet fine-tuned on construction safety dataset.

Base: RTMDet-m pretrained on COCO
Target classes: person, hardhat, no_hardhat, safety_vest, no_safety_vest,
                truck, excavator, crane, forklift, scaffolding, ladder,
                guardrail, barricade
"""

_base_ = []  # In production: ['mmdet::rtmdet/rtmdet_m_8xb32-300e_coco.py']

# Construction safety classes
CLASSES = (
    "person", "hardhat", "no_hardhat", "safety_vest", "no_safety_vest",
    "truck", "excavator", "crane", "forklift", "scaffolding",
    "ladder", "guardrail", "barricade",
)
NUM_CLASSES = len(CLASSES)

# Model configuration
model = dict(
    type="RTMDet",
    backbone=dict(
        type="CSPNeXt",
        arch="P5",
        expand_ratio=0.5,
        deepen_factor=0.67,
        widen_factor=0.75,
        channel_attention=True,
        norm_cfg=dict(type="SyncBN"),
        act_cfg=dict(type="SiLU", inplace=True),
    ),
    neck=dict(
        type="CSPNeXtPAFPN",
        in_channels=[192, 384, 768],
        out_channels=192,
        num_csp_blocks=2,
        expand_ratio=0.5,
        norm_cfg=dict(type="SyncBN"),
        act_cfg=dict(type="SiLU", inplace=True),
    ),
    bbox_head=dict(
        type="RTMDetSepBNHead",
        num_classes=NUM_CLASSES,
        in_channels=192,
        stacked_convs=2,
        feat_channels=192,
        anchor_generator=dict(type="MlvlPointGenerator", offset=0, strides=[8, 16, 32]),
        bbox_coder=dict(type="DistancePointBBoxCoder"),
        loss_cls=dict(type="QualityFocalLoss", use_sigmoid=True, beta=2.0, loss_weight=1.0),
        loss_bbox=dict(type="GIoULoss", loss_weight=2.0),
    ),
)

# Dataset configuration
dataset_type = "CocoDataset"
data_root = "data/construction_safety/"

train_pipeline = [
    dict(type="LoadImageFromFile"),
    dict(type="LoadAnnotations", with_bbox=True),
    dict(type="Resize", scale=(640, 640), keep_ratio=True),
    dict(type="RandomFlip", prob=0.5),
    dict(type="Pad", size=(640, 640), pad_val=dict(img=(114, 114, 114))),
    dict(type="PackDetInputs"),
]

val_pipeline = [
    dict(type="LoadImageFromFile"),
    dict(type="Resize", scale=(640, 640), keep_ratio=True),
    dict(type="Pad", size=(640, 640), pad_val=dict(img=(114, 114, 114))),
    dict(type="LoadAnnotations", with_bbox=True),
    dict(type="PackDetInputs", meta_keys=("img_id", "img_path", "ori_shape", "img_shape", "scale_factor")),
]

train_dataloader = dict(
    batch_size=8,
    num_workers=4,
    persistent_workers=True,
    sampler=dict(type="DefaultSampler", shuffle=True),
    dataset=dict(
        type=dataset_type,
        data_root=data_root,
        ann_file="annotations/train.json",
        data_prefix=dict(img="images/train/"),
        pipeline=train_pipeline,
        metainfo=dict(classes=CLASSES),
    ),
)

val_dataloader = dict(
    batch_size=8,
    num_workers=4,
    persistent_workers=True,
    sampler=dict(type="DefaultSampler", shuffle=False),
    dataset=dict(
        type=dataset_type,
        data_root=data_root,
        ann_file="annotations/val.json",
        data_prefix=dict(img="images/val/"),
        pipeline=val_pipeline,
        metainfo=dict(classes=CLASSES),
    ),
)

# Training configuration
optim_wrapper = dict(
    type="OptimWrapper",
    optimizer=dict(type="AdamW", lr=0.001, weight_decay=0.05),
    paramwise_cfg=dict(bias_decay_mult=0, norm_decay_mult=0, bypass_duplicate=True),
)

train_cfg = dict(type="EpochBasedTrainLoop", max_epochs=50, val_interval=5)
val_cfg = dict(type="ValLoop")
val_evaluator = dict(type="CocoMetric", ann_file=data_root + "annotations/val.json", metric="bbox")

# Learning rate schedule
param_scheduler = [
    dict(type="LinearLR", start_factor=0.001, by_epoch=False, begin=0, end=500),
    dict(type="CosineAnnealingLR", begin=10, end=50, T_max=40, eta_min=1e-5, by_epoch=True),
]

# Hooks
default_hooks = dict(
    timer=dict(type="IterTimerHook"),
    logger=dict(type="LoggerHook", interval=50),
    param_scheduler=dict(type="ParamSchedulerHook"),
    checkpoint=dict(type="CheckpointHook", interval=5, max_keep_ckpts=3),
    sampler_seed=dict(type="DistSamplerSeedHook"),
)

# Runtime
default_scope = "mmdet"
env_cfg = dict(cudnn_benchmark=False, mp_cfg=dict(mp_start_method="fork", opencv_num_threads=0))
log_level = "INFO"
load_from = None
resume = False
