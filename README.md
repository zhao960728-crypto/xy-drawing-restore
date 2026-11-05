# XY图纸1比1还原

在具备识图、文件操作和 Python 命令执行能力的 AI 助手中，协助将 PDF、扫描图或照片还原为可编辑 DXF，生成 PNG 预览和尺寸参考核对记录。

当前版本：**1.0.2**。作者：**XY（GitHub：zhao960728-crypto）**。

## 下载与使用

从 [Releases](https://github.com/zhao960728-crypto/xy-drawing-restore/releases/latest) 下载技能 ZIP。手动安装时，将 `skills/xy-drawing-restore/` 整个目录交给支持本地技能的助手，入口为 `SKILL.md`。不同平台的解析、审核和 Python 执行能力需要分别确认；本项目没有宣称已获 WorkBuddy 或 Red Skill 官方认证。

使用者需要 Python 3.10+、venv/pip 和自己的 AI 助手账号。模型识图与编程使用当前助手的能力；配套脚本不含模型、OCR 或独立的扫描矢量化引擎，不会单独自动还原任意图纸。

首次安装在本次任务目录内创建虚拟环境：

```powershell
python "skills/xy-drawing-restore/scripts/bootstrap.py" --env "C:/my-drawing-task/.venv"
```

运行环境路径由使用者自行选择，不要放进原稿目录。脚本接口、命令示例和核对记录格式见 [workflow.md](skills/xy-drawing-restore/references/workflow.md)。

## 交付与边界

- 交付可编辑 DXF、PNG 预览、来源与尺度记录、自动检查结果；保留输入原稿和生成代码。
- 按真实原图尺寸、明确比例或用户给定参考长度建立毫米尺度，缺少依据时需要确认。
- 自动检查覆盖已声明的实体和尺寸参考，完整图形、孔位、文字和视图关系仍须逐页视觉复核。
- 默认 0.05 mm 是检查容差，不是已验证的识图精度。不承诺与私有 Windows 后台效果一致，不把脚本检查通过当成制造验收。

公开内容仅为本独立技能、辅助脚本和说明，不包含 BDYC 整站、私有后台、账号、密钥、用户图纸或历史任务。脚本不连接开发者电脑；首次安装会访问 PyPI，原稿交给模型后的数据处理规则取决于使用者自己的 AI 助手平台。

## 授权

本项目自有代码与文档采用 **Apache-2.0**，允许免费商用、修改和再分发。对外分发时保留许可证、相关版权和 NOTICE 署名，并标明修改。详见 [商业使用与署名说明](COMMERCIAL_USE.md)、[LICENSE](LICENSE)、[NOTICE](NOTICE) 和 [第三方依赖说明](THIRD_PARTY_NOTICES.md)。免费许可不附送模型额度、第三方服务、托管、支持或制造质量保证。

## 验证状态

本地辅助脚本已做合成样本验证；完整模型识图还原准确率和不同平台实图效果尚未测定。现有限制和历史性能测量的适用范围见 [验证说明](docs/VALIDATION.md)。1.0.2 仅调整公开分发、署名与许可资料，辅助处理算法沿用复查后的 1.0.1。
