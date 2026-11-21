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
