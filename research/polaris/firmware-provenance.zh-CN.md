# A630 固件的合法来源与实测结果（2026-11-21）

结论：**不需要 MIX 3，也不需要碰手机。** 本机 `E:/mix2s` 里那份国行 MIUI fastboot 包
`polaris_images_V12.5.1.0.QDGCNXM_20210707.0000.00_10.0_cn.tgz`（2.98 GB）内含
`images/vendor.img`（855 MB，Android sparse 格式），六个固件有 **5 个与设备观测尺寸逐字节吻合**，
只有 SQE 是更老的版本。方法：工作区 `gpu-driver/tools/ext4_extract.py` 直接读 ext4（穿透 sparse、处理稀疏洞），
不挂载、不需要 root。

## 1 提取结果

| 文件 | MIUI 12.5.1 尺寸 | 设备观测尺寸 | 判定 | SHA-256 |
|---|---|---|---|---|
| `a630_sqe.fw` | 31980 | 32136 | **版本不符** | `34edacb6fdedfbfc07ed5cd1d29b0b57a3543ef5be6ab34a43bd64686826fa1e` |
| `a630_gmu.bin` | 32768 | 32768 | 一致 | `b07f90299ff2ae33948fcfd6ed67f53fbd287b5bf85ff2d43db11d4d32c1621f` |
| `a630_zap.mdt` | 6684 | 6684 | 一致 | `1882b214d24c13eae4ffc39ba5456d5e61c80b089fb77e8b15f7bdd04bc9c831` |
| `a630_zap.b00` | 148 | 148 | 一致 | `b2975f6a4c28a98197c1d694f6e275e71b23ec7e31e32ff5d1f83fdb80a94282` |
| `a630_zap.b01` | 6536 | 6536 | 一致 | `c94da0d6edf4820885b814ecda2f91c89b9d766126c3574c05d580c418815b5f` |
| `a630_zap.b02` | 1968 | 1968 | 一致 | `519b5538809f54ee566246b0520746a8ea82ca9384ea6bddb0b0e0d3c2de520c` |

`a630_gmu.bin` 头部两字为 `0x43f80`（栈指针）与 `0x9`（Thumb 入口），与文档记录的
TCM_BOOT 平铺镜像格式一致；该文件在 ext4 里是**稀疏文件**（只映射 5 块 20480 B，尾部
12288 B 是洞），读取时必须按 `i_size` 补零，否则会少 12 KiB。这条已在
`gpu-driver/tools/ext4_extract.py` 里修好并注释。

### 1.1 zap 四件是同一套的密码学证据

`a630_zap.mdt` 是 ELF32（class 1，machine 164），其哈希表里嵌着分片摘要：

| 分片 | 自身 SHA-256 | 在 `a630_zap.mdt` 中的偏移 |
|---|---|---|
| `a630_zap.b00` | `b2975f6a4c28a981…a94282` | **188** |
| `a630_zap.b02` | `519b5538809f54ee…de520c` | **252** |
| `a630_zap.b01` | `c94da0d6edf48208…815b5f` | 未出现（它是 metadata/hash 段本身，按设计不被哈希） |

也就是说：`mdt` 与 `b00`、`b02` 互相绑定，**不是从不同版本里拼凑的文件**。这比"尺寸相同"
强得多——即使将来换用别的 MIUI 版本，也可以用这条检查立刻判断四件是否成套。

## 2 SQE 的三个版本（都非本机作者的 32136）

| 来源 | 尺寸 | 版本字段 @4 | 备注 |
|---|---|---|---|
| MIUI 12.5.1（polaris，本包） | 31980 | `0x016ee187` | 本机可得的官方件 |
| **设备观测（作者手机）** | **32136** | 未记录 | 上游 `pg_expect` 的基准 |
| linux-firmware `qcom/a630_sqe.fw` | 34188 | `0x016ee207` | 公共仓库，更新版本 |

`tools/polaris_gpu_expect_table.py` 因此**拒绝**生成摘要表——这是设计如此，不是 bug：
尺寸不符即"这套固件本移植从未见过"。门禁工作正常，已实测复现。

推断：作者手机上那份 32136 很可能来自比 12.5.1 更新的 MIUI（GPU 微码会随 OTA 更新）。
所以三选一：

- **A. 读手机自己的 vendor**：`fastboot boot` 临时启动 TWRP 后 `dd` 出 vendor 分区。
  能拿到与作者完全一致的 32136，但要临时启动第三方镜像（不写分区，需明确同意）。
- **B. 钉住 12.5.1 这套**：SQE 用 31980，把六个真实摘要写进 `pg_expect`（新增补丁 0095），
  在注释里写明"来源：polaris V12.5.1 vendor.img，SQE 比作者观测的 32136 早一版"。
  自洽、可复现、可审计；代价是基准从"作者手机"变成"官方包"。
