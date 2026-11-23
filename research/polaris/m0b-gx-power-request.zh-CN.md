# M0b：GX 的第一个写操作（设计，未实现）

**性质：设计文档。没有代码，没有上机。** 这一段一旦落地就是本项目第一次**写**硬件寄存器，
所以先把"写什么、为什么只写这一位、什么条件下放弃"写清楚，再动代码。

## 1. 为什么第一步是 `RSCC_CONTROL_REQ`

契约（[a630v2-bringup-contract](a630v2-bringup-contract.md)）已明确：GX 经 GMU 请求，
**不能手工翻转 GDSCR 当作上电**；GMU 依赖顺序是 CX GDSC → 时钟/IRQ → 临时 DDR 票 →
RPMh/PDC/GMU ITCM → HFI → DCVS。

在我们这条路径上，前面几项都不归我们取得，也不该取得。真正属于"请求 GX 上电"的、且
**可逆且只有一位**的，是 `GMU_RSCC_CONTROL_REQ`（`0x0508ec1c`）的 bit1——它交给 PDC
定序器去执行厂商预置的上电程序。

实测前提（2026-11-23 02:08，`m0a-gx-readonly-probe` §11）：

| 条件 | 实测 | 门的要求 |
|---|---|---|
| 固件摘要 | 六项与钉住值逐条相同 | `PG_GX_WRITE_REQ` 要求 proven |
| `CONTROL_ACK` bit1 | 0（未上电） | 必须为 0 |
| `CONTROL_REQ` bit1 | 0（无人请求） | 必须为 0（`ack != req` 即拒绝） |
| `RSCC_SEQ_BUSY` | 0（定序器空闲） | 必须为 0 |
| `RSCC_PDC_SEQ_START_ADDR` | `0x4520` | 必须是 `0x4520`（厂商程序在位） |

五项全过，`pg_gx_decide()` 现在给出 `PG_GX_WRITE_REQ`。M0b 就是执行它。

## 2. 状态机（一次上机只走一遍）

```
IDLE
 └─ 快照 A：读九字（复用 0097 的探测）
 └─ 若 pg_gx_decide() != WRITE_REQ → IDLE（打印拒绝原因，结束）
SET_REQ
 └─ 写 CONTROL_REQ = 位或(现值, bit1)
ACK_WAIT（超时 T_ack = 20 ms，每 1 ms 轮询一次）
 ├─ ACK bit1 == 1 → ACK_ON
 └─ 超时        → REQ_CLEAR
ACK_ON
 └─ 快照 B：读九字（记录 SEQ_BUSY/PDC_ENABLE 的变化）
 └─ 立即 REQ_CLEAR（不保留上电状态）
REQ_CLEAR
 └─ 写 CONTROL_REQ = 位与(现值, ~bit1)
ACK_OFF_WAIT（超时 T_off = 20 ms）
 ├─ ACK bit1 == 0 → 快照 C，DONE
 └─ 超时          → QUARANTINE
```

`DONE` 的含义：**请求—应答—撤销这条链路在真机上闭合**，GX 回到我们进来时的状态。
`QUARANTINE`：不再写任何寄存器，打印当前九字，保持 CPU 显示，把状态留给下一次分析。

## 3. 为什么 20 ms 后立刻撤销

- 我们**没有**取得 GX 的时钟票、也没装 GMU 固件。让 GX 停在"电已上、时钟没票"的状态
  超过必要时间，是在替一个我们并不拥有的域做决定；契约要求"未取得资源不进入 `owned`"。
- 我们要的信息只有一条：**ACK 会不会跟着 REQ 变成 1**。这一条在毫秒级就能得到。
- 撤销同样是**一次一位的写**，并且有 ACK 回读确认——比"先上电、回头再研究怎么关"安全得多。

## 4. 明确不做的事

| 不做 | 原因 |
|---|---|
| 写 CX/GX GDSCR（`0x0509186c` / `0x0509100c`） | 契约禁止手工翻；那是别的 owner 的寄存器 |
| 投 GPUCC 时钟票 | 未取得，且不属于"请求上电" |
| 装 GMU 固件（ITCM 8192 次写） | 那是 M1，且必须先有 SMMU/IOVA |
| 碰 SMMU（`0x05040000`）、CP、HFI | 全部在 M1 之后 |
| 动显示/framebuffer | 0090 的 CPU 路径必须保持默认 |
| 保留上电状态 | 见 §3 |

## 5. 判据

