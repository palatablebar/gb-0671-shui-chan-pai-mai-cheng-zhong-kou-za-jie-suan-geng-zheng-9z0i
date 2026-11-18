# 深度登记 水产拍卖结算

规模：中型。使用者：水产批发市场结算员。核心规则：先毛皮配对，再扣杂计价，按更正链选择唯一生效版，只支付差额。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 称重采信 | 查看并执行称重采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 货品换算 | 查看并执行货品换算 | `POST /api/conversion` | `app/modules/conversion/rules.py` | `tests/test_conversion.py` |
| M3 毛皮配对 | 查看并执行毛皮配对 | `POST /api/pairing` | `app/modules/pairing/rules.py` | `tests/test_pairing.py` |
| M4 扣杂竞价 | 查看并执行扣杂竞价 | `POST /api/auction` | `app/modules/auction/rules.py` | `tests/test_auction.py` |
| M5 更正裁决 | 查看并执行更正裁决 | `POST /api/correction` | `app/modules/correction/rules.py` | `tests/test_correction.py` |
| M6 应付对账 | 查看并执行应付对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M6b 弃标处置 | 查看弃标处置结果 | `POST /api/disposal` | `app/modules/disposal/rules.py` | `tests/test_disposal.py` |
| M7 结算归档 | 查看并执行结算归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码中文票据 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_encoding` |
| TRAP-02 | BOM表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-04 | 分段表头 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header_real` |
| TRAP-05 | 缺列记录 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bad_columns_real` |
| TRAP-07 | 空值哨兵 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_sentinel_real` |
| TRAP-08 | 前导零票号 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_leading_zero_real` |
| TRAP-14 | 重复编号不同原值 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_duplicate_conflict_real` |
| TRAP-18 | 三类空态 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_states` |
| TRAP-15 | 吨与千克混合 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_tonnes_counterexample` |
| TRAP-06 | 全角千分位 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/conversion/rules.py` | `tests/test_conversion.py::test_fullwidth_thousands` |
| TRAP-10 | 金额半分舍入 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/auction/rules.py` | `tests/test_auction.py::test_half_cent_rounding` |
| TRAP-26 | 陈旧更正分支 | 静默错算或丢失证据 | 显式归一与原因裁决 | `app/modules/correction/rules.py` | `tests/test_correction.py::test_stale_branch` |
| TRAP-31 | 弃标回报重跑重复生成处置 | 应追差额重复入账 | 按 `report_id` 幂等，同号同值只留一条 | `app/modules/disposal/rules.py` | `tests/test_disposal.py::test_same_report_rerun_single_disposal` |
| TRAP-32 | 原成交与重拍重复求和 | 应付虚增一票 | 重拍替换原成交入账，原成交仅留档 | `app/modules/disposal/rules.py`、`app/modules/reconcile/rules.py` | `tests/test_disposal.py::test_resale_not_settled_twice` |
| TRAP-33 | 重拍切换失败仍记新成交 | 丢失原成交或旧处置 | 失败尝试标记未采纳，原成交与上一笔生效重拍保留 | `app/modules/disposal/rules.py` | `tests/test_disposal.py::test_switch_failure_keeps_previous_active_resale` |
| TRAP-34 | 弃标早于复秤/扣杂更正 | 差额按旧版追认 | 按生效版重算，并保留弃标版本、金额与更正调整 | `app/modules/disposal/rules.py` | `tests/test_disposal.py::test_correction_after_abandonment_reported` |
| TRAP-35 | 重拍申报额与称重计价不符 | 直接采信申报额 | 申报额与重算相差超过0.01即不切换并留原因 | `app/modules/disposal/rules.py` | `tests/test_disposal.py::test_declared_amount_mismatch_not_switched` |

## 对账规则

输入=采纳原始行+隔离行+空行+同值重复行+重复表头。计价版本总额与生效票总额分别展示，避免把更正前后重复求和。最终应付=已付+待付，负待付为退款。

弃标票在应付对账中按 `max(原成交, 重拍成交)` 入账，重拍只是冲抵，不是第二笔成交：`重拍 + 应追 + 待追认更正 (+ 溢余) = 原成交`，因此总应付与无弃标时一致，不会重复结算。对账四账仍按原成交口径记录已付与待付，弃标退款、重拍收款与未结清应追另在处置页作为执行口径列示，二者互不混用。对账页逐票展示原成交、重拍成交、应追差额、待追认更正与入账成交额，并提供处置页入口。

## 弃标处置口径

弃标回报与重拍尝试写在 `sample_data/context.json` 的 `abandonment_reports`、`resale_attempts`，随批次上下文与输入快照持久化。处置页逐票展示原成交（含原始称重行、保留的全部成交版本）、弃标证据（回报号、时间、买家、版本、证据）、重拍结果（生效重拍、成交差额、应追差额）与全部重拍尝试。

- 幂等：同一 `report_id` 只生成一个处置；同号同值重复静默忽略，同号不同值保留首条并记 `REPORT_CONFLICT`；同一票号的后续不同回报记 `TICKET_ALREADY_DISPOSED`，不再新建处置。
- 不重复结算：只有一笔重拍成为生效重拍；后一次成交替换前一次，不累加；入账额取原成交与重拍较大者。重拍后待收现款=重拍成交+未结清应追；可退预付=已付−应追；二者与入账额互不混用。
- 应追口径：应追差额以弃标时已认成交额为限、且不超过当前成交缺口；弃标后复秤或扣杂更正抬高的部分列为待追认更正（`correction_pending`），不当作已确认债权，便于后续追认。
- 切换失败：`switch_status` 为失败/回滚的尝试标记「未采纳」，不参与结算；若此前已有生效重拍，则沿用旧处置（状态 `RESOLD_RETAINED`）；若从未成功，则原成交保留（`SWITCH_FAILED`）。
- 更正先后：弃标回报可指明版本；若之后又发生复秤/扣杂更正，差额按当前生效版重算，同时保留弃标时金额、更正差额与 `CORRECTION_AFTER_ABANDONMENT` 提示，便于追认。
- 证据保留：原始称重行、全部成交版本、弃标证据与重拍证据随批次持久化，并写入归档包 `disposals.json`。

## 已知不覆盖

弃标与重拍为现场登记回执，不连接拍卖撮合或支付渠道；处置只生成可复核的追收/退款证据。

无称重设备实时接入，采用现场导出文件。无支付执行，只生成可复核结算证据。
