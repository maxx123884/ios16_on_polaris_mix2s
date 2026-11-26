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

## 10. 0124：M1c 只读普查（`polarisgxm1=8`）

在 CX 上电、时钟打开、**不碰复位**的前提下，读 SMMU 的现状：

| 读什么 | 偏移 | 判读 |
|---|---|---|
| `SMMU_ID0/ID1` | `0x020/0x024` | 架构与特性，确认是 SMMU v2 |
| `SMMU_SCR0` | `0x000` | **bit0 = 翻译是否开启** |
| `SMMU_SACR` | `0x010` | 地址空间控制 |
| `SMMU_SGFSR` | `0x048` | 全局错误状态（有没有挂过） |
| `SMR0 / S2CR0` | `0x800/0xc00` | 流匹配与上下文类型，看 GPU 的流是否已映射 |
| `CB0_SCTLR/TTBR0/TCR/FSR` | `0x1000/0x1020/0x1030/0x1058` | 上下文 bank 0 是否启用、页表在哪 |

这决定了 M1c 是"在现成框架上加一张表"，还是"从零建一整套翻译"。

## 11. 实测（2026-11-26 01:34，`polarisgxm1=8`）——SMMU 是关的，M1c 因此小得多

```
m1c smmu: id0 0x48016605 id1 0x20008005 scr0 0x00200626 (translation off) sacr 0x00000000 sgfsr 0x00000000
m1c smmu: smr0 0x00000000 s2cr0 0x000200ff (type 3, cb 15) cb0_sctlr 0x00000000
m1c smmu: cb0_ttbr0 0x00000000 cb0_tcr 0x00000000 cb0_fsr 0x00000000
```

- `IDR0 = 0x48016605`、`IDR1 = 0x20008005`：ARM SMMU v2（400 系列），支持 4 KiB 粒度、两阶段翻译；
- **`SCR0` bit0 = 0：翻译关闭**，所有访问走物理地址（bypass）；
- `S2CR0 = 0x000200ff`：流 0 的 TYPE = 2（fault），CB = 0xf（无上下文）；但翻译关着时这不生效；
- 上下文 bank 0 的 `SCTLR/TTBR0/TCR/FSR` 全 0：从未被启用；
- `SGFSR = 0`：**没有发生过全局错误**（说明之前的挂死不是 SMMU fault）。

### 这改变了 M1c 的工作量

| 原来以为 | 实际 |
|---|---|
| 要在 XNU 里建 SMMU 域、页表、IOVA 分配 | **不需要**：翻译关着，GMU 用物理地址 |
| 需要 DRM/IOMMU 框架 | 不需要 |
| 只需 | 分配一块物理连续内存 → 写 HFI 队列表 → 把**物理地址**写进 `HFI_QTBL_ADDR`/`HFI_SFR_ADDR` → 再放开复位 |

这也解释了 `=7` 的挂死：`HFI_QTBL_ADDR` 与 `SFR_ADDR` 都是 0，固件起来后按 0 去找队列，
那不是我们给它的内存——它要么等一个永远不来的回应，要么踩到不该踩的地方。

### M1c 的具体步骤（下一步）

1. 在 XNU 里分配**物理连续**内存（`kmem_alloc_contig` 或静态预留），大小按 HFI 表 + 两个队列算（几 KiB）；
2. 按 `a6xx_hfi` 的布局写：
   - `hfi_queue_table_header`（version、size、队列数、write/read index 偏移）；
   - 入口 0（host→GMU 命令队列）、入口 1（GMU→host 回应队列），各带 `queue_header`
     （type、id、prio、read/write index）；
3. 写 `GMU_HFI_QTBL_INFO = 队列数`、`GMU_HFI_QTBL_ADDR = 表物理地址`、`GMU_HFI_SFR_ADDR = 共享区物理地址`；
4. **然后**才：`BOOT_CONFIG = 0x2` → 清 `SYSRESET` → 轮询 `FW_INIT_RESULT`；
5. 若 `FW_INIT_RESULT` 非 0，再读 `HFI_CTRL_STATUS` 看固件是否建立了自己的队列。

