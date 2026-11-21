# 三台参考机型 + HackiOS 的 GPU 驱动可移植性核对（2026-11-21）

问题：MIX 3、K20 Pro、小米 15 的 GPU 驱动能不能参考/移植到 Polaris（MIX 2S，SDM845）的 XNU？

**一句话结论：只有 MIX 3 能提供"本机可直接用的东西"（固件 + 同类寄存器事实），
另两台只能提供方法论；而三台的驱动本体一行都不能进 XNU——因为它们是 Linux 内核驱动，
不是 XNU 驱动。** 下面把"能搬/不能搬"逐条落到出处。

## 0 结论速览

| | MIX 3（perseus） | K20 Pro（raphael） | 小米 15（dada） | HackiOS（A642L / SC7280） |
|---|---|---|---|---|
| SoC | SDM845 | SM8150 | SM8750 | SC7280 |
| GPU | Adreno 630（**与 Polaroid 同款**） | Adreno 640 | Adreno 830 | Adreno 642L（A660 系） |
| 内核侧驱动 | kgsl `adreno_a6xx.c` | kgsl `adreno_a6xx.c` | kgsl（a8xx 分支） | **XNU** `redwood_adreno.c` |
| 用户态 | freedreno/turnip（Mesa） | 同左 | 同左 | Mesa（msm DRM 子集） |
| 对 Polaris 的价值 | **固件可直接用**、序列一一对应 | 同代架构对照，常量/固件不同 | 只有方法论（GMU/HFI 换代） | **唯一同层参考**：XNU 侧阶段机与撤销结构 |
| 能搬进 XNU 的 | 固件二进制、寄存器事实 | 仅"同代行为一致性"校验 | 无 | 结构设计（APSL 2.0，仍需重写） |

## 1 先把三台的驱动栈说清楚（这是"不能直接移植"的根因）

三台机器跑的都是 **Android/Linux**，它们的"GPU 驱动"是两层：

- 内核：Qualcomm **kgsl**（`drivers/gpu/msm/adreno_a6xx.c`、`kgsl_gmu.c`）；
- 用户态：**freedreno/turnip**（Mesa），把 GL/Vulkan 编译成 Adreno ISA。

XNU 里这两层**都不存在**：没有 Linux DRM 框架、没有 dma-buf、没有 `iommu_ops`/`dma_map`
那套 SMMU 抽象、没有 `regulator`/`clk` 框架，也没有 request_firmware 的固件加载器。
所以"把 kgsl 的 `adreno_a6xx.c` 移过来"不是移植，是**重写**：要自己实现上电、
SMMU 建表、GMU 装载、HFI 邮箱、CP 环缓冲这一整套，且每一层都得自己写超时与回滚。

反过来，HackiOS 的 `redwood_adreno.c` 是**同一层**（XNU on Android 硬件）的实现，
所以它才是"别人怎么做的"真正答案——但它做的是 A660L/SC7280，常量、固件格式、SMMU
流编号全部不同。上游 `hackios-gpu-porting.md` 已核对过：**流程可借，常量不可借**。

## 2 能搬的三样东西

### 2.1 固件二进制（只有 MIX 3 能用）

MIX 3 与 MIX 2S 同为 SDM845 + A630v2，vendor 分区里的 `a630_sqe.fw`、`a630_gmu.bin`、
`a630_zap.{mdt,b00,b01,b02}` 是**同一组文件**（同芯片型号、同固件包；逐字节相同属预期，
但需要取到实物后算 SHA-256 才能宣称）。这正是 `tools/polaris_gpu_expect_table.py` 缺的输入。

K20 Pro 是 A640，固件名为 `a640_*`，装不进 A630；小米 15 是 a8xx，完全不同。

### 2.2 硬件事实（三台都能给，但必须带出处、只记数值）

kgsl 的 A630 代码把"哪一步写哪个寄存器"写得很实：TCM_BOOT 逐字写 ITCM、GMU 启动轮询
`0xBABEFACE`、`CP_ME_INIT` 的 ordinal 掩码、`CP_PROTECT_CNTL=3`、SMMU 保护范围按 0x2000
分段等。这些是**硬件契约**，可以据此重写 XNU 代码；但**不能把 GPL-2.0 的 C 代码贴进
APSL 的 XNU 树**（上游纪律，也是本项目的红线）。记录格式：数值 + 源码链接 + 观测条件。

