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

## 9. 已实现（0109，`polarisgxm0b=3`）

`0109-polaris-gx-m0c-ucode-load.patch`（+227 行，只改 `bsd/dev/polaris_gpu.c`）：

- `polarisgxm0b=3` 进入 M0c（`1`/`2` 仍是 M0b 的两级）；
- **先自检再动手**：表里每个地址必须在两个窗口内、4 字节对齐，否则一行都不写；
- **先快照**：36 个表项 + 5 个单独寄存器（`RSC_STATUS0`、`PDC_SLAVE_ID`、隐藏 TCS 的 addr/data、
  `OVERRIDE_START`）全部读出来；
- 按厂商顺序写：SDE 门控 → slave id → 隐藏 TCS 三组 → override → 十个微码字 + TCS1/TCS3 →
  `PDC_GPU_SEQ_START_ADDR=0` → `PDC_GPU_ENABLE_PDC=0x80000001`；
- 唤醒：`REQ=bit1`，各 100 ms 等 `ACK bit1` 与 `SEQ_BUSY==0`；
- **无论唤醒成败都走下电**：`REQ=1`（bit0）→ 等 `RSC_STATUS0` bit16 → `REQ=0`；
- **最后逐字还原**快照，并打印还原后的 `enable/ack/req/busy` 供比对。

日志形状（设计里约定的成功形态）：

```
m0c pre: ack … req … busy … status0 … enable 0x00000000 pdcseq 0x00000000
m0c loaded: enable 0x80000001 pdcseq 0xfebea1e1 rsccseq 0xa7a506a0
m0c ack 0x00000002 after N ms
m0c busy 0x00000000 after N ms
m0c sleep status0 …bit16… after N ms
m0c restored: enable 0x00000000 ack 0x00000000 req 0x00000000 busy 0x00000000
m0c done: PDC ran, GX woke and went back to sleep
```

四种收尾：`done` / `no acknowledge` / `acknowledged but sequencer still busy` /
`woke but did not sleep`。最后一种是 §5 判据表里"能上电、关不回去"的那一格，出现即停手。

本地验证（不需要设备）：`-Weverything -Werror` 真头文件编译、主机 gate 7 场景、校验器全部断言、
`PDC_GPU_ENABLE_PDC` 防漂移测试、aarch64-macos 交叉编译、哈希桩全部通过。

## 10. 第一次上机：SoC 挂死（2026-11-23 15:35 与 15:39）

`polarisgxm0b=3` 镜像两次 `fastboot boot` 后：

- console ring **一个字都没新增**（两次读到的都是同一份 115,144 字节的旧日志，`boot-args` 还是安装镜像的
  `cpus=4`）；
- 用**旧镜像（0107）**做 A/B：同一个手机、同一条链路，ring 立刻出现 916 KB 日志与 11 处 `m0b` 行；
- payload 段表逐字节比对：0107 与 0109 的段布局、`kernel_end`、`mem_size` 完全一致，只有内容不同；
- 两个内核的 SHA-256 都与 CI 产物内的 `SHA256SUMS` 一致。

**结论：0109 的代码把 SoC 卡死了**，而且卡在 console ring 第一次 flush 之前（gate 大约在 1.4 s 跑，
第一轮 flush 还没到），所以 ring 里没有任何痕迹——不是镜像没起来，是起来后立刻死。

按写入位置推断，最可能是某一笔 **PDC/AOSS 窗口的写**（`0x0b480000` 段或 TCS bank）让 AOSS 总线锁死；
RSC 窗口的写（`0x0508d6xx`）在 M0b 已证明可写。但这是推断，不是测量。

设备恢复：按住电源+音量下即可回 fastboot，之后 iOS 正常启动——**没有留下持久状态**。

## 11. 下一次上机怎么定位（0110）

`0110` 把每一次写都变成一行 kprintf：

```
POLARIS GPU: m0c write 0x0b480000 = 0xfebea1e1 (was 0x00000000)
```

kprintf 同时上屏幕（`-enable_kprintf_spam` + fbcon），所以**挂死前最后一行留在屏幕上**。
一次启动 + 一张照片就能定位到具体那一笔写，不需要二分多轮。

在那之前不再改写入内容：先知道"哪一笔会挂"，再谈"为什么挂、能不能绕开"。

## 12. 输出通道的修正（0111）

`0110` 的逐笔打印仍然可能丢：console ring 由线程按 1 秒周期落盘，而 gate 在首次落盘之前就跑完了。
所以 `0111` 把每一笔写改成三步：

```
打印 "m0c write 0x… = 0x… (was 0x…)"
delay(1.3 s)          ← 让日志线程把这一行写进 userdata
真正写寄存器
```

