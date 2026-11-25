# M1b：GMU 固件与 HFI（设计，未实现）

**性质：设计文档。** 前置是 [M1a](m1a-dipper-reference-assessment.zh-CN.md) 第 12 节的判据：
`polarisgxm1=4` 里 GMU 计数器必须在走。计数器不动，本文档一行代码都不该写。

## 1. 为什么是"装固件"而不是"上电"

M1a 已经证明 CX 域由我们开关（`cx_hw_ctrl` bit31 随 `SW_COLLAPSE` 变化）。
GMU 是 CX 域里的 Cortex-M3，它有两条内存：

| 区域 | 地址 | 说明 |
|---|---|---|
| ITCM | `GMU word 0xc00` → **`0x0506d000`** | 指令内存，固件装在这里 |
| DTCM | `GMU word 0xc02` 起 | 数据内存 |

ITCM 从 GMU 基址算 `0x1b400 - 0x1a800 = 0xc00` 个字（dipper 的换算，与 `a6xx_gmu.xml` 的
`GMU_CM3_ITCM_START` 一致）。我们要装的 `a630_gmu.bin` 是 32 KiB，正好 8192 个字。

## 2. 三步，风险递增

| 步 | 动作 | 可逆性 | 判据 |
|---|---|---|---|
| 9 | 写 2 个字进 ITCM、读回、**写回原值** | 完全可逆 | 读回一致 |
| 10 | 装完整 32 KiB、**逐字读回比对**、然后**保持 GMU 复位** | 可逆（重写旧值） | 8192 字全对 |
| 11 | 释放 M3 复位（`CM3_SYSRESET`）、设 `CM3_BOOT_CONFIG`、读 `FW_INIT_RESULT` | **不可逆** | `FW_INIT_RESULT` 变成成功值 |

第 11 步之后 GMU 就在跑固件了，这是本项目第一次把控制权交给别处的代码——
所以它必须在 9、10 都干净通过、并且有"能停"的手段之后才做。

## 3. HFI 的证据（厂商与 dipper 一致）

| 寄存器 | word | 地址 | 内容 |
|---|---|---|---|
| `GMU_HFI_CTRL_STATUS` | `0x5180` | `0x0507e600` | 队列状态 |
| `GMU_HFI_QTBL_ADDR` | `0x5185` | `0x0507e614` | 队列表地址 |
| `GMU_HFI_QTBL_INFO` | `0x5184` | `0x0507e610` | 队列表描述 |
| `GMU_HFI_SFR_ADDR` | `0x5182` | `0x0507e608` | 共享内存区 |
| `GMU_HFI_CTRL_INIT` | `0x5186` | `0x0507e618` | 初始化握手 |

HFI 需要**共享内存和地址空间**（队列表 + 两个队列），也就是 SMMU/IOVA——这是 M1c 的事，
不是 M1b 能顺手带上的。所以 M1b 的终点是"固件装好、校验通过、GMU 仍在复位"，**不发 HFI**。

## 4. 仍然缺的

- 固件装载需要 GMU 的时钟（M1a 第 4 步已开过，但那时 GMU 在复位；装载时要保持同样的时钟状态）；
- `a630_gmu.bin` 的 **PAS 签名**：厂商走 PIL 让 TZ 校验，我们直接写 ITCM 等于绕过签名。
  这在这台设备上是允许的（我们自己写的代码我们自己信任），但如果 GX 上电时 TZ 有额外检查，
  会在这里失败——**这是 M1b 之后、M1c 之前的未知数**；
- 释放复位之后如何"停"：M3 没有我们可控的 halt 位，只能靠 `CM3_SYSRESET` 拉回复位。
  这是唯一的安全阀，必须在第 11 步之前确认它有效（现在读它是 0，说明处于运行/未复位状态，
  这条要在 M1a 的日志上再确认一次）。

## 5. 与 M1c 的关系

M1c 才是"GPU 真正跑起来"：SMMU 建域、HFI 建队列、让 GMU 自己去请求 GX。
那需要 IOVA 分配、页表、以及 Mesa/freedreno 的用户态接口——**这是另一个量级**。
M1b 只回答一个问题：**这块 SoC 的 GMU 能不能被我们喂活**。

## 6. 实测（2026-11-26 00:58，`polarisgxm1=5`）——计划要改

```
m1a gmu counter 22564 then 44360 over 1 ms: RUNNING - the GMU is alive
m1b itcm was 0x2823b094 0x9f890846
m1b itcm wrote 0x5eed0001 0x5eed0002, read 0x5eed0001 0x5eed0002: writable and readable
m1b itcm restored 0x2823b094 0x9f890846: as found
m1a clocks undone: cx hw_ctrl 0x00005ff4 memnoc 0x80004220 snoc 0x80000000 cxo 0x80000000 gmu 0x80004ff0
```

**ITCM 不是空的**：`0x2823b094 0x9f890846` 是真实的指令字，不是未初始化值，也不是 0。
这说明 **XBL 在交付前已经把 `a630_gmu.bin` 装进 GMU 的指令内存**，并且计数器在跑。

于是本文档第 2 节的"装 32 KiB 固件"这一步**可能整步都不需要**：