- **C. 下载更新的 MIUI fastboot 包**（如 V12.5.7 CN）再取一次，希望命中 32136。
  纯网络工作，3 GB 级下载，本机到 GitHub/小米 CDN 均不稳。

## 3 其他来源的边界（都已核对）

- **linux-firmware**（`git.kernel.org/.../linux-firmware.git/tree/qcom`）：有
  `a630_gmu.bin`（32768）与 `a630_sqe.fw`（34188），**没有** `a630_zap.*`；
  其 gmu 与 MIUI 12.5.1 的 gmu 在偏移 1076 处即不同 → 是另一个修订，不能混用。
- **任何 SDM845 + A630 机型**（Mi 8 / POCO F1 / OnePlus 6/6T / Pixel 3 / XZ2 / Z18…）
  的 vendor 都能提供同一组 SoC 级固件；但既然本机已有 polaris 官方包，没必要绕。
- **MIX 3（perseus）**：同 SoC，理论等价；但同样需要额外下载，已无必要。
- **zap 三件**只存在于厂商 vendor（带签名），公共固件库没有；这也解释了为什么必须有 vendor 镜像。

## 4 复现方式

```sh
# 1) 从 MIUI 包里只取 vendor.img（约 855 MB）
tar -xzf polaris_images_V12.5.1.0.QDGCNXM_20210707.0000.00_10.0_cn.tgz \
    -C build/miui --strip-components=2 \
    "polaris_images_V12.5.1.0.QDGCNXM_20210707.0000.00_10.0_cn/images/vendor.img"

# 2) 直接读 ext4（Windows/Git Bash 下需要 MSYS_NO_PATHCONV=1，否则 /firmware 会被改写成 Windows 路径）
MSYS_NO_PATHCONV=1 python ../tools/ext4_extract.py list vendor.img /firmware
MSYS_NO_PATHCONV=1 python ../tools/ext4_extract.py get  vendor.img /firmware/a630_gmu.bin build/fw-miui

# 3) 校验：尺寸不符会立即拒绝
python upstream/tools/polaris_gpu_expect_table.py --firmware build/fw-miui
```

## 5 状态（2026-11-21，已按方案 B 落地）

- **选 B**：钉住 12.5.1 这套，SQE 用 31980。没有为此放松任何检查，也没有读手机。
- 固件集清单：`research/polaris/firmware-sets/polaris-miui-12.5.1.0-qdgcnxm.json`，
  由 `tools/polaris_firmware_set.py` 记录/校验（10 项测试）。`--check` 会重算目录摘要，
  任何一字节不符即 `REFUSE`。
- 代码：`xnu/patches/0095-polaris-gpu-pin-firmware-set.patch`（4 文件 +104/−33）把六个
  真实摘要写进 `polaris_gpu_expect[]`，并把尺寸闸门改成"以表为准"；另加一条判定，
  让"表已填满但内核从未算过摘要"报 `PG_EHASH_UNPROVEN` 而非 `MISMATCH`。
- 验证（都实跑）：0094→0095 顺序应用成功；`-Wall -Wextra -Werror -Wpedantic` 零警告；
  C 校验器 39 处断言（28 次状态期望 + 11 次布尔检查）全过；Python 主机测试 71 项全过；端到端用真表驱动校验器，
  本套返回 `PG_OK`（6 blobs / 131072 B），换 32136 的 SQE 返回 `PG_EBLOB_SIZE`，
  翻一个字节返回 `PG_EHASH_MISMATCH`，摘要全零返回 `PG_EHASH_UNPROVEN`。
- 摘要后端骨架：`xnu/patches/0096-polaris-gpu-firmware-hash.patch`（11 文件 +477/−14），
  校验器拆布局/摘要两阶段，新增 `bsd/dev/polaris_gpu_hash.c`（`ml_io_map` + 内核
  `SHA256_*`），开关 `polarisgpuhash=1` 默认关闭。主机 harness 验证拷贝路径；库与映射
  的可用性须由 CI 编内核确认。
- 仍然**没有上电路径**：固件阶段认可后照旧停在 `firmware backend not implemented`。
- 即使固件全齐，**Metal 上限不变**：界面加速仍无 AIR→Adreno 后端；这一步只解锁
  "GMU 能装载、CP 能跑一条命令"的硬件里程碑。
- 若将来要换成与作者那台一致的 32136：从这台手机的 vendor 分区直接 `dd`（需临时启动
  第三方镜像，未执行），或换更新的 MIUI 包重取；两条都不必改代码，只需新清单 +
  `--manifest` 重生成表。
