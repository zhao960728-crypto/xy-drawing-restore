# 第三方依赖及分发边界

本仓库和技能 ZIP 不捆绑下列依赖的源码、wheel、PDFium 二进制或系统字体；`bootstrap.py` 在使用者任务目录安装指定版本。下表是直接依赖索引，不代替上游完整许可证或全部传递依赖清单。

| 直接依赖 | 固定版本 | 上游许可信息 | 官方来源 |
| --- | --- | --- | --- |
| ezdxf | 1.4.4 | MIT | [PyPI](https://pypi.org/project/ezdxf/1.4.4/) |
| Pillow | 12.3.0 | MIT-CMU；所用发行物内其他组件分别声明 | [PyPI](https://pypi.org/project/Pillow/12.3.0/) |
| matplotlib | 3.10.9 | Matplotlib License Agreement；附带组件及字体各有许可 | [PyPI](https://pypi.org/project/matplotlib/3.10.9/) |
| pdfplumber | 0.11.10 | MIT | [PyPI](https://pypi.org/project/pdfplumber/0.11.10/) |
| pypdfium2 | 5.14.0 | Apache-2.0 / BSD-3-Clause；PDFium 与所含组件另有声明 | [PyPI](https://pypi.org/project/pypdfium2/5.14.0/) |

这些组件不因本项目使用 Apache-2.0 而改用同一许可。若自行制作离线包、安装器或容器并再分发依赖，须随实际分发的版本保留各组件要求的 LICENSE、NOTICE 和其他许可材料；特别是 PDFium 所含组件的完整许可，不能以本文件替代。以所选 wheel、源码或构建物中的声明为准。

中文显示使用安装者本机字体。本项目没有授权再分发系统字体，制作安装器或嵌入字体时应单独确认相应许可。

模型能力由使用者自己的 AI 助手提供，本项目不捆绑模型权重、模型 SDK 或服务账号，也不替模型和平台条款作授权承诺。