风险仍然存在：第 4 步可能再次挂死。但这次是**带着合法队列**去放核心，
与 `=7` 那次"什么都不给就放"有本质区别——而且队列与地址都可以先读回校验。

## 12. 实测（2026-11-26 02:10，`polarisgxm1=9`）——低 4 GiB 没有空闲页

```
m1c hfi: no page below 4 GiB after 64 tries           ← kalloc 连续 64 页全在高地址
m1c hfi: no page below 4 GiB (kmem_alloc_contig 0x3)  ← 0x3 = KERN_NO_SPACE
```

- `kalloc` 64 次，返回的物理地址**全部 ≥ 4 GiB**；
- 换成能指定上限的 `kmem_alloc_contig(..., high = 0xFFFFFFFF, ...)`，直接返回
  `KERN_NO_SPACE`。

**结论：XNU 在这台 6 GB 的机器上不持有 4 GiB 以下的空闲物理页。**
而 `HFI_QTBL_ADDR` 只有 32 位——这两个事实放在一起，说明"直接把物理页地址写进寄存器"这条路
在本平台上走不通，也就把 `=7` 那次挂死解释得更彻底了：不只是"地址是 0"，而是**任何高地址都会被截断**。

## 13. M1c 剩下的两条路

| 方案 | 代价 | 说明 |
|---|---|---|
| **启用 GPU 的 SMMU 翻译** | 大 | 给 GMU 一个 32 位 IOVA 映射到高物理页。厂商一直这么做——SMMU 存在的意义就是这个。需要建页表、开 `SMMUEN`、配流匹配 |
| 找低地址 carveout | 中 | 若 ADT 里有 GPU 预留区且落在 4 GiB 以下，可以直接用它当 HFI 内存 |

先做**只读**的 ADT 普查（找 `memory-reserved`/carveout 节点）比直接写 SMMU 便宜得多，
明天应该先做这一步：如果存在低地址 carveout，M1c 立刻回到"只差写表"；如果不存在，
就必须做 SMMU，那是另一个量级的工作。

## 14. 实测（2026-11-26 02:22，`polarisgxm1=10`）——DRAM 本身从 4 GiB 开始

```
m1c where: dram 0x100000000 size 0x7cc00000 (4 GiB ceiling 0x100000000)
m1c where: GPU-SQE      0x101ffc000 +0x7cec  above
m1c where: GPU-GMU      0x102004000 +0x8000  above
m1c where: GPU-ZAP-*    0x10200c000..        above
m1c where: zap 0x8c415000  splash 0x9d400000
```

**`dram-base = 0x1_00000000`**：这台机器的普通内存**从 4 GiB 开始**，低 4 GiB 根本不属于 DRAM。
这解释了 `kalloc`/`kmem_alloc_contig` 的失败——不是运气，是物理布局。

低 4 GiB 里确实有两块地：ZAP carveout（`0x8c415000`）和 splash 帧缓冲（`0x9d400000`），
但都是已占用的用途，借来放队列等于毁掉 ZAP 固件或显示输出，不能这么做。

## 15. 结论：M1c 必须做 SMMU，没有捷径

| 事实 | 后果 |
|---|---|
| `HFI_QTBL_ADDR` 是 32 位 | GMU 只能看到 4 GiB 以内的地址 |
| DRAM 从 4 GiB 开始 | 没有任何物理页能落在这个范围 |
| 低 4 GiB 只有 zap/splash | 借不了 |

所以 **IOVA 是唯一可行的地址形式，而 IOVA 只能由 SMMU 提供**。这不是"实现细节"，
而是这个平台上 GPU 能工作的前提条件——厂商给 GPU 配 IOMMU 就是为了这个。

### M1c 的工作量（估）

