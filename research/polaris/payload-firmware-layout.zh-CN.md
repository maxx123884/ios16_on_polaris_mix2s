# 把 A630 固件打进 payload（0096 之后的 ①）

**性质：主机侧文件变换。没有刷写、没有临时启动、没有读写手机。**

这一轮只做一件事：让 `/chosen/memory-map` 里真的出现六个固件条目，并且条目指向的
物理内存里真的是那六个文件。固件不执行，GPU 不上电，`polarisgpuhash=1` 也仍是关闭的。

## 1. 为什么不直接 `mkpayload.py --blob`

`--blob` 需要重新链接后的 kernel Mach-O 和独立 ADT。0090 Release 只发布了
`boot.img`，它的 ramdisk **就是** SWEETXNU payload（kernel 段 + ADT），Mach-O 与
ADT 没有单独发布。拿不到 Mach-O，就不能重跑 `mkpayload.py`。

所以在 payload 格式上做等价变换，而不是重新链接内核：

- 原 payload 的每个字节、每个段原样保留；
- 每个固件成为新的一段，起点接在旧 `kernel_end` 上，按 16 KiB 页对齐——与
  `mkpayload.py --blob` 的放置规则相同；
- `/chosen/memory-map` 追加 `NAME = <pa, 精确字节数>`，这是
  `polaris_gpu_read_handoff()` 唯一读取的东西；
- 头部只改 `nsegs / kernel_end / adt_off / adt_size`，命令行 608 字节一字不动。

工具：`tools/polaris_payload_blobs.py`，测试：`tools/test_polaris_payload_blobs.py`（16 项）。

## 2. 顺序约束（为什么起点必须是旧 kernel_end）

shim（`shim/loader.c`）做两件事，顺序固定：

1. 把每个段拷到 `phys_base + (vmaddr - virt_base)`；
2. 把 ADT 写到 `kernel_end + (phys_base - virt_base)`，boot_args 紧跟其后，
   `topOfKernelData` 在 boot_args 之后。

因此固件段必须落在 `[旧 kernel_end, 新 kernel_end)`，新的 `kernel_end` 正好是 ADT
的落点。任何“把 blob 放到 ADT 后面”的做法都会被 shim 覆盖，反之则会覆盖 ADT。
工具在写之前检查这一点，也在 `--verify` 时重新推导一遍。

每段单独页对齐（而不是六个文件拼成一段）是必须的：shim 的 `copy64()` 会按 8 字节
向上取整读取，若两个固件首尾相接，最后一个 4 字节块会读到下一个固件的开头；分开
分配后，越界读只落在零填充里。

## 3. 实数

| 项 | 变换前 | 变换后 |
|---|---|---|
| payload | 11,742,064 B | 11,822,672 B |
| 段数 | 10 | 16 |
| `kernel_end` | `0xfffffe0007ff8000` | `0xfffffe0008018000` |
| ADT | 12,776 B @ `0xb2f980` | 13,088 B @ `0xb43330` |
| 距 ABL 上限（14,589,952 B） | 2,847,888 B | 2,767,280 B |
| `boot.img` | 18,042,880 B | 18,124,800 B |

文件只涨 80,608 B（固件 80,084 + 段表 192 + ADT 312 + 对齐 20）；**物理预留是
128 KiB**，因为六段各自按 16 KiB 对齐。这两个数字不矛盾：payload 里存精确字节，
页尾由 shim 在装载时清零。

| 段 | VA | PA | 字节 | 预留 |
|---|---|---|---|---|
| GPU-SQE | `0xfffffe0007ff8000` | `0x0101ff8000` | 31,980 | 32 KiB |
| GPU-GMU | `0xfffffe0008000000` | `0x0102000000` | 32,768 | 32 KiB |
| GPU-ZAP-MDT | `0xfffffe0008008000` | `0x0102008000` | 6,684 | 16 KiB |
| GPU-ZAP-B00 | `0xfffffe000800c000` | `0x010200c000` | 148 | 16 KiB |
| GPU-ZAP-B01 | `0xfffffe0008010000` | `0x0102010000` | 6,536 | 16 KiB |
| GPU-ZAP-B02 | `0xfffffe0008014000` | `0x0102014000` | 1,968 | 16 KiB |

