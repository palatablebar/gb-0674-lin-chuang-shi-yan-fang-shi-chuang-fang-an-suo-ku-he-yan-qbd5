# 深度登记 临床试验访视窗与方案锁库核验

规模：中型。使用者：临床试验数据管理员。核心规则：按入组基准计算访视半开窗，方案修订按受试者生效时间选版；漏访不可用复检伪填。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 记录采信 | 执行并复核记录采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 方案版本 | 执行并复核方案版本 | `POST /api/protocol` | `app/modules/protocol/rules.py` | `tests/test_protocol.py` |
| M3 访视排窗 | 执行并复核访视排窗 | `POST /api/windows` | `app/modules/windows/rules.py` | `tests/test_windows.py` |
| M4 偏离复核 | 执行并复核偏离复核 | `POST /api/deviation` | `app/modules/deviation/rules.py` | `tests/test_deviation.py` |
| M5 复检裁决 | 执行并复核复检裁决 | `POST /api/retest` | `app/modules/retest/rules.py` | `tests/test_retest.py` |
| M6 锁库检查 | 执行并复核锁库检查 | `POST /api/locking` | `app/modules/locking/rules.py` | `tests/test_locking.py` |
| M7 冻结归档 | 执行并复核冻结归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |
| M8 方案更正 | 更正重锁与放行 | `POST /api/amendments`、`POST /api/amendments/{id}/promote` | `app/modules/amendment/rules.py` | `tests/test_amendment.py` |

## 方案更正重锁规则

更正只允许新增方案版本号，不得改写已生效版本（防止全量追溯否定旧版窗内结论）。按受试者生效时刻截接生效窗：生效前维持旧版结论，生效后按新版重判。决议按基批+更正内容+规则版本生成摘要，同一更正重试幂等不重复。逐受试者×访视对比基批与候选批，处置分维持、重判、新增偏离三类，每行可溯适用方案、窗口、复检结论与来源批次。复检仍不能填补漏访。放行前重算核对：候选批与基批同源、按更正重算摘要一致、差异与决议一致、闸门（行守恒、无未处理影响、影响全部处置、来源可追溯）全过，才切换正式锁库；任一失败回滚，旧版保留。基批已非当前锁库时拒绝放行。

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码检验单位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_real` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-08 | 受试者前导零 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_leading_zero_real` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_header_real` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_columns_real` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_types` |
| TRAP-07 | 缺失检验值 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/protocol/rules.py` | `tests/test_protocol.py::test_sentinel_real` |
| TRAP-25 | 个体方案生效重叠 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/protocol/rules.py` | `tests/test_protocol.py::test_overlap_assignment` |
| TRAP-15 | 检验单位换算 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/protocol/rules.py` | `tests/test_protocol.py::test_unit_conversion_real` |
| TRAP-23 | 访视窗末端 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/windows/rules.py` | `tests/test_windows.py::test_half_open_end` |
| TRAP-11 | UTC日历归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/windows/rules.py` | `tests/test_windows.py::test_utc_day_equivalence` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
