# WearVQA Luna 全量运行笔记

日期：2026-09-18  
模型：`gpt-5.6-luna`  
数据：HF WearVQA train，2500 条  
配置：`image_detail=original`，`reasoning=medium`，标准模式

## 首次运行

- 成功：2485 条
- 失败：15 条，原因是 `max_output_tokens`
- `max_output_tokens`：2048
- 输入 tokens：8,113,202
- 输出 tokens：359,759
- Mean latency：3.063 秒
- Median latency：2.227 秒
- P95 latency：7.604 秒

## 重试后最终结果

15 条失败样本使用 `max_output_tokens=4096` 重试后，2500 条全部成功。

- 输入 tokens：8,223,071
- 输出 tokens：386,879
- Mean latency：3.164 秒
- Median latency：2.248 秒
- P95 latency：7.752 秒

按短上下文价格（输入 `$0.20/M`，输出 `$1.20/M`）估算，最终推理成本约 **$2.11**。不包含后续自动评分成本。

数据来源：`routing/benchmarks/results/wearvqa_luna_full_raw.jsonl`。

这里的 `latency_s` 是从图片编码开始，到同步 API 返回完整答案为止的耗时；若发生重试，也包含等待重试的时间。它不是流式输出的首字等待时间，现有结果无法反推首字延迟。

## LARGE 效果（Luna + Sol 标注）

2500 条 Luna 结果使用 `gpt-5.6-sol`、`reasoning=low` 进行语义判断。

- CORRECT：1501
- INCORRECT：987
- AMBIGUOUS：12
- 有效准确率：60.33%（不计 AMBIGUOUS）
- judge errors：0

表现最好的是 `how_to_purpose`（76.29%）；较弱的是
`object_counting`（45.36%）和 `text_simple_recognition`（49.58%）。

Sol 标注共记录约 587k input tokens 和 90k output tokens，成本约 **$4.15**；
平均标注延迟 2.14 秒，Median 1.88 秒，P95 3.94 秒。

### 按 category 的准确率

| Category                           | 有效样本数 | 准确率 |
| ---------------------------------- | ---------: | -----: |
| object_counting                    |        302 | 45.36% |
| text_simple_recognition            |        240 | 49.58% |
| next_state_prediction              |         68 | 55.88% |
| math                               |        199 | 60.30% |
| text_general_reasoning             |        215 | 60.93% |
| spatial_reasoning                  |        270 | 61.11% |
| activity_recognition               |        255 | 61.18% |
| image_general_reasoning            |        285 | 63.16% |
| image_attribute_simple_recognition |        363 | 64.19% |
| how_to_purpose                     |        291 | 76.29% |

## 第二轮：带原图重新标注（人工复核前）

第一轮的 1501 条 `CORRECT` 原样保留；只把 987 条 `INCORRECT` 和 12 条 `AMBIGUOUS`，共 **999 条**，重新送给 `gpt-5.6-sol` 判断。每次输入是原图、question、GT 和 Luna answer。设置为 `reasoning=medium`、`max_output_tokens=2048`；标签改为 `CORRECT / PARTIAL / INCORRECT / AMBIGUOUS`，其中 `PARTIAL` 也算 usable。999 条全部完成，无调用错误。

| 口径                    | CORRECT | PARTIAL | INCORRECT | AMBIGUOUS |
| ----------------------- | ------: | ------: | --------: | --------: |
| 第二轮实际重标的 999 条 |     136 |     197 |       621 |        45 |
| 与保留的 1501 条合并后  |    1637 |     197 |       621 |        45 |

当前有 **50 条待人工确认**：45 条 `AMBIGUOUS`，另有 5 条的 judge 理由指出参考答案可能与图像冲突（ID：37、4876、4899、3003、3231）。原始 judge 标签仍保留；这 50 条的 `usable` 留空。其余 **2450 条**中，`CORRECT + PARTIAL` 为 **1830 条，usable rate 74.69%**。这不是人工复核后的最终值，也不是 2500 条都经过带图重标的结果。

### 按 category 的暂定 usable rate

下表均排除待人工确认的条目。分母是各 category 当前可判定的条数。

| Category                           | 可判定条数 | Usable | Usable rate |
| ---------------------------------- | ---------: | -----: | ----------: |
| object_counting                    |        294 |    145 |      49.32% |
| text_simple_recognition            |        240 |    147 |      61.25% |
| next_state_prediction              |         68 |     49 |      72.06% |
| math                               |        192 |    142 |      73.96% |
| text_general_reasoning             |        214 |    162 |      75.70% |
| image_attribute_simple_recognition |        355 |    273 |      76.90% |
| image_general_reasoning            |        278 |    220 |      79.14% |
| activity_recognition               |        252 |    207 |      82.14% |
| spatial_reasoning                  |        269 |    231 |      85.87% |
| how_to_purpose                     |        288 |    254 |      88.19% |

