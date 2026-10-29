# Arena2API · Windows x64 便携版（2026-10-29）

把浏览器里已经打开的 Arena 会话接成本机 OpenAI 兼容接口，供 DSH 等客户端使用。工具由客户端在本机执行。

**完整说明见 [使用手册.md](使用手册.md)。**

## 下载

到本仓库的 **Releases** 页面下载：

| 文件 | 内容 |
|---|---|
| `Arena2api-portable-win-x64-2026-10-29.zip` | 便携主程序，自带 Python、Node.js、Qt 运行库，解压即用 |
| `Arena2API-browser-extension-1.6.0.zip` | 浏览器扩展 |
| `dsh-step-viewer-0.1.1-gpl.1.tgz` | DSH “执行过程”插件（可选） |
| `Arena2API-manual-zh-CN.md`、`SHA256SUMS.txt` | 使用手册（与仓库里的 `使用手册.md` 相同）和校验值 |

仓库里只有源码，不含 `runtime\` 运行库，直接克隆下来双击启动器是跑不起来的。普通使用请下载 Releases 里的便携包。

## 快速开始

1. 完整解压到一个独立目录，不要在 zip 里直接运行。
2. 在 Chrome 的 `chrome://extensions` 打开开发者模式，加载 `extension\chrome`。
3. 双击 `启动控制台.cmd`，在设置里改掉默认 API Key `arena-local`，然后启动网关。
4. 在 Arena 页面的 Arena2API 接入设置里打开“启用接入”。
5. 在控制台“账号与会话”里添加一个已有会话，填模型名，启用并暴露模型。
6. 客户端填 `http://127.0.0.1:9090/v1`、你的 Key 和模型名。

## 版本

| 组件 | 版本 |
|---|---|
| 网关 | 0.5.0 |
| 浏览器扩展 | 1.6.0 |
| DSH 插件 dsh-step-viewer | 0.1.1-gpl.1（可选） |
| Python / Node.js / Qt | 3.13.16 / 22.23.3 / 6.8.3（随包自带） |

## 相比 2026-10-19 版的变化

- 账号识别先调 Arena 的 `/api/me`，后台标签页侧边栏没渲染时也能识别。
- Arena 明确拒收提交（403/429）时自动重试一次。
- 拒收时报错写明响应类别（Cloudflare 验证、拦截、人机验证、限流、登录失效），不再被 DSH 显示成“API 密钥无效”。
- 回传工具结果时，上一轮超长的工具参数（比如整段 HTML）用占位说明代替，减少被拦截。
- 按模型名的工具白名单（`.console\tool-filter.json`），默认只放行基础开发工具。
- DSH 进度区显示思考原文和工具步骤，去掉了重复的固定提示。

## 包内内容

- 源码、测试、许可证全部随包提供。
- 不含浏览器、DSH 主程序、账号登录状态、作者的配置和日志。
- `PORTABLE-VALIDATION.json` 记录本版实际验收范围；`SHA256SUMS.json` 仅供核对完整性。
- 分发边界见 `docs/分发边界.md`；构建与第三方许可见 `docs/便携版构建与许可.md`、`THIRD-PARTY-SOURCES.json`。

本项目与 Arena 无官方关联，请遵守其服务条款。许可：GPL v3（见 `LICENSE`）。
