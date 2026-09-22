# Routing full pipeline 架构

## 1. 在线主链路

```mermaid
flowchart LR
    CAM[Aria / Mac 摄像头<br/>约 10 FPS]
    MIC[麦克风<br/>16 kHz]

    subgraph WHEN[WHEN · 何时响应]
        GATE[SigLIP 视觉门<br/>2 FPS]
        ASR[VAD + Whisper]
        EVENT[WhenEvent]
        GATE --> EVENT
        ASR --> EVENT
    end

    subgraph PERCEPTION[持续视觉状态]
        LATEST[最新帧队列<br/>容量 1]
        YOLO[Ultralytics 检测<br/>最多 5 FPS]
        BOTSORT[BoT-SORT<br/>ID + 全局运动补偿]
        STORE[当前物体表<br/>类别 · 框 · 置信度<br/>位置 · 已出现时长]
        LATEST --> YOLO --> BOTSORT --> STORE
    end

    subgraph ROUTING[问题路由]
        Q[触发队列<br/>容量 4]
        TYPE[四路问题分类器]
        PLAN[相关物体证据筛选]
    end

    subgraph EXEC[执行]
        DIRECT[DIRECT_VISUAL]
        TEMP[TEMPORAL]
        MEMORY[MEMORY<br/>接口保留]
        KNOW[KNOWLEDGE]
        DIRECT --> CHOOSE{SMALL / LARGE}
    end

    CAM --> GATE
    CAM --> LATEST
    MIC --> ASR
    EVENT -->|TRIGGER + 当前帧| Q
    Q --> TYPE
    Q --> PLAN
    STORE --> PLAN
    TYPE --> DIRECT
    TYPE --> TEMP
    TYPE --> MEMORY
    TYPE --> KNOW
    PLAN -.辅助证据.-> DIRECT
    PLAN -.辅助证据.-> TEMP
    PLAN -.辅助证据.-> KNOW
```

## 2. 持续物体跟踪如何工作

```mermaid
flowchart LR
    FRAME[新 RGB 帧] --> LIMIT{距上次提交<br/>≥ 0.2 秒?}
    LIMIT -->|否| SKIP[跳过]
    LIMIT -->|是| SLOT[单槽队列<br/>新帧覆盖旧帧]
    SLOT --> DETECT[YOLO 检测常见物体]
    DETECT --> MATCH[BoT-SORT 跨帧匹配]
    MATCH --> GMC[背景特征估计<br/>补偿眼镜整体移动]
    GMC --> TRACK[稳定 track_id]
    TRACK --> STATE[更新当前位置、置信度<br/>首次/最后出现时间]
    STATE --> EXPIRE[超过 1.5 秒未见<br/>不再作为当前证据]
```

默认跟踪 COCO 中与眼镜场景较相关的 19 类物体，包括人、常见交通工具、包、杯瓶、
桌椅、屏幕、手机、书、猫和狗。类别、模型、FPS 和阈值都可在 `.env` 中调整；手和门
不在基础 COCO 类别中，需要以后接自定义或开放词汇检测模型。

## 3. 跟踪结果如何帮助回答

```mermaid
flowchart LR
    QUESTION[用户问题] --> TARGET{是否提到<br/>受支持物体?}
    TARGET -->|否| IMAGE[只使用原图]
    TARGET -->|是| LOOKUP[读取当前物体表]
    LOOKUP --> FOUND{找到相关 track?}
    FOUND -->|否| CAUTION[注明未稳定检测到<br/>不能据此判定不存在]
    FOUND -->|是| EVIDENCE[生成辅助证据<br/>数量 · 左中右位置<br/>置信度 · 持续时间]
    IMAGE --> MODEL[执行模型]
    CAUTION --> MODEL
    EVIDENCE --> MODEL

    MODEL --> SMALL[手机 SMALL]
    MODEL --> LARGE[云端 LARGE]
    MODEL --> MULTI[多帧 TEMPORAL]
```

检测信息只作为辅助，提示模型仍需以原图或多帧为准。这样可以帮助计数、目标定位和
跨帧状态判断，同时避免检测器漏检时直接给出错误结论。