DRAM 是 `0x1_0000_0000 + 0x7cc0_0000`，固件占用的是内核镜像上方紧邻的 128 KiB，
离 8 KiB 的 PAS13 zap 区（`0x8c415000`）和 ABL 开机图（`0x9d400000`）都很远；
工具仍会显式拒绝与这两个窗口重叠的放置。

## 4. 越过 32 MiB 会不会踩到启动映射

原 `kernel_end` 距 `virt_base` 只有 31.97 MiB，新值越过了 32 MiB 边界，所以这个
问题必须先回答，不能假设。

XNU 8796 的 `osfmk/arm64/start.s` 并不只映射一个 32 MiB 块：它先从
`_mh_execute_header` 反推镜像起点，把 `virtBase/physBase` 前移到该点，然后用
`boot_args.mem_size` 减去这段偏移作为映射长度，**一直映射到物理内存末尾**
（`lsr x5, x24, #ARM_TT_L2_SHIFT` 得到 L2 条目数）。32 MiB 只是块粒度，不是上限。
`mkpayload.py` 把 `virt_base` 取在 32 MiB 对齐处，是为了让第一个块正好从
`phys_base` 开始，而不是为了把内核塞进一个块。

结论：`0xfffffe0008018000` 与紧随其后的 ADT、boot_args 都在 bootstrap 映射内。
这条结论来自上游 `start.s` 的取址与长度计算，不是实测；真机仍未验证。

## 5. boot 镜像可以逐字节复现

重打包不是“看起来对”，是可证明的：用 `boot.img` 里原有的 kernel 与 ramdisk，
按同样的头部参数交给仓库的 `tools/mkbootimg.py`，输出与原 `boot.img`
**逐字节相同**（sha256 `897ee85c…59b86`）。唯一需要显式给出的是
`--dtb_offset 0x1dea000`——它同时就是 ABL 允许的 ramdisk 上限
（`0x1dea000 - 0x1000000 = 14,589,952`）。

因此“换掉 ramdisk 再打包”只改变 ramdisk_size 与内容，其余头字段（kernel 地址、
ramdisk 地址、second、tags、页大小、版本、os_version、name、cmdline、id、
recovery_dtbo）全部保持原值；这一点在产物上逐字段核对过。

产物：

- `payload-gpu-fw.bin`：11,822,672 B；
- `boot-gpu-fw.img`：18,124,800 B，sha256 `414313a5…fd179d`；
- 均在本机 `build/gpu-fw/`，**不在仓库里**（Qualcomm 固件不随仓库分发）。

## 6. 复核方法

```sh
# 16 项单元测试（合成 payload，不需要固件）
python -m unittest tools/test_polaris_payload_blobs.py -q

# 对真实 payload 重新推导布局、逐段重算 SHA-256 并与清单比对
python tools/polaris_payload_blobs.py payload-gpu-fw.bin --verify \
    --manifest research/polaris/firmware-sets/polaris-miui-12.5.1.0-qdgcnxm.json
```

`--verify` 不信任写盘时的记录，只从 payload 自己重新算：段表 → ADT 条目 →
`pa` 与段的对应关系 → 段内字节的 SHA-256；并检查页对齐、DRAM 边界、保留窗口、
段间重叠、ABL 上限。另有独立一次性核对：原 10 段逐字节相同、命令行相同、
ADT 差异恰好是新增的六条 `memory-map`。

## 7. 仍然没有证明的事

- **没有真机启动过这个镜像。** 它是否能把六个段搬到对的物理地址、XNU 是否接受
  抬高 128 KiB 的 `topOfKernelData`，只有一次经批准的临时启动能回答。
- **0096 还没有被 CI 编译过。** `<libkern/crypto/sha2.h>`、`ml_io_map()` 在
  `bsd/dev` 里能否编过，主机 harness 替不了 CI。
- **摘要正确不等于固件可用。** 即使 `polarisgpuhash=1` 打印
  `firmware proven against the pinned set`，也只证明“读到的就是钉住的那六个文件”，
  不证明 GMU 能启动、HFI 能通、PAS13 能过。
- payload 无签名，信任模型与 0090 相同：信任来自包的来源，而不是镜像自身的证明。
