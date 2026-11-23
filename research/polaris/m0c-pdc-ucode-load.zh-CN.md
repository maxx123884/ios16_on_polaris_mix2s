# M0c：给 PDC 装序列（设计，未实现）

**性质：设计文档。没有代码，没有上机。** 这是本项目第一次会**改变风险等级**的一步：
M0b 只是"按门铃"，M0c 是把门后面的执行者装上——装上之后 `CONTROL_REQ` 就真的能把 GX 上电。

## 1. 为什么之前两次都没反应

| 实验 | 写了什么 | 结果 |
|---|---|---|
| M0b mode 1 | `GMU_RSCC_CONTROL_REQ` bit1 | 回读 `0x2`，ACK 21 ms 无应答 |
| M0b mode 2 | 先 `PDC_GPU_ENABLE_PDC` = `0x1`，再 REQ | PDC 回读 `0x1`，ACK 仍无应答 |

现在知道两个原因，都在厂商源码里：

1. **使能值写错了**：厂商写的是 `PDC_GPU_ENABLE_PDC = 0x80000001`（bit31 + bit0），
   我们只写了 `0x1`。bit31 大概率就是"使能"本身。
2. **使能值我们自己写错了，而且工具早就写着对的**：`tools/polaris_gmu_csr.py` 第 80 行从建立那天
   就注明 `PDC_GPU_ENABLE_PDC ... written 0x80000001`，而 M0b 的 mode 2 只 `OR` 了 `0x1`。
   `0108` 把值改成常量 `POLARIS_PDC_ENABLE_VALUE = 0x80000001`，并加了
   `tools/test_polaris_pdc_enable.py`（工具与内核常量不一致就失败）——这条漂移不能再发生。
3. **序列是空的**：`RSCC_SEQ_MEM_*`（RSC 微码 5 字）和 `PDC_GPU_SEQ_MEM_*`（PDC 微码 5 字）
   在 fastboot 路径上没有被 bootloader 写入。我们读到的 `SEQ_START=0x4520`、`MATCH_*=0x4510/0x4514`
   只是**入口地址与匹配条件**，指向一段并不存在的程序。

## 2. 来源与证据等级

| 内容 | 来源 | 等级 |
|---|---|---|
| 寄存器偏移 | `a6xx_reg.h`（MiCode `polaris-o-oss`） | 厂商内核源码，一手 |
| 写入值与顺序 | `_load_gmu_rpmh_ucode()`（`adreno_a6xx.c`） | 厂商内核源码，一手 |
| 上电/下电握手 | `a6xx_rpmh_power_on_gpu()` / `a6xx_rpmh_power_off_gpu()` | 同上 |
| 超时 | `GPU_START_TIMEOUT = 100 ms` | 同上 |
| 偏移换算自检 | `RSCC_SEQ_BUSY_DRV0` 算得 `0x0508d404` = 本机实测地址 | 与 M0a 读数一致 |

厂商代码是这颗芯片的原厂驱动，比第三方 fork 强一个等级；但仍不是寄存器手册，
所以"每个字是什么意思"仍然未知（见 §6）。

## 3. 要写的量（由厂商序列换算）

GMU 窗口（`0x0506a000`，重定基 `+ (idx - 0x1A800) * 4`）、PDC 窗口（`0x0b200000`，`+ idx * 4`）：

| 寄存器 | 索引 | 物理地址 | 值 |
|---|---|---|---|
| `GPU_RSCC_RSC_STATUS0_DRV0` | 0x23404 | 0x0508d010 | `BIT(24)`（关 SDE 门控，读-改-写） |
| `RSCC_PDC_SLAVE_ID_DRV0` | 0x2340b | 0x0508d02c | `1`（实测已是 1） |
| `RSCC_OVERRIDE_START_ADDR` | — | — | `0` |
| `RSCC_PDC_SEQ_START_ADDR` | 0x23408 | 0x0508d020 | `0x4520`（实测已是） |
| `RSCC_PDC_MATCH_VALUE_LO` | 0x23409 | 0x0508d024 | `0x4510`（实测已是） |
| `RSCC_PDC_MATCH_VALUE_HI` | 0x2340a | 0x0508d028 | `0x4514`（实测已是） |
| `RSCC_SEQ_MEM_0_DRV0 +0..4` | 0x23580 | 0x0508d600..0x0508d610 | `A7A506A0, A1E6A6E7, A2E081E1, E9A982E2, 0020E8A8` |
| `PDC_GPU_SEQ_MEM_0 +0..4` | 0xA0000 | 0x0b480000..0x0b480010 | `FEBEA1E1, A5A4A3A2, 8382A6E0, BCE3E284, 002081FC` |
| `PDC_GPU_TCS1_CONTROL` | 0x21572 | 0x0b2855c8 | `0` |
| `PDC_GPU_TCS1_CMD_ENABLE_BANK` | 0x21573 | 0x0b2855cc | `7` |
| `PDC_GPU_TCS1_CMD_WAIT_FOR_CMPL_BANK` | 0x21574 | 0x0b2855d0 | `0` |
| `PDC_GPU_TCS1_CMD0_{MSGID,ADDR,DATA}` | 0x21575/6/7 | 0x0b2855d4/d8/dc | `0x10108 / 0x30010 / 1`，`+4: 0x10108 / 0x30000 / 0`，`+8: 0x10108 / 0x30080 / 0` |
| `PDC_GPU_TCS3_*` | 0x215d6.. | 0x0b285758..0x0b28576c | 同上，但 DATA 为 `2 / 3 / 3` |
| `PDC_GPU_SEQ_START_ADDR` | 0x21148 | 0x0b284520 | `0` |
| `PDC_GPU_ENABLE_PDC` | 0x21140 | 0x0b284500 | **`0x80000001`** |

