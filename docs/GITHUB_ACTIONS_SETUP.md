# GitHub Actions 自动构建配置指南

## 📋 概述

本指南说明如何配置 GitHub Actions 自动构建工作流，为 VORTEX Trading System 生成所有平台的可执行文件。

## 🎯 工作流功能

### 触发条件
- **推送标签**：当推送 `v*` 标签时自动触发（例如 `v1.0.0`）
- **手动触发**：通过 GitHub Actions 页面手动触发

### 构建平台
- **macOS**：生成 DMG 和 ZIP 文件
- **Windows**：生成 Setup EXE 和便携版 EXE
- **Linux**：生成 AppImage 和 DEB 包

### 自动发布
- 构建完成后自动创建 Release
- 自动上传所有平台的可执行文件
- 自动生成发布说明

## 🚀 快速开始

### 第 1 步：配置工作流文件

工作流文件已位于：`.github/workflows/build.yml`

### 第 2 步：推送到 GitHub

```bash
cd /path/to/trading-system-desktop

# 添加工作流文件
git add .github/workflows/build.yml

# 提交
git commit -m "Add GitHub Actions build workflow"

# 推送到 GitHub
git push origin main
```

### 第 3 步：创建标签并推送

```bash
# 创建标签
git tag v1.0.1

# 推送标签到 GitHub（触发构建）
git push origin v1.0.1
```

### 第 4 步：监控构建

1. 访问 GitHub 仓库
2. 点击 "Actions" 选项卡
3. 查看构建进度
4. 构建完成后自动创建 Release

## 📊 工作流详解

### 环境设置

```yaml
strategy:
  matrix:
    os: [macos-latest, windows-latest, ubuntu-latest]
```

为三个平台并行构建，加快构建速度。

### 依赖安装

```yaml
- name: Setup Node.js
  uses: actions/setup-node@v3
  with:
    node-version: '18'
    cache: 'npm'

- name: Setup Python
  uses: actions/setup-python@v4
  with:
    python-version: '3.9'
    cache: 'pip'
```

自动安装 Node.js 和 Python 依赖。

### 构建命令

```yaml
- name: Build application
  run: ${{ matrix.build_cmd }}
```

根据平台执行不同的构建命令：
- macOS：`npm run dist-mac`
- Windows：`npm run dist-win`
- Linux：`npm run dist-linux`

### 上传构建物

```yaml
- name: Upload artifacts (macOS)
  uses: actions/upload-artifact@v3
  with:
    name: macos-builds
    path: |
      out/VORTEX Trading System-*.dmg
      out/VORTEX Trading System-*.zip
```

构建完成后上传到 GitHub Actions 工件存储。

### 自动发布

```yaml
- name: Create Release
  if: startsWith(github.ref, 'refs/tags/')
  uses: softprops/action-gh-release@v1
```

当推送标签时自动创建 Release 并上传文件。

## 🔧 配置选项

### 修改构建命令

如果需要修改构建命令，编辑 `.github/workflows/build.yml`：

```yaml
matrix:
  include:
    - os: macos-latest
      build_cmd: npm run dist-mac
    - os: windows-latest
      build_cmd: npm run dist-win
    - os: ubuntu-latest
      build_cmd: npm run dist-linux
```

### 添加环境变量

```yaml
env:
  NODE_ENV: production
  PYTHON_VERSION: 3.9
```

### 修改发布说明

编辑 `Create Release` 步骤中的 `body` 字段：

```yaml
body: |
  # Release Notes
  
  ## Features
  - Feature 1
  - Feature 2
```

## 📈 构建状态

### 查看构建状态

1. 访问 GitHub 仓库
2. 点击 "Actions" 选项卡
3. 查看最新的工作流运行

### 构建日志

点击工作流运行查看详细日志：
- 依赖安装日志
- 编译日志
- 打包日志
- 上传日志

### 构建失败排查

如果构建失败：

1. **查看日志**：查看失败步骤的日志
2. **检查依赖**：确保所有依赖已安装
3. **检查配置**：确保 `package.json` 和 `tsconfig.json` 配置正确
4. **检查脚本**：确保构建脚本存在且正确

## 🔐 安全性

### GitHub Token

工作流使用 `GITHUB_TOKEN` 自动创建 Release。此令牌由 GitHub 自动提供，无需手动配置。

### 代码签名（可选）

对于 macOS 应用，可以添加代码签名：

```yaml
- name: Sign macOS app
  if: runner.os == 'macOS'
  env:
    APPLE_ID: ${{ secrets.APPLE_ID }}
    APPLE_PASSWORD: ${{ secrets.APPLE_PASSWORD }}
    TEAM_ID: ${{ secrets.TEAM_ID }}
  run: |
    # 代码签名脚本
```

## 📊 构建时间

预计构建时间：

| 平台 | 首次构建 | 后续构建 |
|-----|---------|--------|
| macOS | 15-20 分钟 | 10-15 分钟 |
| Windows | 15-20 分钟 | 10-15 分钟 |
| Linux | 10-15 分钟 | 5-10 分钟 |
| **总计** | **40-55 分钟** | **25-40 分钟** |

## 🎯 后续步骤

### 1. 推送工作流文件

```bash
git add .github/workflows/build.yml
git commit -m "Add GitHub Actions workflow"
git push origin main
```

### 2. 创建标签触发构建

```bash
git tag v1.0.1
git push origin v1.0.1
```

### 3. 监控构建进度

访问 GitHub Actions 页面查看构建进度。

### 4. 验证 Release

构建完成后，访问 Release 页面验证所有文件已上传。

## 📝 常见问题

### Q: 如何手动触发构建？

A: 在 GitHub Actions 页面点击 "Run workflow" 按钮。

### Q: 如何修改构建命令？

A: 编辑 `.github/workflows/build.yml` 文件中的 `build_cmd` 字段。

### Q: 如何添加新平台？

A: 在 `matrix` 中添加新的平台配置。

### Q: 如何禁用自动发布？

A: 注释掉 `Create Release` 步骤。

### Q: 构建失败怎么办？

A: 查看构建日志并根据错误信息进行调整。

## 🔗 相关资源

- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [softprops/action-gh-release](https://github.com/softprops/action-gh-release)
- [actions/setup-node](https://github.com/actions/setup-node)
- [actions/setup-python](https://github.com/actions/setup-python)

## ✅ 检查清单

- [ ] 工作流文件已创建
- [ ] 工作流文件已推送到 GitHub
- [ ] 标签已创建并推送
- [ ] 构建已触发
- [ ] 构建已完成
- [ ] Release 已创建
- [ ] 所有文件已上传
- [ ] 发布说明已生成

---

**GitHub Actions 配置完成！** 🎉

现在每次推送标签时，GitHub 会自动为所有平台构建可执行文件并创建 Release。