| 观察 | 结论 | 下一步 |
|---|---|---|
| ACK 在 20 ms 内变 1，撤销后回 0 | PDC 请求链路可用 | M0c：加时钟票后再上一档 |
| ACK 一直 0 | REQ 写进去了但 PDC 没执行 | 查 PDC TCS 程序是否使能（`PDC_GPU_ENABLE` bit0 目前为 0） |
| ACK 变 1，撤销后不回 0 | 上电可，掉电不可（或 PDC 忽略撤销） | **停止**，隔离，先解决"关得回去" |
| 撤销后 `SEQ_BUSY` 变非 0 | 定序器仍在跑 | 立刻停手，不再访问 |

第三条是这次实验最坏的可接受结果：它意味着**我们没有关电权限**。那种情况下 M1 一律不做。

## 6. 上机计划

一次 `fastboot boot`，命令行加 `polarisgxm0b=1`（与 `polarisgxprobe=1` 分开，避免误触）。
日志走已验证路径：内核 console ring（`tools/sweet-log.py`）——0097/0101 的实测已证明
ring 能完整记录到用户态启动结束。

预期日志形状（本设计定义的唯一成功形态）：

```
POLARIS GPU: m0b pre: ack 0x00000000 req 0x00000000 busy 0 pdc 0x00000000 seq 0x00004520
POLARIS GPU: m0b set req -> 0x00000002
POLARIS GPU: m0b ack 0x00000002 after 3 ms
POLARIS GPU: m0b post: ack 0x00000002 req 0x00000002 busy 0 pdc 0x00000000
POLARIS GPU: m0b cleared req, ack 0x00000000 after 2 ms
POLARIS GPU: m0b done: request/ack/clear closed, GX back to off
```

## 7. 仍然缺的证据

- `CONTROL_ACK` bit1 的**时序语义**：只在厂商 `a6xx_rpmh_power_on_gpu()` 的轮询里见过用法，
  没有寄存器手册。本设计的 20 ms 是工程取值，不是规格值。
- PDC 的 TCS 程序在 fastboot 路径上**没被 bootloader 使能**（`PDC_GPU_ENABLE` bit0 = 0），
  但 `SEQ_START/MATCH` 已经在位。REQ 是否需要先使能 PDC，是本设计要在真机上回答的第一个问题；
  若 ACK 不动，下一版再考虑 `PDC_GPU_ENABLE` bit0（那也是**一次一位的写**）。
- 上电后 GX 的供电域是否会被 RPMh 的其他票影响——未知，不在本设计范围内。

## 8. 风险与回退

- 单次写入、单一比特、毫秒级撤销、有回读确认；失败即停，不重试。
- 唯一的持久风险是"上电成功但关不回去"，判据表第三条专门拦它。
- 回退：无需任何刷写动作——`fastboot boot` 不写分区，重启即离开这套内核；
  即使进入 `QUARANTINE`，CPU 显示与 0090 触摸路径仍按原样工作。

## 9. 已实现（0106，默认关闭）

`0106-polaris-gx-m0b-power-request.patch`（+98 行，只改 `bsd/dev/polaris_gpu.c`）：

- 独立开关 **`polarisgxm0b=1`**——探测（`polarisgxprobe=1`）永远不会引发写入，两个开关必须同时给；
- 进入条件：探测完成且 `pg_gx_decide()` 返回 `PG_GX_WRITE_REQ`，否则一行都不写；
- 写 `GMU_RSCC_CONTROL_REQ` bit1 → 每 1 ms 轮询 `CONTROL_ACK` bit1，最多 20 次；
- 无论结果如何**都撤销**（清 bit1），再等 ACK 落回 0，最多 20 ms；
- 三种收尾：`done`（请求—应答—撤销闭合）、`no acknowledge`（提示查 `PDC_GPU_ENABLE` bit0）、
  `acknowledge did not fall`（隔离，且禁止后续阶段）；
- `dsb sy` 只对 aarch64 生成（主机 x86 测试不需要，也不该假装需要）。

本地验证（不需要设备）：真头文件 `-Weverything -Werror` 编译通过；主机 gate 7 个场景、校验器
全部断言、aarch64-macos 交叉编译、哈希桩全部通过——`tools/gate/kernel.c` 为此补了 `delay()` 桩。

**注意 §7 的第一条仍在**：`PDC_GPU_ENABLE` bit0 当前是 0。若真机上 ACK 不动，日志会明确提示
那一点，那时才讨论对 `PDC_GPU_ENABLE` 做同样"一位、可逆、有回读"的写。
