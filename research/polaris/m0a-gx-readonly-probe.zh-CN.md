# M0a 只读探测：GX 的 GMU CSR 地址与判读

目标：在不写任何寄存器、不给 GPU 上电的前提下，读出 GX 当前处于什么状态，
为后续"要不要写、写什么"提供事实。本文给出**地址推导**、**探测清单**、
**判读矩阵**和**拒绝条件**，以及仍然未证的部分。

所有数字都有出处；没有出处的项在 §6 明确标为未证，不得当作可用能力。

## 1. 新增证据：厂商设备树

MIUI 12.5.1（`polaris_images_V12.5.1.0.QDGCNXM`）的 `boot.img` 内核段
（44,248,019 B）尾部追加了一份 FDT：偏移 13,894,948，`totalsize` 494,163，
version 17。解析结果（`tools/polaris_dt_regs.py`）：

| 节点 | reg-names | 窗口 | 说明 |
|---|---|---|---|
| `soc/qcom,gpucc@5090000` | — | `0x05090000 + 0x009000` | `compatible = qcom,gpucc-sdm845-v2` |
| `soc/qcom,gmu` | `kgsl_gmu_reg` | `0x0506a000 + 0x030000` | GMU CSR 窗口 |
| `soc/qcom,gmu` | `kgsl_gmu_pdc_reg` | `0x0b200000 + 0x300000` | GPU 自己的 PDC 块（未使用） |
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

| 寄存器 | a6xx_reg.h 索引 | GMU 内偏移 | 物理地址 |
|---|---|---|---|
| `GPU_RSCC_RSC_STATUS0_DRV0` | `0x23404` | `+0x23010` | `0x0508d010` |
| `RSCC_PDC_SLAVE_ID_DRV0` | `0x2340b` | `+0x2302c` | `0x0508d02c` |
| `RSCC_PDC_SEQ_START_ADDR` | `0x23408` | `+0x23020` | `0x0508d020` |
| `RSCC_PDC_MATCH_VALUE_LO` | `0x23409` | `+0x23024` | `0x0508d024` |
| `RSCC_PDC_MATCH_VALUE_HI` | `0x2340a` | `+0x23028` | `0x0508d028` |
| `PDC_GPU_ENABLE_PDC` | `0x21140` | `+0x1a500` | `0x05084500` |
| `RSCC_SEQ_BUSY_DRV0` | `0x23501` | `+0x23404` | `0x0508d404` |
| `GMU_RSCC_CONTROL_REQ` | `0x23b07` | `+0x24c1c` | `0x0508ec1c` |
| `GMU_RSCC_CONTROL_ACK` | `0x23b08` | `+0x24c20` | `0x0508ec20` |

**对既有文档的一处纠正**：这些"PDC 寄存器"不在 `0x05090000` 的 GPUCC 里，
也不在 `0x0b200000` 的 GPU PDC 块里，而在 **GMU 窗口**里。
`0x4520 / 0x4510 / 0x4514` 是**写进** `SEQ_START_ADDR` 与 `MATCH_VALUE_*`
的值（PDC 程序地址），不是寄存器偏移——两者此前在叙述里容易混。
`0x0b200000` 那个 3 MB 窗口本机 DT 里确实存在，但本驱动暂不使用，用途未证。

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

矩阵里没有"于是我们写什么"。写是 M0b 的事，且必须先满足 §6 的条件。

## 4. 拒绝条件（fail closed）

探测在以下任一情形下**不产生结果**，只产生一条失败原因：

1. 设备树窗口与代码里的常量不一致（GPUCC 不是 `sdm845-v2`、GMU 窗口大小对不上）；
2. 任一寄存器索引落在 `[0, gmu2gpu_offset)` 或越过窗口末尾；
3. 同一物理地址被两个名字占用、或地址未按字对齐；
4. 读到全 `0xFFFFFFFF`；
5. 未通过 `polarisgpuhash` 的摘要校验（固件交接未证明时不做 MMIO）。

## 5. 为什么不现在就写 C

两个理由，都是硬条件：

- **0096 还没被 CI 编译过**。现在往 `bsd/dev/` 里再加一个与 MMIO 相关的编译单元，
  等于把两处未验证的东西叠在一起，出错时无法定位。
- **§6 的未证项还缺出处**。写只读代码不需要证明"写是安全的"，但需要证明
  "读是安全的"，而这需要 PDC/RPMh 侧的时钟与电源域事实。

## 6. 仍未证（不得当作能力）

| 项 | 缺什么 |
|---|---|
| GX 关闭时读 GMU CSR 是否安全 | 需要说明 GMU 窗口的时钟由谁供给（RPMh/always-on 域），目前只有 DT 与代码结构，没有电源域出处 |
| `CONTROL_ACK` bit1 的时序语义 | 只在 `a6xx_rpmh_power_on_gpu()` 的轮询里见到用法，没有寄存器手册 |
| `RSC_STATUS0` bit24 的方向 | 名字叫 "SDE clock gating off"，但置 1 表示"关"还是"已关"未证 |
| `0x0b200000` 这个 3 MB PDC 窗口 | 本驱动不用它；它和 GMU 里的 `RSCC_PDC_*` 是什么关系未证 |
| XNU 侧该窗口是否已被占用 | 需要在 XNU 里确认 `0x0506a000` 没有被 iBoot/其他驱动映射过 |

## 7. 复现

```sh
# 从 MIUI boot.img 取设备树窗口（只读文件，不碰设备）
python tools/polaris_dt_regs.py <MIUI>/images/boot.img

# 推导并自检 GMU CSR 地址
python tools/polaris_gmu_csr.py

# 18 项主机测试
python -m unittest tools/test_polaris_dt_regs.py tools/test_polaris_gmu_csr.py
```

Bash 里 `python` 换成 `python3` 亦可；Windows 下路径用 `E:/...` 形式传给 Python。
