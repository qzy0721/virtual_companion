# 虚拟陪伴系统（Virtual Companion）

一个本地优先的 Windows 虚拟陪伴应用：把流式 AI 对话、实时语音、角色音色复刻、长期记忆、VRM 3D 角色和透明桌宠放在同一条交互链路中。

主界面用于完整的角色、模型与对话管理；桌宠使用 CEF 离屏渲染与 Win32 透明窗口，在桌面上提供轻量的 3D 陪伴和对话入口。用户的配置、角色、聊天记录与参考音频默认保存于本机。
>详细介绍参考[官网](http://106.14.135.2/index.html)
>由于域名备案没有完成现在只能纯ip也没有证书...
## 功能

- **流式 AI 对话**：兼容 OpenAI 风格接口，回复逐段呈现。
- **VRM 3D 舞台**：基于 Three.js 与 VRM 的角色展示，支持表情、VRMA 动作和语音口型驱动。
- **透明桌宠**：CEF + Win32 实现独立透明悬浮窗；可从主界面启动、隐藏和恢复，右键菜单支持进入对话、打开主窗口和退出桌宠。
- **主窗口与桌宠同步**：切换模型、主题、角色上下文、表情与动作会同步至桌宠；桌宠可单独设置舞台宽度、高度及默认待机动作。
- **实时语音**：浏览器 PCM 流通过本地 WebSocket 转发至 DashScope 实时识别，识别结果可编辑后发送。
- **角色声音**：支持 CosyVoice 和 Qwen3-TTS 两条音色复刻链路，并按句播放；不配置语音能力时仍可使用文字对话。
- **角色与记忆**：支持手写或 AI 辅助创建角色、百度百科资料辅助，以及对长对话的长期记忆摘要。
- **本地设置与数据**：通过设置页维护 LLM、DashScope、OSS 和桌宠偏好；敏感配置保存在本地 `.env`。

## 快速开始

### 使用安装包

运行 [VirtualCompanion-Setup-0.2.0.exe](http://106.14.135.2/pages/download.html) 安装。默认安装目录为：

```text
%LOCALAPPDATA%\Programs\VirtualCompanion
```

首次启动后，在设置页填写所需配置：

- `LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL`
- `DASHSCOPE_API_KEY`；如使用业务空间，可填写 `DASHSCOPE_WORKSPACE_ID`
- 使用 CosyVoice 音色复刻时，还需要 `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_BUCKET_NAME`

Windows 11 通常自带 Microsoft Edge WebView2 Evergreen Runtime；若缺失，主窗口会提供安装入口。桌宠额外使用随 Python 服务打包的 CEF 运行时。

### 从源码运行

前置条件：Windows、Python 3.11、Node.js 18+。构建桌面发行版还需要 .NET 8 SDK；制作安装包需要 Inno Setup。

1. 安装 Python 依赖。项目当前开发环境为 `virtual_accompany`：

   ```powershell
   & "D:\Anaconda\envs\virtual_accompany\python.exe" -m pip install -r requirements.txt
   ```

2. 构建前端：

   ```powershell
   Set-Location web
   npm install
   npm run build
   Set-Location ..
   ```

3. 创建并填写本地配置：

   ```powershell
   Copy-Item .env.example .env
   ```

4. 启动后端和桌宠：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\start_pet.ps1
   ```

   脚本会启动 FastAPI 服务，再打开 CEF 桌宠。主界面可访问 `http://127.0.0.1:<端口>/app/`；端口由 `.env` 中的 `APP_PORT` 决定，留空时从 `8000` 起自动选择可用端口。

如只需启动后端服务，可使用：

```powershell
& "D:\Anaconda\envs\virtual_accompany\python.exe" backend\main.py
```

## 桌宠使用说明

在主界面启动桌宠后，3D 模型会显示在独立的透明悬浮窗中。右键模型舞台可以：

- 开始或退出对话模式；
- 打开主窗口；
- 隐藏／恢复或退出桌宠。

关闭主窗口时，程序会保留在系统托盘，桌宠继续运行。若希望完全结束桌宠，可使用桌宠右键菜单或托盘菜单中的“关闭桌宠”；之后可再次通过主界面打开。

设置页可调整桌宠舞台宽度与高度（与模型缩放不同），并从可用 VRMA 动作中选择默认待机动作。上传或切换主界面的 VRM 模型后，系统会将模型同步给桌宠。

## API 文档

后端启动后，交互式 Swagger API 文档位于：

```text
http://127.0.0.1:<端口>/docs
```

也就是说，**API 文档就在后端网页的 `/docs` 路径**。接口字段、请求示例与实时事件格式请以该页面显示内容为准；仓库中的 [api_docs.md](api_docs.md) 可作为补充参考。

## 数据与隐私

发行版默认将可写数据存放在：

```text
%LOCALAPPDATA%\VirtualCompanion\
```

主要包括：

- `.env`：API Key 和服务配置；
- `backend/data/`：角色、聊天记录与长期记忆；
- `pet-preferences.json`：桌宠尺寸与待机动作；
- `pet_models/`：同步给桌宠的当前 VRM 模型；
- `reference_audio/`：默认与用户选择的参考音频；
- `temp_tts/`、`logs/server.log`、`WebView2/`：运行时临时文件、日志与主窗口浏览器数据。

安装包升级或卸载不会主动删除这些数据。`.env` 以明文保存；LLM、ASR、TTS、角色注册等请求会发送至你配置的相应服务商，请妥善保护本机账户和密钥。

## 项目结构

```text
backend/                       FastAPI 路由、服务编排、数据模型和桌宠管理服务
web/                           Vue 3 + Vite 前端、Three.js/VRM 主舞台与桌宠页面
pet_window.py                  CEF 离屏渲染 + Win32 透明桌宠窗口
desktop/                       WinForms + WebView2 主窗口与系统托盘
prompts/                       可编辑的角色、记忆和表情提示词
reference_audio/               默认参考音频
packaging/                     PyInstaller 与 Inno Setup 打包定义
scripts/                       开发启动、构建和安装包脚本
static-display-page/           独立的项目展示静态站点
```

## 构建与发布

生成 WinForms 主窗口和 Python 服务的 one-folder 发行目录：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_package.ps1
```

生成安装包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 -AppVersion 0.2.0
```

需要重建前端、Python sidecar、主窗口后再打包时：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 -AppVersion 0.2.0 -RebuildApplication
```

发布目录结构、用户数据迁移与 WebView2 说明见 [packaging/README.md](packaging/README.md)。

## 当前边界

- 当前面向 Windows x64 的单机单用户使用场景；角色、历史与记忆基于 JSON 持久化，不适用于多用户或高并发服务。
- AI 与语音能力依赖第三方服务的可用性、额度与模型行为。
- 角色设定和长期记忆属于提示词上下文，不能替代可靠事实存储、家长监护或专业建议。
- 安装包尚未进行 Authenticode 代码签名，公开发布前建议加入签名与时间戳流程。
