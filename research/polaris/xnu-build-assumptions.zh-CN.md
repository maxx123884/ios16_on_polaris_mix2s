# 0096 的内核编译假设（逐条核对 8796 上游）

**性质：静态核对 + 主机测试。没有编译过整颗内核，没有动手机。**

0096 的 `bsd/dev/polaris_gpu_hash.c` 用到了三样 XNU 外部的东西。主机 harness 只能用
桩模拟它们，所以三样都必须回到上游源里确认，而不是“编过再说”。

## 1. `libkern/crypto/sha2.h`

树内真实路径是 **`libkern/libkern/crypto/sha2.h`**：include 根是 `libkern/`，所以源码里
写 `<libkern/crypto/sha2.h>` 才对得上（直接找 `libkern/crypto/sha2.h` 会 404，容易被
误判成“头文件不存在”）。该头定义：

```c
typedef struct {
	ccdigest_ctx_decl(CCSHA256_STATE_SIZE, CCSHA256_BLOCK_SIZE, ctx);
} SHA256_CTX;

void SHA256_Init(SHA256_CTX *ctx);
void SHA256_Update(SHA256_CTX *ctx, const void *data, size_t len);
void SHA256_Final(void *digest, SHA256_CTX *ctx);
```

与 `polaris_gpu_hash.c` 的三处调用逐字一致。Apple 自己的 `libkern/crypto/corecrypto_sha2.c`
也用同一行 include。

## 2. `CRYPTO_SHA2` 是打开的

`corecrypto_sha2.c` 的实现整段在 `#if defined(CRYPTO_SHA2)` 内；未定义时三个函数会变成
`UNSUPPORTED_API` 桩（链接能过，调用即 panic）。所以“能编过”不等于“能用”。

- `config/MASTER`：`options CRYPTO_SHA2 # <crypto_sha2>`
- `config/MASTER.arm64`：`LIBKERN_BASE = [ … crypto_sha2 … ]`，
  `DEVELOPMENT`/`RELEASE` 都含 `LIBKERN_DEV`/`LIBKERN_RELEASE` → `LIBKERN_BASE`

CI 默认 `KERNEL_CONFIG=DEVELOPMENT`、`MACHINE_CONFIG=SWEET`，SWEET 是 arm64 机器配置，
因此走的是带 `crypto_sha2` 的那条属性链。0023 补丁正是对这条链的 SWEET 分支做替换
（`ccsha256_ltc_di` 直接来自内置 LTC），这也从侧面印证该分支是活的。

## 3. `ml_io_map` 的签名

`osfmk/arm64/machine_routines.c` 提供实现；公开头里没有稳定声明，所以 Apple 自己的
kext 也是手写 extern。`apple-oss-distributions/IOPCIFamily` 的 `AppleVTD.cpp` 第 41 行：

```c
extern "C" vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
```

与 0096 的声明逐字相同，且该文件用同一函数映射 VT-d 的 MMIO 寄存器——说明它在 arm64
上返回可做 Device 访问的 VA，而不是“只能读 cacheable 内存”的别名。

## 4. 顺手修掉的第四个假设：`vm_offset_t` 的作用域

`bsd/sys/param.h` → `sys/types.h` → `machine/types.h` 这条链里没有
`<mach/vm_types.h>`；`vm_offset_t`/`vm_size_t` 属于后者。原先靠传递包含“碰巧能编”，
现在显式写出：

```c
#include <mach/vm_types.h>
```

主机桩同步拆成 `stub/mach/vm_types.h`（只放两个 typedef），`stub/sys/param.h` 不再
重复定义，避免 C99 下 typedef 重定义。

## 5. 改完之后的验证

| 项 | 结果 |
|---|---|
| 0090 基线 → 0094 → 0095 → 0096 顺序应用 | 通过，0 CRLF |
| C 校验器 | 51 处断言全过 |
| 哈希 harness（含新桩） | `hash copy path matches the source for every pinned size` |
| Python 主机测试 | 87 项全过 |
| 补丁规模 | 12 文件 +492/−14（原 11 文件 +477/−14） |

## 6. CI 仍然必须证明的事

- 整颗内核在 macOS 14 + Xcode 15 下能否编过（头文件解析、`-Werror`、链接顺序）；
- `bsd/conf/files` 的编译标志是否让 `-DARM64_BOARD_CONFIG_SWEET` 进到 `bsd/dev`；
- `ml_io_map` 在内核链接时是否可达（它由 osfmk 提供，bsd 静态链入，预期可达但未证）。

这三条都不是主机测试能替代的。在 CI 跑过之前，0096 只能算“有出处、有主机测试”，
不能算“可编译”。

## 7. 把三个源文件对真头文件编一遍（2026-11-22）

§6 的第一条里，最可能先炸的是“我们自己的代码在真头文件下能不能过 `-Werror`”，
那一半不需要 CI：把 `xnu-8796.141.3` 的树取下来、打上补丁，用内核自己的编译选项
直接编 `bsd/dev/polaris_gpu*.c` 即可。

```sh
git clone --depth 1 -b xnu-8796.141.3 \
  https://github.com/apple-oss-distributions/xnu ../build/xnu-141   # 97 MB
python3 tools/polaris_kernel_syntax.py --tree ../build/xnu-141 --apply
```

工具做的事，以及每一件的理由：

