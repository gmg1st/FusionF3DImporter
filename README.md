# FusionF3DImporter — 本地 F3D 模型一键导入 Fusion 360

一个 Fusion 360 附加模块（Add-In）：把任意**本地 .f3d 文件**的实体一键导入当前设计，无需先把文件上传到云端项目。

## 为什么需要它

- Fusion 360 原生"插入"只认云端数据（插入零部件/云文件），本地 `.f3d` 只能整体"打开"成一个独立文档
- 部分版本的 `importToTarget` API 存在官方回归 bug（`InternalValidationError : results` / `Internal error: correlationId`），标准导入通道在未保存文档中必然失败
- 本插件在命令事件上下文中直接执行 `importToNewDocument`（临时打开）+ 跨文档复制实体（TemporaryBRep）的组合——该 API 通道不受上述限制，隐藏面板页面仅作兜底，**对"未保存（带星号）"的目标文档同样有效**

## 功能

- 入口：**实体（SOLID）页 → "插入"下拉菜单 → "导入本地 F3D"**
- 一次点击：选择本地 f3d → 插件自动临时打开、复制全部实体、插入当前设计、关闭临时文档
- 导入的实体保持原生坐标位置
- 支持 Ctrl+Z 撤销
- 全程写入 `import_log.txt` 日志，出问题可自查

## 安装

1. 下载或克隆本仓库到任意目录（路径建议不含中文）
2. 打开 Fusion 360 → **工具（UTILITIES）→ 脚本和附加模块 → 附加模块**
3. 点绿色 **+** 号，选择本仓库文件夹（含 `FusionF3DImporter.manifest` 的目录）
4. 选中 **FusionF3DImporter** → 点 **运行**（可勾选"运行时启动"开机自动加载）

## 使用

1. 打开（或新建）你的目标设计
2. 实体页 → "插入"下拉菜单 → **导入本地 F3D**
3. 选择 `.f3d` 文件 → 实体自动出现在当前设计中

## 已知限制

- 导入的是**实体几何**（基础实体），不含源文件的零部件结构 / 草图 / 特征历史
- 仅支持 `.f3d`（f3z / step 等格式不在范围内）
- 若某版本 Fusion 的 `importToTarget` 修复后，可自行切回标准导入通道以获得完整特征

## ProbeContexts 诊断探针

`ProbeContexts/` 是一个独立的诊断脚本：用于在**脚本上下文 vs 命令事件上下文**中测试
`importToTarget`、`FileDialog`、`TemporaryBRepManager` 的可用性。当你在自己的 Fusion
版本上遇到导入问题时，运行它并把结果弹窗截图发到 Issue，可以快速定位问题层级。

## 目录结构

```
FusionF3DImporter/
├── FusionF3DImporter.py        # 主插件（按钮注册 + 导入逻辑）
├── FusionF3DImporter.manifest  # 插件清单
├── palette.html                # 备用面板页面（隐形执行通道）
├── resources/                  # 图标（16x16 / 32x32）
├── ProbeContexts/              # 诊断探针脚本
└── install.ps1                 # 可选：部署到 Fusion AddIns 目录
```

## 环境说明

开发与实测环境：Windows 11 + Fusion 360（2026 年版本）。核心导入通道只依赖公开 API，
理论上跨版本可用；若你的版本表现不同，欢迎提 Issue。
