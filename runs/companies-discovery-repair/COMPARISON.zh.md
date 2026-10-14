# Companies 单源复测

Haiku 5.5 / low reasoning；仅使用既有原始数据与随附文档。FNOS：修复前 **0/3**，本次 **3/3**。

| 指标 | 修复前 | 本次 | 变化 |
|---|---:|---:|---:|
| entity.abn | 389 | 389 | +0 |
| entity.acn | 389 | 389 | +0 |
| entity.date_registered | 389 | 389 | +0 |
| entity.entity_type | 386 | 389 | +3 |
| entity.legal_name | 389 | 389 | +0 |
| entity.status | 3 | 3 | +0 |

| 运行成本 | 修复前 | 本次 |
|---|---:|---:|
| 请求数 | 13 | 28 |
| 回执费用 USD | 0.0478373 | 0.1216585 |
| 用时秒 | 151.473 | 415.941 |

费用差 USD +0.0738212；费用依据为 OpenRouter 响应回执，完整性：True，不是账单。

原始记录、定位和 ID 一致：True；proposal/validation 取样哈希均相同。检查 128 个既有文件，SHA256 全部保持一致。当前运行时 `318ca052972c62fb4cb8d21614e88f1ddc5e9e2cc9d71bf81f983d5e0fc5c611`。

原计划比较生成请求：1；实际覆盖缩水反馈：2。两次保护都针对 DRGD，分别拦截第 2 和第 4 候选。初始提案及发现复核未提出 entity_type；第 3 候选首次提出 APTY/APUB/FNOS，最后选中的第 6 候选保留三类。不能把 FNOS 的恢复直接归因于一次 FNOS 拦截，也不能把本轮结果当作受控因果实验。

费用为旧轮的 **2.54 倍**，增加 **USD 0.0738212**。新增原计划比较步骤自身费用为 **USD 0.0042144**；本轮共 6 个候选、7 次 Agent 决策，另有 3 个完整但无效的 AST 响应及格式修订尝试，合计费用 **USD 0.0126466**。剩余差异涉及更多规划、规则生成和修复轮次，不能将总费用差全部归于新增比较步骤。没有超时或未知结果。

最终规则只保留有依据的 `DRGD → deregistered`，未输出证据不足的 `REGD → active`、`EXAD → in_liquidation`。其他既有字段值逐条完全一致；冻结名称规则对照 389/389 一致，仍不是人工金标或全面准确率。

这是一轮已见来源复测，不证明新源泛化、多次稳定性或因果归属。旧结果和评价标签没有进入模型输入。未做外部信息补全、源专用规则或全量重跑；审批仅为 synthetic，人工审批为 0。

证据：[逐条 FNOS、字段变化与哈希](comparison.json)、[费用回执](provider-receipts.json)、[运行摘要](summary.json)、[最终配置](real-asic-companies/artifacts/final-config.json)。