| 步骤 | 为什么必须这么做 |
|---|---|
| 打全部 `xnu/patches/*.patch` | 三个源文件由 0093/0094/0096 创建；`bsd/conf/files` 也只是补丁的产物 |
| 把 `osfmk/arm` + `osfmk/arm64` 拼成 `machine/` | 内核构建里 `osfmk/machine` 是 make 生成的软链接，树里没有 |
| 用 `tools/kernel-headers/string.h` | 内核的 `<string.h>` 来自 macOS SDK（`-nostdinc` 后由 SDK 补），zig 没有 SDK |
| `bsd/conf/Makefile.template` 的 `-D` 集合 | 少了 `-DBSD_BUILD` 等，`sys/types.h` 会走另一条分支，检查比 CI 更松 |
| `makedefs/MakeInc.def` 的 `-Weverything -Werror` 与全部 `-Wno-` | 内核就是这套；顺序有意义（先整组关，再单独开 `-Wpointer-arith`） |
| 先编 `tools/kernel-headers/baseline.c` 做对照 | 只含我们依赖的六个系统头。它编不过就说明环境不对，此时我们的失败不算数 |

结果：

```
ok    control baseline.c compiles with the same flags (header chain is sound)
ok    polaris_gpu.c compiles against xnu-8796.141.3 headers (1 warning(s))
ok    polaris_gpu_hash.c compiles against xnu-8796.141.3 headers (clean)
ok    polaris_gpu_validate.c compiles against xnu-8796.141.3 headers (clean)
```

**它抓到一个真问题**：`polaris_gpu_init` 在定义处没有原型，`-Wmissing-prototypes`
（`-Weverything` 的一部分）会判 `-Werror` 失败。主机 gate 编得过去是因为它用的是
桩头文件，CI 会用真头文件——这条只在真头上出现。修法是新增 `bsd/dev/polaris_gpu.h`
放原型，`polaris_gpu.c` 包含它（0093 里一并加上）。

### 符号面（工具现在会查）

编完之后工具把三个目标文件的 Mach-O 符号表读回来，查两件在链接期才会暴露、但在对象里
就能量出来的事：

- **同一个全局符号被两个 TU 定义** → 链接必炸；
- **我们调用、但整个树里没有定义** → 编译能过（声明是有的），链接才炸。

实测：7 个全局符号、无重复；12 个外部符号全部在树里找到定义：

| 符号 | 树里的定义 |
|---|---|
| `ml_io_map` | `osfmk/arm64/machine_routines.c`（原型在 `osfmk/arm/machine_routines.h`，逐字段一致） |
| `SHA256_Init/Update/Final` | `libkern/crypto/corecrypto_sha2.c`，0023 已把它改成内置 LTC 实现 |
| `PE_parse_boot_argn` | `pexpert/gen/bootargs.c` |
| `SecureDTLookupEntry` / `SecureDTGetProperty` | `pexpert/gen/device_tree.c` |
| `polaris_touch_i2c_board` | `bsd/dev/polaris_touch_diag.c`（返回 `bool`，与我们的 extern 一致） |
| `printf` / `memcpy` / `memset` / `bzero` | 内核自带（`printf` 在 `osfmk/kern/printf.c`，另三个是 libkern 的实现） |

两个判据都是"精确"而不是"看起来像"，因为都踩过坑：

- 判定义不能用 `git grep`——补丁新建的文件是未跟踪的，`git grep` 看不见，
  `polaris_touch_i2c_board` 因此一开始被判成"不存在"。改为直接扫文件系统。
- 判"这一行是定义"必须要求符号在列 0：`bsd/dev/mem.c:326` 是
  `PE_parse_boot_argn("kmem", …)` 的续行，缩进之后和 K&R 定义长得一模一样。
- 搜索范围排除 `SETUP/`、`tools/`、`tests/`：那里也有 `printf`/`memcpy`，但那是编译到
  主机上的程序，把它们算作"内核里有定义"等于自欺。

另外修掉了工具的一个不忠实处：zig 的 clang **默认给每个目标文件插 UBSan**
（`int f(int a){int b=a*4;return b;}` 就会导入 `__ubsan_handle_mul_overflow`），
而内核按自己的 `UBSAN_CHECKS` 选择插桩；现在 `-fno-sanitize=all -fno-stack-protector`
把它关掉，否则这些 handle 会把我们真正依赖的外部符号埋掉。同类的还有 zig 的输出缓存：
第二次跑同样的输入它会直接复用产物、**不重放诊断**，于是"有警告"的文件第二次显示
"clean"；现在每次给一个丢掉的 `ZIG_LOCAL_CACHE_DIR`，保证两次结果一样。

这一层只证明"符号有定义"，不证明链接顺序与裁剪（`-dead_strip`、export 列表）。

### 这个检查不覆盖什么

- `-DBSD_KERNEL_PRIVATE`。`bsd/conf/Makefile.template` 对每个 bsd 源都加它，但它让
  `sys/systm.h` 拉进 `sys/tty.h` 等内核私有面，那需要 `-include meta_features.h` 和
  构建期生成的头。实测：打开这个宏后，`polaris_gpu.c` 与一个原生 bsd 源都在 XNU 自己的
  `tty.h` 里报同样的错——所以缺口在环境，不在我们的代码。工具因此不传它，覆盖的是
  非私有面，而我们的原型与类型全在非私有面上。
- 链接（`ml_io_map` 是否可达）、`meta_features.h` 里的 `CONFIG_*`、其余 90 多个补丁
  的文件是否都能过 —— 仍然只能由 CI 证明。
- 三个“clang 版本差”警告被显式关掉：本机是 zig 的 clang 22，CI 是 Apple clang 15。
  `-Wunsafe-buffer-usage`（clang 16 才有）、`-Wshadow-header`、`-Wreserved-identifier`
  都只打在 Apple 自己的 typedef（`bsd/arm/_types.h`）和 corecrypto 头上，与本项目无关；
  它们的名字集中在 `TOOLCHAIN_GAP`，注释里写了理由。