| 原计划 | 现在 |
|---|---|
| 第 9 步：写 2 字验证可写 | ✅ 已验证（可写、可读、还原精确） |
| 第 10 步：装 32 KiB 并逐字比对 | ❓ **可能不需要**——固件已在里面 |
| 第 11 步：释放复位看 `FW_INIT_RESULT` | ← **这才是下一步** |

### 下一步（`polarisgxm1=6`，纯只读）

在 CX 上电 + 时钟打开的状态下，读三组寄存器问它"活到哪一步"：

| 寄存器 | word | 地址 | 判读 |
|---|---|---|---|
| `GMU_CM3_FW_INIT_RESULT` | `0x501c` | `0x0507e070` | 固件自检结果，非 0 才算起来 |
| `GMU_CM3_BOOT_CONFIG` | `0x5001` | `0x0507e004` | 启动配置（位含义按 a6xx_gmu） |
| `GMU_HFI_CTRL_STATUS` | `0x5180` | `0x0507e600` | HFI 队列是否就绪 |
| `GMU_HFI_QTBL_INFO/ADDR` | `0x5184/0x5185` | `0x0507e610/14` | 队列表是否已由固件写好 |
| `GMU_CM3_SYSRESET` | `0x5000` | `0x0507e000` | 复位状态（读 0 的含义要按 a6xx 定义确认） |

**如果是 XBL 装好并已运行固件**，那么 `FW_INIT_RESULT` 很可能是成功值、`HFI_CTRL_STATUS` 可能有非零位——
那就意味着我们离"M1c 建 HFI 队列 + 让 GMU 请求 GX"只差地址空间（SMMU/IOVA）这一层。
如果全是 0，则说明固件装进去了但从未启动（等我们释放复位），回到原计划第 11 步。

## 7. 实测（2026-11-26 01:10，`polarisgxm1=6`）——固件在，但从未运行

```
m1b CM3_SYSRESET       0x00000001
m1b CM3_BOOT_CONFIG    0x00000000
m1b CM3_FW_INIT_RESULT 0x00000000
m1b HFI_SFR_ADDR       0x00000000
m1b HFI_QTBL_INFO      0x00000000
m1b HFI_QTBL_ADDR      0x00000000
m1b HFI_CTRL_STATUS    0x00000000
m1b HFI_CTRL_INIT      0x00000000
m1b verdict: fw_init_result 0x00000000 (zero), hfi_status 0x00000000 (zero)
```

- `SYSRESET` bit0 = 1：**CM3 被按在复位里**；
- `BOOT_CONFIG = 0`：启动配置没设；
- `FW_INIT_RESULT = 0`、HFI 全 0：**固件从未执行**，队列从未建立。

结合 ITCM 里有内容，图景完整了：**XBL 把固件装进 ITCM，然后把 CM3 按在复位里交给我们**。
这正是"等下一级启动"的状态，也解释了为什么之前所有读都是 0——不是坏了，是没启动。

## 8. 下一步（`polarisgxm1=7`）：放开复位

两笔写，顺序与 dipper 的启动表一致：

1. `BOOT_CONFIG(0x5001) = 0x2`；
2. `SYSRESET(0x5000) = 原值 & ~1`（放开复位）；
3. 轮询 `FW_INIT_RESULT(0x501c)`，最多 50 ms，非 0 即表示固件自检通过；
4. 读 `HFI_CTRL_STATUS(0x5180)` 看队列是否建立。

**收尾重按复位并还原 `BOOT_CONFIG`**，所以失败也不留半开状态。
最坏情况是 CM3 起来后跑飞、总线卡住——那时需要断电重启，属可接受代价。

## 9. 实测（2026-11-26 01:22，`polarisgxm1=7`）——放开复位会挂住整机

```
m1b boot before: sysreset 0x00000001 bootcfg 0x00000000
m1b boot write bootcfg 0x00000002
m1b boot release reset (sysreset 0x00000000)
        ← 之后没有任何输出，`boot after` 一行从未打印
```

清 `SYSRESET` bit0 后系统立刻失去响应，连下一行 printf 都没执行——**不是 GPU 挂了，是整机卡住**。
需要长按电源强制重启。

### 为什么

最可能是：CM3 一启动就执行 ITCM 里的固件，而固件的头几步会去访问内存
（HFI 队列、共享区），但**我们还没有为它建立任何地址空间**——GPU 的 SMMU 未配置、
没有 IOVA 映射、没有队列。固件在等一个永远不会来的回应，或者访问了一个未映射地址
把 NoC 拖住。

这与 dipper 的顺序一致：它的启动表在放手之前就设置好了 `HFI_QTBL_ADDR`/`HFI_SFR_ADDR`
（`0x60009000` / `0x6030100`）——**先给地址，再让核心跑**。我们是反过来的。

### 结论

| 能做的 | 状态 |
|---|---|
| CX 上电/掉电 | ✅ 可逆，已验证 |
| 四个时钟逐个开关 | ✅ 逐个验状态位 |
| ITCM 可写可读可还原 | ✅ |
| 只读问固件状态 | ✅ |
| **让 CM3 执行** | ❌ **必须先有 SMMU + HFI 队列，否则挂死** |

所以 M1b 到此为止，**它给出了一个明确的、可复现的边界**：不是"编不出来"，而是
"顺序不能反"。下一步（M1c）必须先做地址空间：SMMU 建域、页表、IOVA 分配、
HFI 队列表写入共享区——这一切都要在碰 `SYSRESET` 之前完成。
