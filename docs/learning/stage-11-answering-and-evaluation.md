# Stage 11：引用回答、拒答与 Prompt 对照

## 当前状态

Stage 11A 的工程边界和真实 Provider 连通性已完成：

- `AnswerService` 组合 Stage 10 hybrid 检索与回答 Provider，核心业务不依赖 LangChain 对象。
- V0 是最小零样本 Prompt；V1 增加不可信上下文、引用、拒答和审批边界。
- 缺少费用日期时直接追问，无检索证据时直接拒答，两者都不调用回答模型。
- 模型只能返回 `S1/S2...` 来源编号；服务端将其映射为真实 chunk，未知编号按不可信输出拒绝。
- 引用部分提取文档时返回覆盖警告；同类制度多版本进入结果时返回版本冲突警告。
- 百炼通过 OpenAI 兼容接口接入，LangChain 只负责消息和模型适配；自动化测试使用 fake model，不联网。
- 北京地域百炼连通测试已通过。混合思考模型默认关闭思考，避免把隐藏推理 token 当作回答质量收益；虚构测试从 586 个输出 token 降到约 53–55 个。
- 正式 runner 使用每任务独立数据库会话和全局受控并发，同一 20 题对比 V0/V1，并保存状态、引用、越权来源安全、延迟和 token。

用户明确授权后，固定 20 题的 V0/V1 正式对照已经完成；20 题回答要点也已由项目负责人分批确认。原始机器报告仍保留 `pending_human_review` 字段，人工结论单独保存在 `data/evaluation/results/stage11_answer_point_review.json`，避免改写原始运行证据。

Stage 11 工程与评测闭环已经完成：8 题独立调优集已在北京地域运行 V0/V1/V2 对照并完成人工复核；V2 没有稳定优于 V1，未进入线上配置。RAGAS 0.4.3 已在原 20 题的 V1 冻结回答上完成辅助评判，并与领域硬指标和人工结论做了差异校准。

## 正式基线结果

固定条件：`qwen3.7-plus-2026-05-26`、温度 0、关闭思考、Stage 10 hybrid top-5、20 道 `reviewed` 题、全局并发 4。

| 指标 | V0 | V1 |
| --- | ---: | ---: |
| 状态准确率 | 0.95 | 0.90 |
| 引用精确率 | 0.9815 | 0.9804 |
| 引用召回率 | 0.9412 | 0.9412 |
| 禁止来源安全率 | 1.00 | 1.00 |
| 硬通过率 | 0.85 | 0.80 |
| 输入 / 输出 token | 19632 / 1507 | 22368 / 1673 |
| 平均单题端到端延迟 | 3167 ms | 2383 ms |

这组结果不支持“Prompt 越长越好”。V0 当前硬指标稍高，但 V1 包含不可信上下文等必要安全边界；结合后续独立调优集的人工作业结果，当前线上默认确定为 V1。

## 回答要点人工复核

人工 rubric 将每题分为 `full_pass`、`partial_pass` 和 `fail`：完整通过要求状态、核心结论、必要条件和引用范围都符合标准；部分通过表示核心结论基本正确但有遗漏、范围污染或轻微过度追问；失败表示状态或核心结论错误，或出现安全/权限问题。

| 人工指标 | V0 | V1 |
| --- | ---: | ---: |
| 完整通过率 | 0.20 | 0.30 |
| 加权人工分（部分通过按 0.5） | 0.575 | 0.60 |
| 未失败率 | 0.95 | 0.90 |

人工复核说明 V1 的要点覆盖略好，但它在 `TRAVEL-EVAL-008` 和 `TRAVEL-EVAL-015` 把已经可以条件式回答的问题错误转成追问；两版共同存在 `TRAVEL-EVAL-010` 多来源材料漏合并、`TRAVEL-EVAL-013` 无关展会例外污染和多题条件遗漏。这一轮自身没有充分证据宣布胜出者；最终选择还要结合后面的独立调优集，而且任何一组人工分数都不能宣传成生产准确率。

## 调优集对照与最终 Prompt 决策

固定条件仍为同一模型、温度、hybrid top-5、数据和输出结构。8 题仅用于调优，不是正式盲测。

