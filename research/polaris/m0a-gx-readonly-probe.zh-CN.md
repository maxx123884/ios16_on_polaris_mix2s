# M0a 只读探测：GX 的 GMU CSR 地址与判读

目标：在不写任何寄存器、不给 GPU 上电的前提下，读出 GX 当前处于什么状态，
为后续"要不要写、写什么"提供事实。本文给出**地址推导**、**探测清单**、
**判读矩阵**和**拒绝条件**，以及仍然未证的部分。

所有数字都有出处；没有出处的项在 §7 明确标为未证，不得当作可用能力。

## 1. 新增证据：厂商设备树

MIUI 12.5.1（`polaris_images_V12.5.1.0.QDGCNXM`）的 `boot.img` 内核段
（44,248,019 B）尾部追加了一份 FDT：偏移 13,894,948，`totalsize` 494,163，
version 17。解析结果（`tools/polaris_dt_regs.py`）：

| 节点 | reg-names | 窗口 | 说明 |
|---|---|---|---|
| `soc/qcom,gpucc@5090000` | — | `0x05090000 + 0x009000` | `compatible = qcom,gpucc-sdm845-v2` |
| `soc/qcom,gmu` | `kgsl_gmu_reg` | `0x0506a000 + 0x030000` | GMU CSR 窗口 |
| `soc/qcom,gmu` | `kgsl_gmu_pdc_reg` | `0x0b200000 + 0x300000` | GPU 的 PDC 块；`PDC_GPU_*` 写这里（见 §2） |
| `reserved-memory/gpu_region@0x8c415000` | — | `0x8c415000 + 0x002000` | zap 的 8 KiB 预留（与 0090 事实一致） |

两点值得记下：

- GPUCC 的 compatible 是 **`sdm845-v2`**。非 v2 的 `qcom,gpucc-sdm845`
  寄存器布局不同，工具会把这种 DT 判为不适用，而不是"差不多能用"。
- GMU 窗口 `0x0506a000 + 0x30000` 的末尾是 `0x0509a000`，**跨过了 GPUCC 的
  起点 `0x05090000`**。厂商 DT 就是这么写的（两个子孔径在硬件上分开），
  所以工具只把它记为提示，不当错误。

## 2. 地址推导：索引 → 物理地址

厂商驱动用"字索引"寻址 GMU 寄存器，再做一次重定基：

```
kgsl_gmu.c:1155   gmu->gmu2gpu_offset = (gmu->reg_phys - device->reg_phys) >> 2
adreno.c:2914     reg = gmu->reg_virt + ((offsetwords - gmu2gpu_offset) << 2)
kgsl_device.h:570 (offsetwords >= gmu2gpu_offset) &&
                  ((offsetwords - gmu2gpu_offset) * 4 < gmu->reg_len)
```

代入本机 DT：

```
gmu2gpu_offset = (0x0506a000 - 0x05000000) >> 2 = 0x1A800
byte = (index - 0x1A800) * 4，窗口 [0x0506a000, 0x0509a000)
```

这一步不能"差不多"：用错重定基不会缺页，只会安静地读到隔壁块。
所以 `tools/polaris_gmu_csr.py` 把公式写死成推导，并对每个寄存器做窗口边界检查。

### 探测清单（全部只读）

| 寄存器 | 索引 | 所在窗口 | 窗口内偏移 | 物理地址 |
|---|---|---|---|---|
| `GPU_RSCC_RSC_STATUS0_DRV0` | `0x23404` | GMU | `+0x23010` | `0x0508d010` |
| `RSCC_PDC_SLAVE_ID_DRV0` | `0x2340b` | GMU | `+0x2302c` | `0x0508d02c` |
| `RSCC_PDC_SEQ_START_ADDR` | `0x23408` | GMU | `+0x23020` | `0x0508d020` |
| `RSCC_PDC_MATCH_VALUE_LO` | `0x23409` | GMU | `+0x23024` | `0x0508d024` |
| `RSCC_PDC_MATCH_VALUE_HI` | `0x2340a` | GMU | `+0x23028` | `0x0508d028` |
| `PDC_GPU_ENABLE_PDC` | `0x21140` | **PDC** | `+0x84500` | **`0x0b284500`** |
| `RSCC_SEQ_BUSY_DRV0` | `0x23501` | GMU | `+0x23404` | `0x0508d404` |
| `GMU_RSCC_CONTROL_REQ` | `0x23b07` | GMU | `+0x24c1c` | `0x0508ec1c` |
| `GMU_RSCC_CONTROL_ACK` | `0x23b08` | GMU | `+0x24c20` | `0x0508ec20` |

