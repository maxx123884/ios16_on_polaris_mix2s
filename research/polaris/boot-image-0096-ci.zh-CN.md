# 0096 的启动镜像（CI 内核 → boot.img）

**性质：主机侧打包。没有刷写、没有临时启动、没有读写手机。** 这个镜像从未被启动过。

这一轮把 §16 编出来的内核装进一个可 `fastboot boot` 的镜像，并逐项核对里面的东西。

## 1. 内核来自哪次构建

| 项 | 值 |
|---|---|
| run | [44940183357](https://github.com/maxx123884/ios16_on_polaris_mix2s/actions/runs/44940183357)（`kernel_config=RELEASE`） |
| 内核 | `kernel.release.sweet` 12,269,160 B，sha256 `5b92dadf…f739` |
| 架构 | Mach-O arm64 `MH_EXECUTE` |

**为什么必须 RELEASE**：DEVELOPMENT 内核带符号，payload 会是 16,708,880 B，而 ABL 把 payload
装在 `0x1000000`、DTB 在 `0x1dea000`，上限只有 14,589,952 B——多出的 2.1 MB 会覆盖设备树，
`shim/build.sh` 会直接警告 "Expect a boot failure"。0090 能启动也正是因为它是 RELEASE。
改用 RELEASE 后 payload 落到 11,820,176 B，余量 2,769,776 B。

## 2. 三个部件

| 部件 | 来源 | 大小 / sha256 |
|---|---|---|
| shim | `PLATFORM=polaris shim/build.sh`，zig 交叉编译 | `out/Image` 225,280 B（`image_size=225280`） |
| DTB 尾 | 原厂 `../build/miui/boot.img` 的 65 个 DTB 中只取 **Polaris** 的 14 个 | `out/polaris-dtb.bin` 6,856,239 B，sha256 `3d481f0b…95d1` |
| payload | `mkpayload.py`（内核 + ADT + 六个固件） | 11,820,176 B，sha256 `d9564247…2185` |
| ADT | `mkadt.py --polaris --ram 6` | `shim/out/sweet-ci96.adt` 10,048 B |

只取 Polaris 变体不是省事：原厂尾有 65 个 DTB、30 MB，全塞进去镜像会到 47 MB，而且 0090
那份能启动的镜像只有 18 MB（尾 ~6 MB）。14 个 Polaris 变体覆盖 board-id 0x28/0x29/0x2a/0x2b/0x2c。

## 3. 镜像

```
out/shim-polaris-xnu.img  18,698,240 B
sha256 3fdfd374f2c73cfc05dadeb6449b717aa28572f700678cc7f4c4e39828842a20
kernel=6,869,074  ramdisk=11,820,176  header=v1
```

（0090 那份是 18,042,880 B；差值来自内核与六个固件。）

启动参数（完整，241 字节）：

```
debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0
unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1
launchd_unsecure_cache=1 wdt=-1 sweetusb=2 polarisgpu=1 polarisgpuhash=1
```

`polarisgpu=1 polarisgpuhash=1` 意味着**一开机就会跑门并做 SHA-256 校验**；把这两个词去掉
就是 0090 的等价行为（门不运行）。门本身仍然只读：不映射寄存器、不上电、不改显示。

## 4. 核对过的四件事

1. **解包回读**：`unpack_bootv2.py` 取出的 `ramdisk` 与 payload **逐字节相同**（`cmp` 无输出）；
2. **固件到位**：六个 `GPU-*` 在 `/chosen/memory-map` 里的 PA 与精确字节数都对，
   `payload` 里能找到每个文件的完整字节（例如 `GPU-SQE` 31,980 B @ `0x0101ff8000`）；
3. **尺寸合法**：payload 11,820,176 < 14,589,952（ABL），镜像 18.7 MB < boot 分区 64 MiB；
4. **启动参数**：`cpus=8`、`polarisgpu=1`、`polarisgpuhash=1` 都在。

## 5. 这个镜像不保证什么

- **从未启动过**，所以"能打包"不等于"能开机"；
- **ADT 缺 `/product`**：`mkadt.py` 警告 `research/ipsw-x/d22ap.adt` 缺失，那是 iPhone X 身份
  （`hw.product` 等）的来源。没有它，设备可能不以 iPhone X 自我识别，GUI 表现会与 0090 不同。
  门与哈希不受影响。要补：从 iPhone X IPSW 里取出 D22AP 设备树，放到该路径重新生成 ADT；
- **DTB 只含 Polaris 变体**：换机型不可用（本来也不该换）；
- **RELEASE 内核符号被剥离**：我们的函数不在符号表里（只剩 6,336 条导出符号），
  但 `kernel.release.sweet.link/*.a` 里有 `bsd/dev/polaris_gpu.c` 的编译单元和
  `_polaris_gpu_init`、`_pg_validate_layout`，payload 里也有 `POLARIS GPU: acceleration
  unavailable`、`firmware proven` 字符串；未剥离的 DEVELOPMENT 内核（§16）已逐符号验证过。

## 6. 如果要上机

```sh
fastboot boot build/shim-polaris-xnu-0096ci.img
```

这是设备操作，需要明确同意；预期输出是门在第一个未实现阶段拒绝并打印
`POLARIS GPU: acceleration unavailable; CPU display unchanged`，显示仍走 CPU。
