# M1 可行性：GMU 到底活着没有（只读）

**性质：只读探针 + 判据。没有写入。** PDC 直连上电已判死（见
[m0c §16](m0c-pdc-ucode-load.zh-CN.md)），因为没有任何东西为 `gfx.lvl` 投 RPMh 票。
换一条路之前，先回答一个更基础的问题：**GMU 现在能不能被访问、CX 域是不是开着的。**

## 1. 为什么 GMU 可能是另一条路

厂商的 `a6xx_rpmh_power_on_gpu()`（写 `CONTROL_REQ` 等 `ACK`）**是被 GMU 启动流程调用的**。
也就是说：真正的顺序是"先让 GMU 跑起来，GMU 再去向 RSC 请求 GX"。我们之前在没有任何
owner 的情况下直接按门铃，所以才没有应答。

GMU 住在 CX 域（不是 GX），而 CX 很可能已经被 bootloader 打开（设备树把 GPUCC 描述成
always-on 的父域）。若 CX 是开的、GMU 寄存器可读，那么"装 GMU 固件 + HFI"就是可行路线；
若 CX 也是关的，那 XNU 侧必须先拿 GDSC/RPMh 权限——同样是死路。

## 2. 读什么（全部只读）

| 寄存器 | 索引 | 物理地址 | 想回答的问题 |
|---|---|---|---|
| `GPU_CC_CX_GDSCR` | 0x2441b | `0x0509106c` | CX 域是否被 collapse（bit0） |
| `GPU_CC_GX_GDSCR` | 0x24403 | `0x0509100c` | GX 域是否被 collapse |
| `GMU_CM3_SYSRESET` | 0x1f800 | `0x0507e000` | M3 是否被按在复位 |
| `GMU_CM3_CFG` | 0x1f82d | `0x0507e0b4` | CFG 是否为合理值（非 0、非全 1） |
| `GMU_CX_GMU_POWER_COUNTER_ENABLE` | 0x1f840 | `0x0507e100` | 电源计数器是否被使能过 |
| `GMU_GMU2HOST_INTR_INFO` / `HOST2GMU_INTR_SET` | 0x1f992 / 0x1f994 | `0x0507e648` / `0x0507e650` | 中断通道是否安静 |
| `GMU_AO_HOST_INTERRUPT_STATUS` | 0x23b05 | `0x0508ec14` | AO 侧是否有挂起中断 |
| `GMU_AO_AHB_FENCE_CTRL` | 0x23b10 | `0x0508ec40` | AHB fence 是否关着 |

地址换算自 `a6xx_reg.h` 索引，沿用与本项目探测一致的 GMU 窗口规则。**交叉验证**：
`GX_GDSCR` 算得 `0x0509100c`，与 bring-up 契约文档记录的 `+0x100c` 一致；`CX_GDSCR` 算得
`0x0509106c`，与 `+0x106c` 一致。两处独立来源对上，说明换算没错。

## 3. 判据

| 观察 | 结论 | 下一步 |
|---|---|---|
| GDSCR 读回 `0xffffffff` | GPUCC 没人应答（未上电或未映射） | 停；XNU 侧缺 GDSC 权限 |
| 任一 GDSCR bit0 = 1 | 该域被 collapse（关着） | 若 CX 关 → 停；若只有 GX 关 → 正常，GX 本来就该关 |
| 两个 GDSCR bit0 = 0，且 GMU 寄存器非 0/非全 1 | **CX 开着、GMU 可访问** | M1a：读 GMU 版本/ITCM 尺寸，准备固件装载 |
| GDSCR 正常但 GMU 寄存器全 0 | CX 开着但 GMU 未上电 | M1a：先确认 GMU 的 GDSC/时钟归属 |

## 4. 只看不写的原因

上一轮已经证明：在没有任何 owner、没有 RPMh 投票的情况下写电源相关寄存器，要么无回应
（M0b），要么点着定序器却不产生结果（M0c）。所以这一步**故意只读**——读不会改变状态，
失败也不会留下副作用。

## 5. 上机方式

`polarisgxprobe=1 polarisgmgmu=1`（与 `polarisgxm0b` 完全独立，三个开关互不隐含）。
日志落在 console ring，按既有方式读取；输出形如：

```
POLARIS GPU: m1 gdsc cx 0x… gx 0x…
POLARIS GPU: m1 gmu sysreset 0x… cfg 0x… counter 0x…
POLARIS GPU: m1 gmu intr info 0x… set 0x… ao 0x… fence 0x…
POLARIS GPU: m1 verdict: …
```

## 6. 实测结果（2026-11-23 22:35，`0115`）

```
POLARIS GPU: m1 gdsc cx 0x00222001 gx 0x00222001
POLARIS GPU: m1 gmu sysreset 0x00000000 cfg 0x00000000 counter 0x00000000
POLARIS GPU: m1 gmu intr info 0x00000000 set 0x00000000 ao 0x00000000 fence 0x00000000
POLARIS GPU: m1 verdict: a GDSC is collapsed (cx bit0 1, gx bit0 1)
```

### 判读

- `GDSCR` bit0 = 1 是 **SW_COLLAPSE**：两个域都被软件按在关断态。值 `0x00222001` 里
  状态位 `[31:16] = 0x0022`，不是"上电完成"的样子。
- GMU 六个寄存器全读 0，且**不是** `0xffffffff`：总线有应答，只是域没电/没时钟——
  这和 CX 关断完全一致。
- 因此 **GMU 现在无法访问**，`CM3_SYSRESET` 之类的复位状态读出来也没有意义（读的就是 0）。

### 这条路也关了，而且和 PDC 是同一个原因

CX 的上电在厂商路径里由 GDSC 框架 + `cx.lvl`/`mx.lvl` 的 **RPMh 投票**驱动，
PM8998 S9 那条 PMIC 轨是它的上游。XNU 侧没有 RPMh driver、没有 GDSC 框架，
契约文档也明确禁止"手工翻转 GDSCR 当作整体上电"。

所以结论与 M0c 一致、但位置更靠前：

| 层 | 状态 |
|---|---|
| PDC 请求 GX | 窗口可写、微码可装、请求无应答（缺 `gfx.lvl` 投票） |
| GMU 固件/HFI | **不可达**：CX 域被按在关断（缺 GDSC 所有权 + `cx.lvl` 投票） |

**GPU 在本项目范围内到此为止。** 想继续，前置条件是先在 XNU 里实现 RPMh 投票与
GDSC 所有权——那是独立的一层，工作量大于今天所有工作之和，且需要 `pd`/TZ 的权限证据。

### 保留的判断力

一次只读探测（没有任何写入）就把两条路都定性了，代价只有一次上机。这正是"先读后写"
在这个平台上的价值：M0b/M0c 用写入换来的信息，不如这次九次读取换来的干净。