### 2.3 阶段机结构（HackiOS，唯一同层参考）

`redwood_adreno.c` 的 22 阶段 + 逆序撤销，把"CX 上电 → 熔丝读型号 → 时钟实测 → GMU 页表
→ 固件装载 → GMU 启动 → HFI → 3D 核上电 → CP 环 → 首帧"拆成可单步、可跳过、失败即停的
序列，并用 `gmu_space_is_on / gmu_runs / gmu_talks / core_is_on / rsc_awake / cx_off`
六个布尔量记"我到底拿到了什么"。**这套所有权记账 + 逆序回滚 + 每步前置复检**是 Polaris
最该照搬的部分，它跟上游 `tools/polaris_gpu_stages.py` 里的 `BringupLedger` 是同一思路。

## 3 搬不了的四样

1. **kgsl 的 C 代码**：GPL-2.0 → APSL 2.0 不兼容，且依赖 Linux 内核设施。
2. **Mesa/turnip**：它产出 Adreno ISA。iOS 16 的界面走 Metal，Apple 的编译器只产 AGX
   指令，全世界没有 AIR→Adreno 后端。**这一层不解，GPU 再通也不会让界面变快。**
3. **别家的常量**：Redwood 的 GPU `0x03d00000`、GPUCC `0x03d90000`、SMMU `0x03da0000`、
   zap `0x8b71a000` 与本机 `0x05000000 / 0x05090000 / 0x05040000 / 0x8c415000` 毫无关系；
   A640/A830 的基址同理。
4. **别家的固件装载格式**：Redwood 的 `a660_gmu.bin` 按 16 字节 `address,size,type,value`
   块解析并额外分配内存；本机 `a630_gmu.bin` 是 32768 B 平铺镜像，前两字为栈指针
   `0x43f80` 与 Thumb 入口 `0x9`，走 TCM_BOOT 逐字写入。**改文件名是最容易犯的致命错误。**

## 4 新发现：GX 上电其实不依赖 bootloader 的 command-db

上游此前把 `command_db_owned` 当成 M0 的硬门槛（拿不到 ABL 的 ARC/BCM 命令库就不动）。
但 MiCode A630 的 `_load_gmu_rpmh_ucode()` / `a6xx_rpmh_power_on_gpu()` 显示：
**GPU 核心（GX）的上电是 GMU 自己的 RSCC/PDC 序列完成的，用的是固定 uCode**：

| 项 | 值 | 出处 |
|---|---|---|
| 关闭 SDE 时钟门控 | `GPU_RSCC_RSC_STATUS0_DRV0 = BIT(24)` | kgsl `adreno_a6xx.c` |
| PDC 从机号 | `RSCC_PDC_SLAVE_ID_DRV0 = 1` | 同上 |
| PDC 序列入口 | `PDC_SEQ_START_ADDR = 0x4520` | 同上 |
| PDC 匹配值 | `0x4510`（LO）/ `0x4514`（HI） | 同上 |
| RSC 序列 uCode（5 字） | `0xA7A506A0, 0xA1E6A6E7, 0xA2E081E1, 0xE9A982E2, 0x0020E8A8` | 同上 |
| PDC 序列 uCode（5 字） | `0xFEBEA1E1, 0xA5A4A3A2, 0x8382A6E0, 0xBCE3E284, 0x002081FC` | 同上 |
| 上电握手 | `RSCC_CONTROL_REQ = BIT(1)` → 轮询 `ACK BIT(1)`（100 ms）→ 轮询 `RSCC_SEQ_BUSY_DRV0 == 0` | 同上 |
| 使能 PDC | `0x80000001`，TCS1/TCS3 enable bank = 7 | 同上 |

**含义**：M0 可以拆成两半——

- **M0a（GX，不再被 command-db 卡住）**：GMU 起来后由 RSCC/PDC 序列给 3D 核上电，
  所需值全部在上表，可离线建模、可写代码、可在真机分步验证。
- **M0b（CX/MX，仍阻塞）**：GPU 的 CX 供电与 DDR/总线投票走 apps-RSC
  （`0x179e0000`，TCS 在 `0x179e0d00`），这部分才是真需要本机 command-db / ARC 地址的东西。
  而 CX 若已被 bootloader 开着，**在确认所有权之前既不能关也不能当作"我开的"**。