这样即使写操作把总线锁死，**命名那一笔的行已经在 flash 里**，读 ring 就能定位，不需要屏幕照片
（照片这条路已经证明不可靠：挂死被复位后，屏幕上是系统自己的启动日志）。

同时加了 `polarisgxm0csteps=N`：只执行前 N 笔写，用于进一步缩范围。
代价是整轮 m0c 会慢约 50 秒（40 笔 × 1.3 s），在 bring-up 阶段可以接受。

## 13. 第二次上机：序列跑通、仍未上电（2026-11-23 20:33，`0111` 节奏版）

逐笔写全部成功，回读正确；握手失败，并留下一个副作用：

```
m0c pre: ack 0x00000000 req 0x00000000 busy 0x00000000 status0 0x00000000
       enable 0x00000000 pdcseq 0x00000000            ← 微码确实是空的
m0c write 0x0508d034 = 0x00000000 (was 0x82204514)     ← 隐藏 TCS 里原本是 0x4514/0x4510/0x4520
m0c write 0x0508d600 = 0xa7a506a0 (was 0x00000000)     ← RSC 微码为空，证实 bootloader 未写
m0c write 0x0b480000 = 0xfebea1e1 (was 0x00000000)     ← PDC 微码为空
m0c write 0x0b284500 = 0x80000001 (was 0x00000000)     ← PDC 使能
m0c loaded: enable 0x80000001 pdcseq 0xfebea1e1 rsccseq 0xa7a506a0   ← 全部写入并回读一致
m0c ack 0x00000000 after 101 ms                        ← ACK bit1 仍不来
m0c sleep status0 0x01010006 after 1 ms                ← 假阳性：bit16 一开始就是 1
m0c restored: enable 0x00000000 ack 0x00000001 req 0x00000000 busy 0x00000001
m0c quarantined: ucode loaded, no acknowledge
```

### 三个新事实

1. **之前两次"挂死"是写入节奏问题**：40 笔写挤在微秒级会锁死 AOSS 总线；每笔间隔 1.3 s 后，
   同一条序列完整跑完。这是**继续做 PDC 工作的前提条件**，也是本设计没预料到的一条。
2. **窗口完全可写、值留得住**：三处微码与使能位都回读一致——不存在"PDC 窗口不可写"这回事。
3. **定序器被点着了，但 GX 没上电**：`busy` 0→1、`ack` bit0 0→1，说明请求被受理、有东西开始跑，
   而 `ack` bit1（GX 上电确认）始终为 0。同时**我们把 `RSCC_SEQ_BUSY_DRV0` 留在 1**——按 §5
   判据这属于"定序器仍在跑"，本应立即停手并隔离，当前实现只是打印了 quarantine，没有阻止还原。

### 判据的缺陷（下个补丁要修）

- `sleep status0` 必须在 **ACK 曾经升起过** 的前提下才有意义；否则 `status0` bit16 天然为 1，
  会打印出假的"下电成功"。
- `busy != 0` 之后**不应该**继续把微码清零（等于把正在跑的程序抽掉）；应停在原地报告。

### 下一步要问的问题

既然请求被受理、定序器开跑，缺的可能是 PDC 序列**执行所需的前置**（AOSS 时钟、或 `PDC_GPU_ENABLE`
的 bit31 语义之外的东西），也可能是 `REQ` 的触发方式（RSC 走的是 RPMh 命令而不是这一位）。
下一步应当是**读而不是写**：`RSCC_TCS0..3_DRV0_STATUS`（0x0508dd18 起）与 `RSC_STATUS0` 的其它位，
看定序器卡在哪个 TCS 上——读不会改变状态，风险远低于再写。

## 14. 0112：只读 TCS，并修掉两个判据缺陷

读三处 `RSCC_TCS{0,1,3}_DRV0_STATUS`（`0x0508dd18` / `0x0508e2b8` / `0x050907f8`，均由索引换算并自检），
在 **发 REQ 之前** 与 **ACK 等待之后** 各读一次：

```
m0c tcs before req: tcs0 … tcs1 … tcs3 … busy … status0 …
m0c tcs after req:  tcs0 … tcs1 … tcs3 … busy … status0 …
```

判据修正两处：

1. `sleep status0` 只在 ACK 升起过时才算数，否则打印
   `quarantined: ucode loaded, no acknowledge (sleep check meaningless)`；
2. `busy != 0` 之后**不再抽走微码**：只清 `PDC_GPU_ENABLE`（避免 PDC 处于 armed 状态），
   保留微码供下次分析，并打印
   `quarantined: sequencer still busy (0x…); ucode left loaded, PDC disabled`。

这两条是上一轮实测直接教出来的：假阳性会误导判断，抽走正在执行的程序会让状态更难解释。