| 指标 | V0 | V1 | V2 |
| --- | ---: | ---: | ---: |
| 自动硬通过率 | 0.625 | 0.75 | 0.75 |
| 引用召回率 | 0.9375 | 1.00 | 1.00 |
| 禁止来源安全率 | 1.00 | 1.00 | 1.00 |
| 人工完整通过 / 部分 / 失败 | 2 / 5 / 1 | 3 / 4 / 1 | 2 / 5 / 1 |
| 人工加权分 | 0.5625 | 0.625 | 0.5625 |
| 输入 / 输出 token | 8522 / 743 | 9674 / 935 | 10866 / 843 |

V2 能处理部分条件式回答和相对期限，但仍在供应商拜访题上错误追问展会条件，在普通客户培训题上引入无关展会例外，并在两道材料合并题中遗漏要点。它比 V1 增加约 `12.3%` 输入 token，却没有人工质量收益。因此：**线上保持 V1，V2 只保留为失败实验，不对原 20 题继续做无决策价值的回归调用。**

## RAGAS 辅助结果

RAGAS 只评判原 20 题中“预期回答且 V1 实际回答”的 15 题；拒答、追问和状态错误继续由领域指标判断。

| 指标 | 合并结果 | 有效样本 |
| --- | ---: | ---: |
| Faithfulness | 0.9464 | 14 / 15 |
| Factual Correctness | 0.5013 | 15 / 15 |
| Context Recall | 0.9778 | 15 / 15 |

45 个指标槽位中 44 个有效；`TRAVEL-EVAL-013` 的 Faithfulness 在一次定向重试后仍连接失败，保留缺分而不继续刷结果。RAGAS 与人工存在明显差异：`TRAVEL-EVAL-014` 的审批顺序人工判 full，RAGAS Factual Correctness 却为 0；`TRAVEL-EVAL-007` 人工判 full，RAGAS Faithfulness 仅 0.25。相反，`TRAVEL-EVAL-013` 的 RAGAS 事实分 0.7 没有表达“无关展会例外污染”的业务风险。

这正是辅助评测的价值和边界：它能提示答案完整性和上下文覆盖，但不能替代权限、日期、版本、引用范围、拒答状态和人工业务判断。当前回答模型和 RAGAS 裁判使用同一个百炼模型，也存在同模型偏差，Stage 15 可换独立裁判做抽样验证。RAGAS 0.4.3 还需要将 `langchain-community` 锁在 0.4.1，以避开其对已删除兼容模块的已知导入问题；该依赖只属于 `evaluation` extra。当前 runner 无法取得裁判 token 和货币成本，报告明确记录为限制，不伪造成本数字。

正式失败类型：

1. `TRAVEL-EVAL-010`：top-5 已包含两份互补材料，回答只引用第一份，漏掉第二份独有的“离店日期”。
2. `TRAVEL-EVAL-013`：展会通知虽然进入 top-5，但用户没有询问展会例外；模型主动引入 720 元并产生多余引用。
3. `TRAVEL-EVAL-015`：问题已说明公司指定大型展会，模型仍把可直接给出的条件式答案标成追问。
4. V1 的 `TRAVEL-EVAL-008`：模型已回答“返岗后两个工作日”，却因为不能换算具体日历日期而过度追问。

完整原始回答、引用和全部 top-k 证据保存在 `data/evaluation/results/stage11_prompt_comparison.json`。第一次初跑的一次结构化输出失败也保存在 `stage11_prompt_comparison_preliminary.json`，没有用重跑覆盖失败事实。

## 三条主链路

```text
问题缺少 expense_date
  -> Answer Service 判断无法确定制度版本
  -> 不检索、不调用大模型
  -> needs_clarification + missing_information
```

```text
问题包含 expense_date
  -> hybrid 检索先执行知识库、日期、权限和 ready 硬过滤
  -> top-k chunks 编号为 S1/S2...
  -> V0/V1（或离线候选 V2）Prompt 装配问题、日期、版本冲突和不可信检索上下文
  -> 百炼 Provider 返回结构化 JSON
  -> Answer Service 校验引用编号只能来自本次上下文
  -> 映射真实文档/chunk/章节/页码并附加覆盖警告
  -> 返回 answered/refused/needs_clarification
```