**两个窗口，两条规则，不能混用**（本文上一版把 `PDC_GPU_ENABLE_PDC` 算进
GMU 窗口，写成了 `0x05084500`，这是**错的**）：

- GMU 窗口按"索引减 `0x1A800` 再乘 4"重定基（`adreno.c:2914`）。
- PDC 窗口**完全不重定基**。厂商是独立 ioremap 这个资源、再用
  `a6xx.c:1173 _regwrite(regbase, offsetwords, v) → regbase + (offsetwords << 2)`
  直接写：`kgsl_gmu.c:921 gmu->pdc_reg_virt = devm_ioremap(res->start, ...)`，
  资源名就是 DT 里的 `kgsl_gmu_pdc_reg`。所以
  `0x0b200000 + 0x21140 * 4 = 0x0b284500`。

错用规则的后果不是缺页：`0x05084500` 落在 GMU 窗口里，是**另一个寄存器**，
读得到、写得进，但和 PDC 无关。这正是把规则做成推导＋双窗口边界检查的原因。

**另一处纠正**：`0x4520 / 0x4510 / 0x4514` 是**写进** `SEQ_START_ADDR` 与
`MATCH_VALUE_*` 的值（PDC 程序地址），不是寄存器偏移——两者此前在叙述里容易混。

## 3. 判读矩阵

按此顺序读，得到的组合直接决定下一步：

| 观察 | 含义 | 允许的动作 |
|---|---|---|
| `CONTROL_ACK` bit1 = 1 且 `SEQ_BUSY` = 0 | GX 已上电、定序器空闲 | 可继续只读（GPUCORE 状态、SMMU） |
| `CONTROL_ACK` bit1 = 0 | GX 未上电 | 只读；**不得**写 `CONTROL_REQ` |
| `SEQ_BUSY` ≠ 0 | 有人正在跑序列 | 立即停止，不再访问 |
| `SEQ_START=0x4520` 且 `MATCH_LO=0x4510` 且 `MATCH_HI=0x4514` | 厂商那套 PDC 程序仍在 | 记录；说明上电路径被走过 |
| `PDC_ENABLE` bit0 = 1 | PDC 已使能 | 记录 |
| 任一寄存器连续两次读到 `0xFFFFFFFF` | 总线/时钟没开 | 停止并报告，不要重试 |

矩阵里没有"于是我们写什么"。写是 M0b 的事，且必须先满足 §7 的条件。

## 4. 拒绝条件（fail closed）

探测在以下任一情形下**不产生结果**，只产生一条失败原因：

1. 设备树窗口与代码里的常量不一致（GPUCC 不是 `sdm845-v2`、GMU 窗口大小对不上）；
2. 任一寄存器索引落在 `[0, gmu2gpu_offset)` 或越过窗口末尾；
3. 同一物理地址被两个名字占用、或地址未按字对齐；
4. 读到全 `0xFFFFFFFF`；
5. 未通过 `polarisgpuhash` 的摘要校验（固件交接未证明时不做 MMIO）。

## 5. 第三方实现的交叉验证（外部证据，不是本机测量）

另一个 polaris fork（`SUPCDF/ios16_on_polaris_mix2s`，分支 `d5ec4cdc`）里有
一份 2659 行的 A630 上电实现 `0098-polaris-a630-gmu-bringup.patch`，包含
GPUCC、RSC/PDC、GMU 启动、HFI 与 SMMU。用它的头文件表逐条对照我们推导的地址：

| 我们的索引 | 它的写法 | 它的地址 | 我们的地址 |
|---|---|---|---|
| `0x23404` | `GMU_WORD(0x8c04)` | `0x0508d010` | `0x0508d010` |
| `0x23408` | `GMU_WORD(0x8c08)` | `0x0508d020` | `0x0508d020` |
| `0x23409` | `GMU_WORD(0x8c09)` | `0x0508d024` | `0x0508d024` |
| `0x2340a` | `GMU_WORD(0x8c0a)` | `0x0508d028` | `0x0508d028` |
| `0x2340b` | `GMU_WORD(0x8c0b)` | `0x0508d02c` | `0x0508d02c` |
| `0x23501` | `GMU_WORD(0x8d01)` | `0x0508d404` | `0x0508d404` |
| `0x23b07` | `GMU_WORD(0x9307)` | `0x0508ec1c` | `0x0508ec1c` |
| `0x23b08` | `GMU_WORD(0x9308)` | `0x0508ec20` | `0x0508ec20` |

