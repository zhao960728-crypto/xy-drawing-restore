# XY图纸1比1还原

独立的图纸还原项目。配合具备识图、文件操作和 Python 执行能力的 AI 助手，将 PDF、扫描图或照片还原为可编辑 DXF，并生成 PNG 预览和尺寸核对记录。

当前源码 **1.0.3**，最近发布版 **1.0.2**。作者 **XY（GitHub：zhao960728-crypto）**。

## 下载与使用

从 [Releases](https://github.com/zhao960728-crypto/xy-drawing-restore/releases/latest) 下载技能 ZIP。手动安装时，将 `skills/xy-drawing-restore/` 整个目录交给支持本地技能的助手，入口为 `SKILL.md`。不同平台的解析、审核和 Python 执行能力需要分别确认；本项目没有宣称已获 WorkBuddy 或 Red Skill 官方认证。

使用者需要 Python 3.10+、venv/pip 和自己的 AI 助手账号。模型识图与编程使用当前助手的能力；配套脚本不含模型、OCR 或独立的扫描矢量化引擎，不会单独自动还原任意图纸。

首次安装在本次任务目录内创建虚拟环境，并从 PyPI 安装依赖。

```powershell
python "skills/xy-drawing-restore/scripts/bootstrap.py" --env "C:/my-drawing-task/.venv"
```

运行环境放在原稿目录之外。原稿由使用者选择的 AI 助手按其平台规则处理。脚本接口、命令示例和核对记录格式见 [workflow.md](skills/xy-drawing-restore/references/workflow.md)。

## 交付与边界

- 交付可编辑 DXF、PNG 预览、来源与尺度记录、自动检查结果；保留输入原稿和生成代码。
- 按真实原图尺寸、明确比例或用户给定参考长度建立毫米尺度，缺少依据时需要确认。
- 可编辑曲线使用原生圆弧或有误差边界的连续多段线。局部复杂花纹保留轮廓与空隙，尺寸使用原生 DIMENSION 并从实际文件回读。
- 逐页比较原稿与实际 DXF，放大核对复杂区域。通过结构检查、尺寸回读和视觉核对后，用 `deliver` 生成逐页同名 DXF 与 PNG。
- 自动检查覆盖已声明的实体和尺寸参考，完整图形、孔位、文字和视图关系仍须逐页视觉复核。
- 0.05 mm 为默认尺寸核对容差。具体还原效果按原稿逐页复核。

## 授权

本项目自有代码与文档采用 **Apache-2.0**，允许免费商用、修改和再分发。对外分发时保留许可证、相关版权和 NOTICE 署名，并标明修改。详见 [商业使用与署名说明](COMMERCIAL_USE.md)、[LICENSE](LICENSE)、[NOTICE](NOTICE) 和 [第三方依赖说明](THIRD_PARTY_NOTICES.md)。免费许可不附送模型额度、第三方服务、托管、支持或制造质量保证。

## 验证状态

本地辅助脚本已做合成样本验证；完整模型识图还原准确率和不同平台实图效果尚未测定。现有限制和历史性能测量的适用范围见 [验证说明](docs/VALIDATION.md)。1.0.2 仅调整公开分发、署名与许可资料，辅助处理算法沿用复查后的 1.0.1。