1. 在 XNU 里建一个 SMMU 域：页表（4 KiB 粒度）、`TTBR0/TCR`、开 `CB0_SCTLR`；
2. 把 HFI 页（高物理地址）映射到一个低 IOVA（例如 `0x60000000` 一带）；
3. `SMR0/S2CR0` 让 GPU 的 stream id 走这个域，`SCR0` 开 `SMMUEN`；
4. 再把 IOVA 写进 `HFI_QTBL_ADDR`，然后才放开 `SYSRESET`。

第 1 步就需要 XNU 侧的内存管理协作（页表内存本身也要物理连续、且被 pmap 记录），
不是几个寄存器的事。**这是一个独立项目，与今晚所有工作同量级或更大。**

## 16. 0130 未启动：一次需要复盘的失败（2026-11-26 03:05）

`0130`（zap 太小则回退到 splash 尾部）**内核根本没跑起来**：

| 实验 | 镜像大小 | 结果 |
|---|---|---|
| `=10`（`0128`，对照） | 18,714,624 | ✅ 启动，ring 更新（`flush #2`，blob 0x101ffc000） |
| `=11`（`0129`，zap 版） | 18,731,008 | ✅ 启动，打印 `carveout too small 0x2000` |
| `=11`（`0130`，splash 回退） | 18,731,008 | ❌ **ring 无任何更新**（与对照同一次 flush 计数） |

同尺寸、同镜像结构，一个跑一个不跑 → **不是大小问题，是 `0130` 的代码或它触发的路径**。
`fastboot boot` 仍然返回 OKAY（这台设备的静默特性）。

### 明天第一件要判定的事

用二分法在这两种可能之间定位，成本都很低：

1. **`ml_io_map` 映射 splash（`0x9d400000`）失败或触发早期 fault** ——
   把 `ml_io_map` 那行换成"只打印地址、不映射"，如果内核能跑，就是它；
2. **`polaris_gpu_read_handoff` 在 `zap_hfi` 路径里被调用两次**（`=10` 的 where 已经调过一次）——
   某些 ADT 读取可能不可重入/有副作用，改成复用已有结果即可。

### 教训

`=10` 到 `=11` 之间我跳过了"先跑一次只打印、不访问"的中间步。这个项目的纪律一直是
**一次只加一个动作**，这次图快跳了一步，结果是一次无法归因的挂起——代价比多做一步更高。

## 17. 二分结果（2026-11-26 03:20，`0131`）：死因是 `ml_io_map(splash 尾页)`

```
m1c where: zap 0x8c415000 splash 0x9d400000
m1c zap: carveout only 0x2000, falling back to splash
m1c zap: using splash 0x9d400000 +0x2400000, table at 0x9f7ff000
m1c zap: would map 0x9f7ff000, not mapping yet
```

只把映射换成一个 printf，内核就正常跑起来了。结论明确：

| 事实 | 含义 |
|---|---|
| splash 区 = `0x9d400000 + 0x2400000`（36 MB） | 表地址 `0x9f7ff000` 在 4 GiB 以下，符合 32 位要求 |
| 不映射 → 一切正常 | 内核、`m1c where`、串口输出都健康 |
| `ml_io_map(0x9f7ff000)` → 整机死 | **帧缓冲最后一页并未被映射**：ADT 声明 36 MB，实际 backing 更小 |

教训：ADT 的 `size` 是"预留范围"，不等于"已映射范围"。拿它的尾部当内存，
必须先验证那一页真的可访问——而这正是这次跳过的一步。

### 下一步（`0132`）

在 splash 的**起始页**（`0x9d400000`，确定被映射，因为屏幕正在显示）放表，并且：

1. 先把那 4 KiB 快照下来；
2. 写表 → 读寄存器 → 回读；
3. **把快照写回去**，屏幕上只闪一下左上角 4 KiB。

这条路径不碰未映射页，也不会留下显示残影。