它的注释写着"`GMU_WORD(n) = 4 * n`，n 已是厂商字索引减 `0x1a800`"，
和我们从 `kgsl_gmu.c:1155` 推出的规则一致；八条全部逐字对上。
`tools/polaris_gmu_csr.py` 把这张表做成了断言（`INDEPENDENT_WORDS`），
改坏推导会直接测失败。

**它还给了三条我们此前没有的经验观察**（来自它的提交说明与日志字符串，
属于别人的真机结果，不是我们的测量）：

- `RSC_STATUS0` bit16 在 `fastboot boot` 进入内核时**是 0**（RSC 已被 bootloader
  留在唤醒态）；强行做一次 sleep/wake 会让 `RSCC_CONTROL_ACK` 变 0、
  GMU 的 RPMh 投票被忽略——所以它只在 bit16 置位时才唤醒。
- `HOST2GMU_INTR_EN_0..3` 在 fastboot 路径上是 0，OOB 位**到不了 CM3**，
  即使 set/raw 寄存器有变化；要显式写 `0x c0ff0001` 才有 HFI 门铃。
- PDC 的 TCS 序列（`SEQ_MEM` 5 字 + TCS1/TCS3 bank）在 fastboot 路径上
  没被 bootloader 编程，需要自己写；这与 §2 里 PDC 窗口的用途互相印证。

**边界**：那份实现的 README 仍然写着"没有 XNU GPU 上电、GMU/HFI…"，
即作者自己也没有把它记为已验证完成。它的日志字符串（"GMU did not boot"、
"RSC already awake"）说明代码在真机上跑过并留下了观测，但这不等于
它已经成功上电。所以本文把它当**交叉证据**，不当**本机事实**；
我们自己的只读探测仍按 §4 的拒绝条件执行。

## 6. 为什么不现在就写 C

两个理由，都是硬条件：

- **0096 还没被 CI 编译过**。现在往 `bsd/dev/` 里再加一个与 MMIO 相关的编译单元，
  等于把两处未验证的东西叠在一起，出错时无法定位。
- **§7 的未证项还缺出处**。写只读代码不需要证明"写是安全的"，但需要证明
  "读是安全的"。§5 的第三方实现提供了**别人的**观测（它在 GX 未起时读
  `RSC_STATUS0`、`CONTROL_ACK`、`SEQ_BUSY` 并打印日志），但这不构成
  我们自己对这台机器的证据；而且那几条日志是**失败路径**打出来的，
  无法从中判断"读是否曾经踩到过没开时钟的窗口"。

## 7. 仍未证（不得当作能力）

| 项 | 缺什么 |
|---|---|
| GX 关闭时读 GMU CSR 是否安全 | 厂商代码里读发生在 GMU 时钟已给的阶段；我们自己**没有**真机读数，只有第三方实现的日志声称可行（§5） |
| `CONTROL_ACK` bit1 的时序语义 | 只在 `a6xx_rpmh_power_on_gpu()` 的轮询里见到用法，没有寄存器手册 |
| `RSC_STATUS0` bit16/bit24 的方向 | bit16「asleep」与 bit24 的方向都只有第三方实现的说法，没有寄存器手册 |
| `0x0b200000` 窗口在 XNU 侧是否可访问 | 厂商是 Linux ioremap；XNU 的 `ml_io_map` 能否映射该地址未测 |
| XNU 侧 GMU 窗口是否已被占用 | 需要在 XNU 里确认 `0x0506a000` 没有被 iBoot/其他驱动映射过 |

## 8. 复现

```sh
# 从 MIUI boot.img 取设备树窗口（只读文件，不碰设备）
python tools/polaris_dt_regs.py <MIUI>/images/boot.img

# 推导并自检 GMU CSR 地址
python tools/polaris_gmu_csr.py

# 18 项主机测试
python -m unittest tools/test_polaris_dt_regs.py tools/test_polaris_gmu_csr.py
```

Bash 里 `python` 换成 `python3` 亦可；Windows 下路径用 `E:/...` 形式传给 Python。
