# Smart AI Glasses · Routing Full Pipeline

这是清理后的 routing 工程。在线入口是 `when/run_live.py`，数据流依次经过：

```text
camera / microphone
        ↓
WHEN trigger + continuous object tracking
        ↓
4-way question router
        ↓
DIRECT_VISUAL / TEMPORAL / MEMORY / KNOWLEDGE
        ↓
SMALL phone VLM or LARGE cloud VLM
```

## 目录

- `when/`：语音意图、视觉门、Project Aria 输入和在线入口。
- `integration/`：事件队列、四路路由和最终执行。
- `routing/`：路由模型、短期视觉缓冲、云端多帧调用和手机 bridge。
- `routing/perception/`：Ultralytics + BoT-SORT 持续物体检测与跟踪。
- `routing/benchmarks/`：生成最终路由器所需的数据脚本，以及 WearVQA 最终评测结果。

## 运行

```bash
conda activate eyewhen
pip install -r when/requirements.txt
cp .env.example .env
python -m when.run_live --no-preset --negatives auto
```

`.env` 只保存在本机，不会被 Git 跟踪。物体跟踪默认开启；可用
`OBJECT_TRACKING_ENABLED=0` 临时关闭。第一次使用默认模型时，Ultralytics 会下载权重。
实时窗口默认只显示手机的框、track ID 和检测置信度；使用
`--track-overlay all` 显示全部跟踪类别，或用 `--track-overlay off` 关闭叠加层。

完整架构图见 [`when/ARCHITECTURE.md`](when/ARCHITECTURE.md)，Luna 的 2500 条
WearVQA 运行与评分记录见
[`routing/benchmarks/wearvqa_luna_run_note.md`](routing/benchmarks/wearvqa_luna_run_note.md)。