```text
hybrid 没有召回可访问证据
  -> 不调用回答模型
  -> refused
  -> 不编造制度，也不消耗生成 token
```

## 阅读顺序

第一次进入本阶段时先读 `docs/learning/code-navigation-map.md`，用五条主链路建立全局位置感，再按下面顺序深入。

### 第一遍：回答主链路（A级）

1. `backend/tests/test_stage11_answering.py` 中缺日期、无证据和正常引用三个测试。
2. `backend/app/api/routes/answers.py` 的公共回答入口。
3. `backend/app/services/answering.py` 的 `AnswerService`。

只要求讲清：入口收到什么、何时不调用模型、检索结果怎样变成引用、失败怎样返回。无需背函数名。

### 第二遍：Prompt 与模型边界（A级/B级）

1. `backend/app/services/answer_prompts.py`
2. `backend/app/services/bailian_answer_provider.py`

重点理解：检索上下文为什么是不可信数据；LangChain 为什么只出现在 Provider；JSON 能解析为什么仍不代表回答正确。

### 第三遍：评测（B级）

1. `backend/app/evaluation/answer_metrics.py`
2. `backend/app/evaluation/stage11_baseline.py` 只读 `_evaluate_prompt` 和最终报告字段。

重点理解：一个 case 怎样把预期状态、必须引用版本和禁止引用版本变成硬指标；为什么引用命中仍需要人工检查答案要点。人工复核记录在 `data/evaluation/results/stage11_answer_point_review.json`。V2 必须来自真实失败类型，不能把冻结测试题改写成示例。领域引用/拒答指标先于 RAGAS；RAGAS 只增加辅助视角。

### 第四遍：Prompt 调优闭环（B级，可在确认新题前先看结构）

1. `data/evaluation/stage11_tuning_v0.json`：8 题已核验调优集。
2. `backend/app/services/answer_prompts.py`：V2 的四条通用决策示例。
3. `backend/app/evaluation/stage11_ragas.py`：只看 `build_ragas_samples`，其余框架调用属于 C 级。

重点理解：调优集用来修改 Prompt，原 20 题只能做回归。由于我们已经看过原 20 题失败案例，它不再是真正盲测集；最终泛化结论必须来自未参与 Prompt 设计的新留出题或 Stage 15 扩展集。

## 分级要求

- A级：回答路由、`AnswerService` 主骨架、日期/权限/证据/引用异常边界，能写伪代码或约七成骨架。
- B级：Prompt 版本、上下文装配、Provider 适配、引用与拒答指标，能读懂、解释和修改。
- C级：Pydantic Schema、LangChain 消息类、SDK 参数和评测 JSON 序列化，知道用途并能定位即可。

## 必读与可跳过

必读：

1. `backend/app/services/answering.py`
2. `backend/app/services/answer_prompts.py`
3. `backend/app/api/routes/answers.py`
4. `backend/tests/test_stage11_answering.py`

可跳过：

- `backend/app/schemas/answer.py` 的字段声明细节。
- `ChatOpenAI`、OpenAI SDK 和 HTTP 重试内部实现。
- Stage 7–10 已通过的上传、解析和索引测试。
- 真实 baseline runner 的临时导入与 JSON 输出样板。
- 并发任务、环境版本采集和临时知识库清理的实现细节。

## 当前验收问题

1. 为什么缺少费用日期和完全没有证据时不应该调用大模型？
2. 引用白名单能证明什么，不能证明什么？为什么 `S1` 存在仍不等于它支持答案？
3. 为什么 Prompt 要声明检索上下文是不可信数据？这条指令为什么仍不能替代服务端权限过滤？

## 下一步

Stage 11 工程收尾完成。按学习约定，先进行 Stage 7–11 轻量回溯：每阶段一条真实调用链、一个小范围代码或测试修改、一次故障定位；之后进入 Stage 12 Function Calling。Stage 12 必须从服务端身份上下文派生权限，不能继续让公共回答 API 固定使用 `all_employees`。真正盲测留给未参与 Prompt 设计的新留出题或 Stage 15 扩展集。