所以路线从"先拿 command-db"改成"**先证 CX 现状与归属，GX 走 PDC 固定序列**"。这是本轮
唯一实质性的路线修正，来源是别人（kgsl）的实现，正好回答了你问的"别人怎么做的"。

**必须先声明的不确定性**：上表数值来自对本机无法访问的源码的读取（经代理抓取文本后整理），
**没有逐字节对 `a6xx_reg.h` 复核**，也没有真机读回。它足够支撑"路线判断"和离线建模
（`tools/polaris_rscc_pdc.py` 已把它写成带出处、可测试的契约，17 项测试），但**不足以直接
写进 C 后端**。进硬件前必须做两件事：拿 `a6xx_reg.h` 核准每个偏移；在真机上对
`RSCC_CONTROL_REQ/ACK` 做一次只读-写-读回的隔离实验（超时即隔离，不重试）。

## 5 阶段映射（HackiOS 22 阶段 → Polaris Stage → kgsl）

| HackiOS 阶段 | Polaris `Stage` | kgsl 对应 | 本机还缺什么 |
|---|---|---|---|
| 1 外部时钟/RPMh 资源名 | `CLOCKS` | `kgsl_gmu.c` 取 CX/GMU 时钟 | 本机时钟树与所有权 |
| 2 熔丝读型号 | `BOARD` | `adreno_a6xx` gpucore 表 | QFPROM 读取接口（XNU 侧） |
| 3 GPUCC 现状（只读） | `CLOCKS` | GPUCC 读 | 无（可先做只读） |
| 4 CX 半边上电 | `RAILS` | CX GDSC + PM8998 S9 | **M0b：CX 归属** |
| 5 时钟使能/实测 | `CLOCKS` | GPUCC + debugcc | 频率计数器读数路径 |
| 6 GMU/GBIF/cx_misc 首批寄存器 | `GMU` | `a6xx_start()` 前半 | SMMU/PDC 就位 |
| 7 GMU 的 RPMh 通道 | `GMU` | `_load_gmu_rpmh_ucode()` | 上表 5+5 字序列 |
| 8 SMMU 全局 ID/stream 表 | `SMMU` | `adreno_smmu.c` | CB5–7 HYP 实测 |
| 9 SMMU 上下文 0 | `SMMU` | 同上 | 页表格式/TCU 同步 |
| 10 GMU DTCM 自检 | `GMU` | ITCM/DTCM 写读 | 无（先决：GX 有电） |
| 11 TZ 能力探测 | `PAS` | `PAS_IS_SUPPORTED` | SCM 调用通道 |
| 12 zap 签名校验 | `PAS` | `PAS_INIT_IMAGE` | zap 8 KiB carveout |
| 13 GMU 页表 + SMMU ctx1 | `SMMU` | kgsl mmu | 域分离 |
| 14 装载 GMU 固件 | `GMU` | TCM_BOOT 8192 次写 | 固件进 payload |
| 15 启动 GMU（等 `0xBABEFACE`） | `GMU` | `a6xx_gmu_start()` 10 ms | 无 |
| 16 HFI 队列/PDC | `HFI` | `HFI_CTRL_INIT` | 队列地址/中断 |
| 17 HFI 对话 + 3D 核上电 | `HFI` | perf/bw 表 + CORE_FW_START | **M0a：上表 PDC 序列** |
| 18 3D 核 SMMU + 微码 | `CP` | `a6xx_rb_start()` | 环缓冲物理页 |
| 19 装载 zap | `PAS` | `PAS_AUTH_AND_RESET` | 同 12 |
| 20 启动 CP | `CP` | `CP_SQE_CNTL=1` + `CP_ME_INIT` | 命令包构造 |
| 21 退出 secure 并写回一字 | `CP` | — | **首个可证伪判据** |
| 22 画面呈现 | （不做） | — | Metal 后端缺失，止步于此 |

## 6 诚实的边界

- 上表能让我们把"第一道门"从"等 command-db"推进到"CX 归属 + GX 固定序列"，但它**仍然
  不等于 GPU 能用**：SMMU 的 CB5–7 是否被 HYP 拦住、PAS13 是否接受我们的签名流程、
  固件是否进得了 payload，三件都没有实物验证。
- 固件只能从 **MIX 3 同款机器或 MIUI 包**里取；从 K20 Pro（A640）或小米 15（a8xx）取
  是错误的，格式与型号都不匹配。
- 本轮**没有**复制任何 GPL 代码；文档与工具里只记录数值与来源。