上电握手（`a6xx_rpmh_power_on_gpu`）：

```
REQ = BIT(1)  →  wmb
轮询 ACK 的 BIT(1)          100 ms
轮询 RSCC_SEQ_BUSY_DRV0 == 0 100 ms
REQ = 0
```

下电握手（`a6xx_rpmh_power_off_gpu`，非 v1 路径）：

```
REQ = 1        ← 注意是 bit0，不是 bit1
wmb
轮询 GPU_RSCC_RSC_STATUS0_DRV0 的 BIT(16)  100 ms
REQ = 0
```

**这张表是本设计最重要的产出**：它是"关得回去"的官方路径，也是 §5 判据表的执行依据。

## 4. 可逆性

这十次左右的写不能靠"改回去一位"撤销，所以：

1. **写前快照**：`RSCC_SEQ_MEM_0_DRV0 +0..4`、`PDC_GPU_SEQ_MEM_0 +0..4`、TCS1/TCS3 六个寄存器、
   `PDC_GPU_ENABLE_PDC`、`PDC_GPU_SEQ_START_ADDR` —— 全部先读出来存进日志；
2. **改一个已知量**：只改厂商表列出的那些，不碰其它位（`RSC_STATUS0` 用读-改-写）；
3. **结束后还原**：按快照逐字写回，再读一遍确认与快照相同；
4. **失败也还原**：任何超时/异常路径都要走到还原，不在 PDC 里留半套程序。

## 5. 判据

| 观察 | 结论 | 动作 |
|---|---|---|
| ACK bit1 在 100 ms 内变 1，`SEQ_BUSY` 回 0 | **序列生效、GX 上电** | 立刻走下电握手，确认 `RSC_STATUS0` bit16 变 1，再还原微码 |
| ACK 仍 0 | 微码写了但没被采纳（或使能值仍不对） | 还原并记录 `PDC_GPU_ENABLE` 读回值 |
| ACK 变 1 但 `SEQ_BUSY` 一直非 0 | 序列卡在中途 | 停止，**不**尝试下电握手（状态未定义），隔离并报告 |
| 下电握手超时（bit16 不变 1） | 能上电、关不回去 | **最坏结果**：M1 永久关闭；本设计到此为止 |

## 6. 仍然不知道的

- 那 10 个字是**微码**，语义未知：我们只是原样搬运厂商的表，不理解内容。若厂商表与本机
  A630v2 版本不匹配（`adreno_is_a630v1` 分支存在），可能行为不同。
- `PDC_GPU_ENABLE_PDC` 的 bit31 究竟是什么，没有手册；本设计按厂商原值写，不解释。
- 这些寄存器是否被 RPMh（AOSS）其它 master 共享——厂商路径假定自己独占。我们是在
  `fastboot boot` 起来的 XNU 里写，此时 Android 不在运行，但 RPMh 硬件仍在。
- `wmb()` 在 XNU 里对应什么：设计上用 `dsb sy`（aarch64）近似，M0b 已用同一做法。

## 7. 为什么值得做

做完 M0c，这条链路要么闭合（PDC 能被我们驱动），要么被证明走不通。两种结果都比现在
"写了没反应、不知道为什么"更有价值——而且 §5 的最坏结果会把 M1 直接关掉，
这也是一种明确。

## 8. 实现门槛（未满足前不写代码）

1. 用户明确同意——这一步的风险等级高于 M0b（M0b 不可能上电，M0c 可能）；
2. `0107` 之后的主机测试与 CI 全绿；
3. 还原路径先写好并通过 host 测试（顺序、快照字段、失败分支），再谈触发路径。
