# 参考移植评估：dipper-xnu（小米 8，SDM845 + A630）

**结论先说：技术路线可移植，代码不可复制（许可不兼容），而且它正好补上了我们卡住的那一层。**

## 1. 它证明了什么

[youzjuzi/dipper-xnu](https://github.com/youzjuzi/dipper-xnu) 是**同 SoC（SDM845）、同 GPU（A630）**的 XNU 16.5 移植，
README 记录已到 iOS 桌面、**Mesa/freedreno 驱动 A630 渲染、59 FPS、GMEM tile、零拷贝 DPU scanout**，
另有 Wi-Fi/BT/音频。也就是说：**这块 GPU 在 XNU 上是可以跑起来的**，不是理论。它底子是 HackiOS（同我们这套的上游）。

关键证据不是 README，而是它的驱动代码结构——`bsd/dev/mi8_adreno.c` 的头部注释把十个步骤写得很清楚，
和我们自己的 M0a/M0b/M0c 是同一套方法论（先读后写、每步声明要碰什么、遇到意外就停）：

| 步骤 | 动作 | 对我们意味着什么 |
|---|---|---|
| 1 | 读 GCC 里 GPU 的分支时钟 | 我们先要读同一批寄存器 |
| 2 | 读 RPMh 的命令库（cmd_db，bootloader 留下的） | 我们担心的"没有 cmd_db"其实它也是**从 bootloader 留的数据里读**，不需要驱动 |
| 3 | 读 GPUCC（**仅当 `GCC_GPU_CFG_AHB_CLK` 已开**），以及两个 GDSC | 印证了"未开时钟就访问会挂总线" |
| 4 | **写 CX 电源开关**（`gpu_cx_gdsc`，VOTABLE，状态读 `gds_hw_ctrl` bit31） | **这正是我们 M1 缺的那一步** |
| 5 | 写时钟：`memnoc_gfx`、`snoc_dvm_gfx`、GMU 源（19.2 MHz 晶振）、`cxo_clk`、`cx_gmu_clk`，逐位验状态 | 可照做 |
| 6 | 读 GMU 寄存器，**AO 计数器必须在计数** | 判断"GMU 真的活了"的硬指标 |
| 7–8 | 读 SMMU（ARM SMMU v2，`0x5040000`）与上下文 bank | 在 CX 域，所以必须先做 4/5 |
| 9–10 | 往 GMU 的 ITCM 写两个字节并读回、再装 32 KB 固件读回 | 固件装载方式 |

注意第 3 步的括号：**"仅当 `GCC_GPU_CFG_AHB_CLK` 已开"**——这句话解释了为什么我们在 CX 关断时读写都"没反应"，
也说明他们的做法同样是先确认时钟再动手。

## 2. 它补上了我们的哪一格

我们的 [M1 可行性](m1-gmu-feasibility.zh-CN.md) 实测：

```
m1 gdsc cx 0x00222001 gx 0x00222001        ← bit0=1，SW_COLLAPSE，两个域都关着
m1 verdict: a GDSC is collapsed (cx bit0 1, gx bit0 1)
```

当时结论是"Cx 需要 GDSC + RPMh 投票，XNU 侧没有"。**dipper 的做法说明 CX 不一定要 RPMh 投票**：
GDSC 本身是 VOTABLE 的开关，写 `GDSCR` 清 `SW_COLLAPSE`、再轮询 `gds_hw_ctrl(0x1540)` 的 bit31，
就能把 CX 打开——上游电（PMIC）在 bootloader 阶段已经在了。

这是**改变判断的一条信息**：CX 也许能在 XNU 里自己开，GMU 因此可达，GX 再由 GMU 去请求。

## 3. 代码不能复制，只能重推

`docs/legal/GPU_LICENSE.md` 写得很清楚：

- `mi8_a630_hw_init.inc` 标 **GPL-2.0-only**（Linux 派生：180 条初始化写、105 条 HWCG、32 条 CP protect）；
- `mi8_adreno.c` `#include` 它并在内核里执行这些表；
- GNU 明确把 **APSL 2.0 列为与 GPL 不兼容**，作者自己的结论是"保留 GPL 声明满足署名，但不解决合并二进制分发"。

所以对本项目的约束是：

| 可以 | 不可以 |
|---|---|
| 读它的代码，把它当作"硬件需要什么"的证据 | 把 `mi8_adreno.c` / `mi8_a630_hw_init.inc` 复制进我们的树 |
| 按它引用的**同一批公开来源**（Linux `gdsc.c`、`gpucc-sdm845.c`、`cmd-db.c`、`a6xx_gmu.xml`）自己实现 | 以 MIT/APSL 名义重发它的表 |
| 引用它的**结论**（哪个寄存器、什么顺序、什么状态位） | 分发把 GPL 表链进 APSL 内核的合并二进制 |

这与我们项目一贯的做法一致：**只从公开来源推导，逐条写清出处**。寄存器地址和操作序列属于功能性事实，
但表本身（180 条写 + 105 条 HWCG）是它的表达，我们不搬。

## 4. 建议的移植路线（M1a）

按我们自己的步进模型做，**每步只读或一次一位，能停就停**：

| 我们的步 | 内容 | 风险 |
|---|---|---|
| `polarisgxm1=1` | 读 GCC 的 GPU 分支时钟状态 + `GCC_GPU_CFG_AHB_CLK` 是否已开 | 只读 |
| `=2` | 读 GPUCC 的 CX/GX GDSCR 与 `gds_hw_ctrl`（补上我们 M1 探针没读的那个寄存器） | 只读 |
| `=3` | **写 CX GDSCR**：设 `clk-dis-wait=8`、清 `SW_COLLAPSE`，轮询 `0x1540` bit31 最多 2 ms | **第一次写 GPUCC**，可逆（写回原值） |
| `=4` | 开 CX 半边的时钟（memnoc/snoc/GMU/cxo/gmu），每个验状态位 | 写，逐位可逆 |
| `=5` | 读 GMU 的 AO 计数器两次，看它是否在走 | 只读，**GMU 是否真活的判据** |
| `=6` | （GMU 固件装载与 HFI 仍属 M1b，先不做） | — |

前三步都是只读，第四步是"一位开关 + 回读"，第五步是关键判据。整个 M1a 不需要 RPMh 投票——
这要看 `=3` 的实际结果，如果 CX 开了但 GMU 计数器不动，就说明还缺时钟或电压票，那时再谈。

## 5. 与我们差异的地方（需要留意的）

| 项 | dipper | 我们（polaris / MIX 2S） |
|---|---|---|
| 启动链 | edk2 UEFI + xnuloader | ABL + `fastboot boot`（stock bootloader） |
| 内核版本 | 8796.121.2 | 8796.141.3（同一代，补丁可迁移） |
| GPUCC/GMU/SMMU 地址 | `0x5090000` / `0x506a000` / `0x5040000` | **相同**（我们 M1 探针已实测 GMU 窗口、契约文档记录 GPUCC/SMMU） |
| cmd_db 位置 | 从 bootloader 留的内存读 | 待确认；ABL 是否同样留下需要探测（只读） |
| 显示路径 | DPU 零拷贝 + `fb_native=2` | 我们用 CPU framebuffer（0090），不动 |

地址相同是最大的便利：**同一颗 SoC 的 GPU 子系统寄存器布局和 dipper 完全一致**，所以上表里的数字可直接用，
不像换平台那样要重新推导。

## 6. 一句话

dipper-xnu 把"能不能在 XNU 上跑 A630"从猜想变成了已实现，并且它对我们最大的价值不是代码，而是
**第 3、4 步那两句**：先确认 `GCC_GPU_CFG_AHB_CLK` 再碰 GPUCC；CX 是 VOTABLE 开关，清 `SW_COLLAPSE`
就能开，状态看 `0x1540` bit31。我们的 M1 探针读到"Cx collapsed"就停了，而它告诉我们下一步该写什么。

## 7. 已实现（0118，`polarisgxm1=1|2`）

`0118-polaris-m1a-gcc-gpucc-read.patch`（只读，两个独立开关值）：

- `polarisgxm1=1`：映射 GCC（`0x00100000`，长度 `0x1f0000`），读
  `apps_vote0 (0x52004)`、`gpu_cfg_ahb (0x71004)`、`gpu_memnoc (0x7100c)`、`gpu_snoc (0x71018)`，
  并对 `gpu_cfg_ahb` 打印 `running` / `OFF`（CBCR bit31 = 关）；
- `polarisgxm1=2`：在上一步基础上，**仅当 `gpu_cfg_ahb` 显示 running 时**才映射 GPUCC
  （`0x05090000`，`0x9000`）并读 GX/CX GDSCR（`0x100c`/`0x106c`）、`cx_gmu_cbcr`/`cxo_cbcr`
  （`0x1098`/`0x109c`）、RCG CMD/CFG（`0x1120`/`0x1124`）、**CX GDSC hardware control（`0x1540`）**、
  GX clamp（`0x1508`）。

`0x1540` 正是我们上一次 M1 探针没读的那个寄存器——它是 CX 开关真正的状态位（bit31），
dipper 的 `cx_wait()` 就是轮询它。`0x100c`/`0x106c` 与我们既有契约文档的 GDSCR 地址完全一致，
说明两边地址表是同源的。

预期输出：

```
POLARIS GPU: m1a gcc: apps_vote0 0x… cfg_ahb 0x… (running) memnoc 0x… snoc 0x…
POLARIS GPU: m1a gpucc: gx_gdscr 0x… (off) cx_gdscr 0x… (off)
POLARIS GPU: m1a gpucc: cx_gmu_cbcr 0x… cxo_cbcr 0x… rcg_cmd 0x… rcg_cfg 0x…
POLARIS GPU: m1a gpucc: cx_hw_ctrl 0x… (not on) gx_clamp 0x…
```

判读要点：若 `cfg_ahb` 是 `OFF`，说明 GPUCC 现在连时钟都没有——那 CX 开关即使清 `SW_COLLAPSE`
也开不起来（第 4 步会白写），得先解决 AHB 时钟归属，这是比 GDSC 更靠前的一层。

## 8. 实测（2026-11-24 00:56，`polarisgxm1=2`）

```
POLARIS GPU: m1a gcc: apps_vote0 0x01601d7b cfg_ahb 0x00000001 (running) memnoc 0x80004220 snoc 0x80000001
POLARIS GPU: m1a gpucc: gx_gdscr 0x00222001 (off) cx_gdscr 0x00222001 (off)
POLARIS GPU: m1a gpucc: cx_gmu_cbcr 0x80004220 cxo_cbcr 0x80000001 rcg_cmd 0x80000000 rcg_cfg 0x00000000
POLARIS GPU: m1a gpucc: cx_hw_ctrl 0x00005ff4 (not on) gx_clamp 0x00000001
```

| 观察 | 值 | 判读 |
|---|---|---|
| `gcc_gpu_cfg_ahb_clk` | `0x00000001`（bit31=0） | **在跑**：可以安全访问 GPUCC，dipper 的硬前提成立 |
| `apps_vote0` | `0x01601d7b` | bit15 = 1：GPLL0 已投给 GPU |
| `gpu_memnoc_gfx` | `0x80004220` | bit31 = 1：**分支关** |
| `gpu_snoc_dvm_gfx` | `0x80000001` | bit31 = 1：**分支关** |
| `cx_gmu_clk` | `0x80004220` | bit31 = 1：**关** |
| `cxo_clk` | `0x80000001` | bit31 = 1：**关** |
| GMU 时钟源 RCG | `0x80000000` / `0x00000000` | root 处于 `ROOT_OFF` |
| `CX GDSC hw_ctrl` | `0x00005ff4`（bit31=0） | CX 未上电，与 `cx_gdscr` bit0=1 一致 |
| `GX clamp` | `0x00000001` | 有 clamp 位在位（待第 4 步后复核） |

结论：**GPU 复合体完全停着，且没有任何异常**——时钟全关、两个域都 collapsed、AHB 时钟却是在跑的。
这正是"可以安全地开始上电"的干净起点，也是第一次可以真写 GPUCC 的时机。

注意 `m1a gpucc` 能读出来本身就是证据：dipper 的警告（未开 AHB 时钟访问会挂总线）在我们这里不成立，
因为 `cfg_ahb` 是 running。反过来说，如果它当时是 OFF，这三行读就会变成一次挂死。

## 9. 0119：拨 CX 开关（`polarisgxm1=3`）

第一次写 GPUCC，动作与 dipper 的 `step_cx_on` 一致：

```
读 cx_gdscr（快照）+ 读 cx_hw_ctrl（必须 bit31 = 0，否则"已上电，不动"）
写 cx_gdscr = (原值 & ~0xf000) | (8 << 12)          ← qcom,clk-dis-wait-val = 8
写 cx_gdscr = 上面的值 & ~1                          ← 清 SW_COLLAPSE
睡 1 µs，轮询 cx_hw_ctrl bit31，最多 2 ms（10 µs 一步）
打印 ON / STILL OFF，并把 cx_gdscr 写回快照
若曾经 ON：再轮询 bit31 是否回 0
```

两笔写之前各插 1.3 s 等日志落盘（M0c 的教训），所以这一段会慢约 5 秒。

**可逆性**：无论结果如何都写回快照；并显式检查"关得回去"。出现
`STILL ON - we own power-on but not power-off` 即按判据停手，不再做第 4 步。

预期输出：

```
m1a cx before: gdscr 0x00222001 hw_ctrl 0x00005ff4
m1a cx write gdscr 0x00228001 (clk-dis-wait 8)
m1a cx write gdscr 0x00228000 (collapse cleared)
m1a cx after:  gdscr 0x00228000 hw_ctrl 0x8……: power ON
m1a cx restore gdscr 0x00222001
m1a cx restored: hw_ctrl 0x00005ff4: switch fell again
```

## 10. 实测：CX 开得起来，也关得回去（2026-11-24 01:52，`polarisgxm1=3`）

```
POLARIS GPU: m1a cx before: gdscr 0x00222001 hw_ctrl 0x00005ff4
POLARIS GPU: m1a cx write gdscr 0x00228001 (clk-dis-wait 8)
POLARIS GPU: m1a cx write gdscr 0x00228000 (collapse cleared)
POLARIS GPU: m1a cx after: gdscr 0xf8228000 hw_ctrl 0x80001fe0: power ON
POLARIS GPU: m1a cx restore gdscr 0x00222001
POLARIS GPU: m1a cx restored: hw_ctrl 0x00005ff4: switch fell again
```

- `hw_ctrl` 从 `0x00005ff4`（bit31=0）变成 `0x80001fe0`（**bit31=1**）：CX 域上电；
- 写回快照后回到 `0x00005ff4`：**掉电**；
- GDSCR 上电后读 `0xf8228000`：bit31 是上电状态位，bit0（SW_COLLAPSE）已清。

**这是本项目第一次真正给 GPU 的某一半供上电，并且完整地撤了回来。**
`m1-gmu-feasibility` 里「CX 关着所以 GPU 不可达、到此为止」的结论据此作废——CX 不是不可达，
而是需要我们把开关打开，而这件事刚刚做成了。

## 11. 下一步：第 4 步（时钟）与第 6 步（GMU 是不是活的）

CX 有了电，但它那一半的时钟还是全关（`memnoc`/`snoc`/`cx_gmu`/`cxo` 的 CBCR bit31 都为 1）。
所以下一步是在 **CX 保持上电的窗口内**：

1. 开 `gpu_memnoc_gfx`（`GCC 0x7100c`）与 `gpu_snoc_dvm_gfx`（`0x71018`），各验 CBCR bit31 落 0；
2. 开 `cx_gmu_clk`（`GPUCC 0x1098`）与 `cxo_clk`（`0x109c`）；
3. 设 GMU 时钟源（`0x1120` RCG CMD：`UPDATE | ROOT_EN`，`0x1124` CFG 定源）；
4. **读 GMU 的 AO 电源计数器两次**，中间隔几毫秒——**如果它在走，GMU 就活着**。

判据很干净：计数器是硬件自走的，不依赖我们理解固件。它一动，M1b（装固件 + HFI）才有意义。
时钟开关和 CX 一样逐笔可逆：跑完把四个 CBCR 写回、CX 关掉，并复核。