第二轮记录了 **4,006,861 input tokens**、**165,818 output tokens**，按 Sol 输入 `$4/M`、输出 `$20/M` 估算约 **$19.34**。单条 judge 延迟 Mean **5.42 秒**、Median **4.33 秒**、P95 **12.32 秒**；这是完整 judge 答案返回后的耗时，不是首字延迟。两轮标注的日志费用合计约 **$23.58**：第一轮约 `$4.24`（包含 15 次失败后重试的已记录调用），第二轮约 `$19.34`。上文第一轮的 `$4.15` 只按最终成功记录计算，因此略低。

结果文件：`routing/benchmarks/results/wearvqa_luna_sol_image_aware_graded.csv` 是 2500 条的合并视图；同名 `.jsonl` 只记录本轮 999 次 Sol 调用。待复核的 50 条另见 `routing/benchmarks/results/wearvqa_luna_human_review/review.csv`，其 `human_label` 和 `human_note` 目前为空。

## 人工复核后的更新

已完成对 46 条可直接复核条目的人工标注，并写入
`routing/benchmarks/results/wearvqa_luna_human_review/review.csv`。其中：

- CORRECT：7
- PARTIAL：26
- INCORRECT：13
- AMBIGUOUS：0

PARTIAL 按约定计入 usable。ID 3003 的前后关系经人工复核仍为 INCORRECT；ID 4513 按人工意见记为 PARTIAL。

4 条参考答案冲突项也已完成人工定案：ID 37 为 CORRECT，ID 3231、4876、4899 为 INCORRECT。当前统计覆盖全部 2500 条。

### 更新后的整体结果

| Label     | 条数 |
| --------- | ---: |
| CORRECT   | 1641 |
| PARTIAL   |  223 |
| INCORRECT |  636 |
| AMBIGUOUS |    0 |

当前可判定 2500 条，其中 `CORRECT + PARTIAL = 1864`，usable rate 为 **74.56%**。

### 更新后的 category usable rate

| Category                           | 可判定条数 | Usable | Usable rate |
| ---------------------------------- | ---------: | -----: | ----------: |
| object_counting                    |        302 |    150 |      49.67% |
| text_simple_recognition            |        240 |    147 |      61.25% |
| next_state_prediction              |         68 |     49 |      72.06% |
| math                               |        199 |    146 |      73.37% |
| text_general_reasoning             |        217 |    163 |      75.12% |
| image_attribute_simple_recognition |        363 |    280 |      77.13% |
| image_general_reasoning            |        287 |    225 |      78.40% |
| activity_recognition               |        259 |    214 |      82.63% |
| spatial_reasoning                  |        274 |    233 |      85.04% |
| how_to_purpose                     |        291 |    257 |      88.32% |

当前没有剩余 AMBIGUOUS，所有 2500 条均纳入统计。

## 分类别任务改进方向

- 找错问题的指向 --> 空间推理、文字推理、文字识别存在问题
  - 需要先对物体/文字区域进行定位 --> 进行方位/空间信息的推理 --> 裁剪问题指向的区域 --> 根据局部图回答
- 图像细节不准 --> 音响文字识别、属性识别等问题
  - 需要肯定数据集的分辨率有一些确实不高，对于眼镜项目可以请求更高分辨率（router在面对识别问题自动请求高分辨率）
  - 可以通过Grounding DINO、OWL-ViT 这类能用文字提示直接检测不同类别的模型识别并确定边界，对目标进行局部放大后传入（object_counting也可以通过这个方式解决）
  - 文字和数字进行OCR复核
- 动作识别 --> 影响具体动作、用途等的判断（例如动作的混淆）
  - 动作问题需要多帧传入，识别动作模式
  - 对于常见的部分（例如人的身体部分、常见的物体）进行实时位置检测定位，传输时需要结合这些物体的位置、特征等进行分析
- 相似物体的识别 (品种混淆)
  - （想不到……）
- 空间关系理解 --> 影响spatial reasoning、next state prediction、活动/动作识别
  - 多帧！视角需要固定，识别物体在不同帧中位置的变化
  - 物体（们）的当前位置或状态需要首先确定，然后再分析关系/变化，对于预测问题需要进行reasoning或CoT
- 计算和推理（包括用途推理、属性识别这样的文/图推理，math问题可以不予考虑）
  - 事实提取需要首先进行，然后再根据事实进行推理

### 一些思路和优先任务

- 空间问题不能传裁剪图，否则会丢失参照物
  - 需要传原图+局部图，必要时OCR，正确率和延迟需要测试（可能需要高分辨率的重拍）
- 多帧与单帧在动作、下一步预测上的效果比较，决定何时出发多帧
  - 多帧流程设想：持续保留一段时间的画面，问题出现后选动作前中后能表现变化的几帧+局部图
  - 由于眼镜会移动，因此不能只看物体的坐标变化，还要区分物体动还是眼睛/摄像头动，因此建议进行常态化定位
    - 常态化定位覆盖少量常见&有用的类别，例如人、手、杯子、门、屏幕等，一些位置天生固定的物品可以作为角度变化的依据，这些物品会在画面中一起移动，从而可以判断背景/眼镜移动了多少，在判断目标物体移动的情况（有没有额外的位移）【背景特征点跟踪】
      - 眼镜是否有陀螺仪和加速度计？
      - 对于近大远小（远近物体移动距离不同），因此需要一个距离的简单估算（角速度可能更好）；微小的位移 --> delta
