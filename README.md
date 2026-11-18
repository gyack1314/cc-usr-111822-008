# 跨境电池合作核算

按来源版本保存业务资料，把**采购流向 → 加工地 → 产能投用 → 就业/税收/电力 → 服务地区**串成一条可核实链路，
按可核实期间给出各地贡献范围，并把尚未兑现的对外承诺单列。已出具的合作报告不可覆盖，
更正产地证明或投产日期后出具的新版本必须说明地区归属和贡献变化的来源。

## 运行

    python -m unittest discover -s tests -v
    python -m compileall -q battery_cooperation tests
    python -m battery_cooperation.cli data/example.json                      # 当前贡献与流向
    python -m battery_cooperation.cli data/example.json --report MY-Q3       # 出具不可变报告
    python -m battery_cooperation.cli data/example.json --report MY-Q3 --database reports.db
    python -m battery_cooperation.cli data/example.json --report MY-Q3 --history --database reports.db
    python -m battery_cooperation.cli data/example.json --report MY-Q3 --revision 1 --database reports.db

`--database` 使用文件库可跨多次运行累积资料版本与报告；默认内存库适合单次核算。

## 核算规则

**资料版本（evidence.py）**
- 每份资料以 `(kind, id, revision)` 唯一标识，正文哈希固定；同版本内容冲突直接拒绝。
- `at(known_at)` 只取截至该已知时间的最新版本，历史结果可重放。
- 同一身份的更新必须作为**新 revision** 提交，旧 revision 不被修改。
- 新 revision 校验不通过时不丢弃历史：沿用上一可用版本参与核算，被拒版本及原因写入 `fallback_documents`。

**业务记录（models.py）**
支持 `plant / shipment / origin_certificate / employment / tax / revenue / investment / power / service / commitment / fiscal`。
必填字段、日期、数字范围在入库后统一校验；不合规记录进入 `invalid_documents`，未知种类进入 `unsupported_documents`，
两者都不静默丢弃。

**采购流向与地区归属（lots.py）**
- 批次按 `shipment.plant` 落到加工厂，再取工厂所在国家作为加工地。
- 原产地取截至已知时间最近签发、且覆盖该批次运输日期的 `origin_certificate`；
  没有证明时保留申报值并标注 `仅申报`，有证明时标注 `已核证`，并输出证明签发日期。
- 加工或发运日期缺失、或不在报告期的批次不计入贡献，原因写入 `period_note`。
- 产地证明可晚于报告期签发以更正历史批次，是否可采用由资料收到时间决定。
- `flows` 按（原产地, 加工地, 核证状态）汇总采购流向，跨境加工链上同一批货物只在加工地计一次。

**期间（models.resolve_period）**
每条凭证必须给出 `period_start/period_end`，或用 `fiscal_year + jurisdiction` 指向已登记的 `fiscal` 财年口径。
期间不完整的记录进入 `pending_documents`，不猜、不摊到别的期间。跨期记录按与报告期的重叠天数折算。

**贡献区间（contribution.py）**
- 只有工厂已投用、期间重叠、带 `evidence_ref` 凭证的记录才计入。
- 同一工厂同类凭证期间重叠时，只取最近收到的版本，其余标注“未重复计入”，避免重复申报。
- 就业、电量、税费、收益、投资各自成桶，**不跨量纲相加**；金额另保留 `original_currency` 原币明细。
- 每个数值给出 `low/base` 区间：折算用 `factors` 系数表的 low/base 两档，就业和电量用凭证覆盖率。
- `power_coverage` 是覆盖率，与供电量分列，不相加。
- 产能按投用/关闭日期与报告期重叠天数折算。
- 税费、收益、投资不合并：合作方要的税收贡献看 `tax`，投资收益看 `revenue`/`investment`。

**承诺（commitments.py）**
承诺单列于 `commitments`，不并入已实现贡献。按承诺自身的工厂（未指明时按国家）比较：
- 目标日期晚于报告期：`未到期`，不把公告金额当成已实现，也不预扣实现值。
- 目标日期早于报告期：`需按目标期报告核对`。
- 目标日期在报告期内：`已兑现` 或 `未兑现`，`outstanding` 只给缺口。
- 金额承诺同时保留原币目标与折算基准比较值。

**折算系数（factors.py）**
输入可用 `factors.rates` 注入审计口径，键为 `口径:币种:口径`，值为 `{low, base}`。
未提供时使用占位表，并在报告 `method.factors_source/factors_configured` 中明示。
缺币种或无系数的金额不猜汇率，只保留原币并进入 `unverified_documents`。

**报告版本（reports.py）**
- 报告按名称保存为不可变版本，数据库触发器禁止 UPDATE/DELETE。
- 同名再次出具生成新 `revision`，旧版本的正文、输入快照和哈希保持不变；`known_at` 不得回退，期间不得改变。
- 新版本 `changes` 分四类说明来源：`region_changes`（逐批次加工地/原产地变化）、
  `contribution_changes`（逐地区逐口径前后值）、`plant_changes`（投产日期/状态变化）、
  `evidence_changes`（新增或更正的资料及 revision），并附 `commitment_changes`。
- 每版都固定 `snapshot`（资料种类、id、revision、哈希），报告数字可回溯到具体资料版本。

## 样例

`data/example.json` 演示：印尼采购、马来西亚加工的两批次在 Q3 计入马来西亚；
Batam 工厂 12 月才投用，其批次与产能不计入；E3 就业凭证在 10 月但缺凭证引用，不进入 Q3；
税费与收益币种不同、口径分列；四项承诺覆盖未到期、未兑现两种情况。
