# 把 0094–0096 推到 CI 编译：需要的动作与判读方法

本文只讲一件事：让 GitHub Actions 真正用 Xcode 编一次带 0096 的内核。
所有结论都来自本机实测或工作流/构建脚本原文，不来自"应该没问题"。

## 1. 为什么这一步必须由人来做

| 事实 | 出处 |
|---|---|
| 工作流只在 `push` 到 `main` 或 `workflow_dispatch` 时运行 | `.github/workflows/xnu.yml` |
| 本机对 `luuu20309/ios16_on_polaris_mix2s` 只有读权限 | `git push --dry-run` 需要凭据；本机无 `gh`、无 token |
| 本机 `main` 领先 `origin/main` 13 个提交 | `git log --oneline origin/main..HEAD` |

所以要么在别处用你自己的账号推，要么给这台机器一个可推送的凭据。

## 2. 两条路径

### A. 你自己推（推荐，不需要把 token 交出来）

在 GitHub 网页上 fork `luuu20309/ios16_on_polaris_mix2s`，然后：

```sh
cd E:/mix2s/gpu-driver/upstream
git remote add fork https://github.com/<你的账号>/ios16_on_polaris_mix2s.git
git push fork main
```

fork 之后**必须先做一件事**：打开 fork 的 **Actions** 标签页，点
" I understand my workflows, go ahead and enable them"。fork 的 Actions 默认是关的，
不点这一下，push 上去什么都不会跑。

推送命中 `xnu/patches/**`，会自动触发一次 run，参数是
`macos_version=13.5`、`kernel_config=DEVELOPMENT`、`machine_config=SWEET`、`keep_going=true`。

### B. 给这台机器一个细粒度 token

GitHub → Settings → Developer settings → Fine-grained tokens，只勾：

- Repository access：仅 `ios16_on_polaris_mix2s`（你的 fork）
- Permissions：`Contents: Read and write`、`Actions: Read`

然后我就能 push 并直接读失败日志，不用你来回贴。

## 3. 推之前先跑预检

```sh
cd E:/mix2s/gpu-driver/upstream
sh tools/ci-preflight.sh \
  --conf-files ../build/upstream-xnu/conf-files-141.txt \
  --pg-base ../build/pgbase-polaris_gpu.c
```

它复现 CI 的补丁应用方式，但**把"跳过"当失败**。为什么这一点是关键：

```
for PATCH in "${PATCH_DIR}"/*.patch; do
    if APPLY_CHECK=$(git apply --check "$PATCH" 2>&1); then
        git apply "$PATCH"
    else
        warning "Skipping patch ${PATCH} (...)"
    fi
done
```

`darwin-xnu-build/build.sh` 打不上就只打一条 warning 继续。**绿色的 run 不代表
0096 被应用了**，只代表"没打上的补丁也照样编过了"。

## 4. 判读一次 run 的结果

看日志时按顺序确认三件事，缺一条就不能说"0096 编过"：

1. `Applying patch: .../0096-polaris-gpu-firmware-hash.patch` 出现过；
2. 同一份日志里**没有** `Skipping patch`（尤其没有 0093/0094/0095/0096）；
3. 有 `polaris_gpu_hash.c` 这一行编译命令，且 `-Werror` 下无 error。

第 3 条的意义：`bsd/conf/files` 里加了 `bsd/dev/polaris_gpu_hash.c standard`，
只有这条目被 conf 读到，这个文件才会被编译。前两条过了、第三条没有，说明条目没生效。

## 5. 本机已验证的部分（不需要 CI）

| 检查 | 结果 |
|---|---|
| 工作流实际编的 XNU 版本 | `xnu-8796.141.3`（`distribution-macOS` 的 `macos-135/release.json` 里 `xnu.tag`） |
| `bsd/conf/files` 在 101.5 与 141.3 之间 | 逐字节相同，24,339 B，sha256 `1ca1167ce9b6a05e…` |
| 整条补丁链的 conf/files hunk（14 个补丁） | 全部 `git apply` 干净应用，含 0094/0096 |
| 0094 的 `polaris_gpu.c` 上下文 | 与 0093 产出的文件逐字节一致（3,381 B，`4ff48a9a…`） |
| 我们三个补丁里唯一的上游文件 | 只有 `bsd/conf/files`；其余文件都由 0093/0094 自己创建 |
| 补丁命名与 glob | 96 个 `.patch` 编号唯一；`*.sh` 有 3 个（工作流 `cp ... *.sh` 不会因空 glob 失败） |
| 主机测试 | 87 项 Python + 51 条 C 断言 + 哈希 harness 全过 |

`bsd/conf/files` 的 hunk 是在本地一份 6 行的合成副本上生成的（`@@ -3,4`），
行号与真实文件（596 行）不符；`git apply` 靠上下文定位，实测能落到真实位置
（第 113–119 行一带），所以能用——但这是个易碎点，改这一带时要注意。

## 6. 失败时我需要什么

- 失败 job 的日志片段：从第一个 `error:` 往上 20 行、往下 20 行即够；
- 或者给我 B 路径的 token，我自己拉全量日志。

有了日志，修的是补丁本身（重新生成 `0094`/`0096`），不需要碰手机。