## 18. 0133/0134：表放对了，放开复位仍然挂（2026-11-26 21:29）

**0133 成功**——shim 在关 MMU 状态下把表写到 `0x9DCE6000`（可见帧缓冲最后一页），
内核只写寄存器、零映射：

```
m1c hfi: table 0x9dce6000 seeded by the shim, not mapped here
m1c hfi: regs after info 0x00000001 tbl 0x9dce6000 sfr 0x9dce6078 (tbl matches)
```

这是本移植第一次把队列表放到 GMU 能寻址的地址上，且代价为零。

**0134 仍然挂**（`polarisgxm1=12`，先备表再放复位）：

```
m1b boot before: sysreset 0x00000001 bootcfg 0x00000000
m1b boot write bootcfg 0x00000002
m1b boot release reset (sysreset 0x00000000)
        ← 之后无输出，ring 里 launchd 行为 0，确认整机挂死
```

判断依据：这次 ring 只有 8306 字节、`launchd` 零行；正常启动会有大量 launchd 输出。
用户按键进 fastboot 是**硬复位**救回来的，不是自然启动。

## 19. 下一步：把"挂"当作判据，逐项隔离

既然挂得可复现，就可以当 oracle 用。三个单变量实验，每个只要一次上机：

| 步骤 | 只做什么 | 若存活说明 |
|---|---|---|
| `=13` | 只写 `BOOT_CONFIG = 2`，**不碰复位** | 写 bootcfg 本身安全，问题在复位后取指 |
| `=14` | 写 `BOOT_CONFIG = 0`，再放开复位 | 0x2 这个值可能是错的 |
| `=15` | 只清 `SYSRESET`，不写 bootcfg | 顺序/依赖关系 |

还有一个必须先排除的未知：**shim 写进帧缓冲的那 4 KiB，内容是否真的是我们以为的那样**。
内核读不到低地址，但可以反过来做——让 shim 把同一份表**复制一份到内存映射的 blob 区**，
内核读出来打印，逐字比对。`015` 应该先加这个校验，否则后面每个结论都建立在
"表内容正确"这个未验证的假设上。

## 20. 0135/0136 单变量隔离：**`BOOT_CONFIG = 0x2` 才是挂死的原因**（2026-11-26 21:42）

| 实验 | 做了什么 | 结果 |
|---|---|---|
| `0135`（`=13`） | 只写 `BOOT_CONFIG=2`，不碰复位 | ✅ 存活，系统正常启动（launchd 72 行） |
| `0136`（`=14`） | 写 `BOOT_CONFIG=0`，再清 `SYSRESET` | ✅ **存活**，系统继续启动 |

`=14` 的 ring 是同一次启动内两次调用：

```
143: cfg-only after: sysreset 0x1 bootcfg 0x2: survived the write, reset untouched
144: boot before:    sysreset 0x1 bootcfg 0x2
145: write bootcfg 0x0
146: release reset (sysreset 0x0)
148: libignition: hello: commence primary ignition        ← 活着
```

`m1a clocks: cx on` 在整份 ring 里只出现 **1 次**，所以这是**一次启动**，不是重启后的残留。

### 结论

之前两次挂死（`=7`、`=12`）的真正原因不是"没有队列"、也不是"地址超 32 位"——
**是 `BOOT_CONFIG = 0x2`**。在这个 GMU 上，`0x2` 大概表示"从非 ITCM 的源启动"，
CM3 取指到不存在的空间，把整机拖死。写 `0` 才是对的。

### 下一步（`=15`）：复位后第一次读寄存器

`=14` 里 `boot after` 那行没有打出来，取而代之的是一整行空格——说明**清复位之后的第一批
寄存器读**还有问题（可能读 `FW_INIT_RESULT` 时 GMU 状态未稳）。

`015` 改成：放复位 → 先打一行标记 → 只读 `FW_INIT_RESULT` 一次 → 再打结果。
这样能区分"读本身出事"还是"固件没起来"。
