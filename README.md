# 虚拟陪伴系统

一个面向全年龄用户的本地优先虚拟陪伴应用。它将流式 AI 对话、实时语音输入、基于角色音色复刻的TTS语音合成、长期记忆机制与 VRM 3D 角色放在同一条交互链路中，并提供了多种快速角色创建路径，既适合日常陪伴，也适合二次元角色互动与个人化角色创作。

项目不是一个远程托管的聊天网站：桌面版本会在本机启动 FastAPI 服务，并通过 WinForms + WebView2 显示 Vue 前端。用户的配置、角色、聊天记录和参考音频默认保存在本机用户目录。

## 能力概览

- 流式对话：OpenAI 兼容的 LLM 接口，前端按帧呈现增量文本。
- 3D 陪伴：使用 Three.js 搭建3D舞台展示 VRM 角色模型，并支持基于 AI 控制的表情切换以及 VRMA 身体动作与语音口型。
- 实时 ASR：浏览器 PCM 流经本地 WebSocket 转发到 DashScope，返回中间和最终识别文本，用户可在发送前编辑。
- TTS 与音色：角色级支持 CosyVoice 与 Qwen3-TTS 两条声音复刻链路，并按句排队播放；未启用音色时仍可进行纯文字对话。
- 角色管理：支持手写提示词、LLM 生成提示词，或带百度百科资料辅助创建；参考音频会托管到应用数据目录。
- 长期记忆：从长对话历史中提炼摘要，并在后续对话中作为上下文注入。
- 设置页：图形化维护 LLM、DashScope 与 OSS 配置；保存后桌面壳会自动重启本地服务。

## 快速开始

### 使用安装包

运行 [VirtualCompanion-Setup-0.1.0.exe](dist/installer/VirtualCompanion-Setup-0.1.0.exe) 安装即可。安装器默认安装到：

```text
%LOCALAPPDATA%\Programs\VirtualCompanion
```

首次启动后，打开设置页填写配置：

- `LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL`
- `DASHSCOPE_API_KEY`，可选 `DASHSCOPE_WORKSPACE_ID`
- 使用 CosyVoice 时还需 `OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_BUCKET_NAME`

默认角色会在首次启动时以本地提示词创建，因此只配置 LLM 时也可以先进行文字对话。Qwen3-TTS 可直接提交本地参考音频，不需要用户购买 OSS；CosyVoice 效果通常更稳定，但声音复刻仍需 OSS 公网音频地址。每个角色可独立选择语音引擎。

桌面版本不需要安装 Python 或 .NET 8，但需要 Microsoft Edge WebView2 Evergreen Runtime。Windows 11 通常已包含它；缺失时桌面壳会显示安装入口。

### 从源码运行

前置条件：Windows、Python 3.10（当前项目使用 Anaconda 管理环境）、Node.js 18+。构建桌面壳时还需要 .NET 8 SDK；制作安装包需要 Inno Setup。
#### 1. 克隆源码

#### 2. 创建 Python 虚拟环境
在项目根目录下：
```powershell
# 创建并激活虚拟环境
conda create -n VirtualCompanion python=3.11 -y
conda activate VirtualCompanion

# 安装 Python 依赖
pip install -r requirements.txt
```
>不建议使用 conda install 下载相关包。本项目需要的 curl-cffi 库在 conda 的 defaults、conda-forge 这两个频道似乎都找不到，会导致报错，只有pip能正常下载。

#### 3. 前端依赖与构建

在项目根目录下：
```powershell
cd web
npm install
npm run build
cd ..
```
#### 4. 复制并填写配置
在项目根目录下：
```powershell
Copy-Item .env.example .env
```

#### 5. 启动本地服务，并在可用后打开浏览器
在项目根目录下：
```powershell
./scripts/quickstart.ps1
```

开发时访问 `http://127.0.0.1:<端口>/app/`；端口由 `.env` 的 `APP_PORT` 决定，留空时从 8000 起自动选择可用端口。Swagger 文档位于 `/docs`。

## 数据与隐私

发布版的用户可写数据位于 `%LOCALAPPDATA%\VirtualCompanion\`：

- `.env`：API Key 与服务配置。
- `backend/data/`：角色、聊天历史和长期记忆。
- `prompts/`：可自行修改的提示词文件。
- `reference_audio/`：默认和用户选择的参考音频。
- `logs/server.log`：本地服务完整日志。
- `WebView2/`：内嵌浏览器用户数据。

安装包升级或卸载不会主动删除该目录。请妥善保护 Windows 用户账户；`.env` 以明文保存，且语音、TTS、角色注册和 LLM 请求会按所填服务商配置发送到相应云端。

## 项目结构

```text
backend/                       FastAPI 路由、服务编排与数据模型
web/                           Vue 3 + Vite 前端、Three.js/VRM 舞台
desktop/                       WinForms + WebView2 桌面壳
prompts/                       可编辑的角色、记忆和表情提示词
reference_audio/               默认参考音频
packaging/                     PyInstaller 与 Inno Setup 打包定义
scripts/                       开发、one-folder 和安装包构建脚本
```

更完整的设计、数据流、接口分层、风险与演进建议见 [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md)。接口字段与实时事件格式应以运行中服务的 `/docs` 为准。

## 构建与发布

生成 WinForms + Python one-folder 目录：

```powershell
./scripts/build_package.ps1
```

生成安装包：

```powershell
./scripts/build_installer.ps1 -AppVersion x.y.z
```

一次性重建前端、sidecar、桌面壳并生成安装包：

```powershell
./scripts/build_installer.ps1 -AppVersion 0.2.0 -RebuildApplication
```

发布细节见 [packaging/README.md](packaging/README.md)。当前安装包未做 Authenticode 代码签名，公开发布前建议加入签名和时间戳流程；当前 Inno Setup 7 编译器也提示为非商业使用版本，商业分发前应确认授权条件。

## 当前边界

- 系统面向单机单用户；角色、历史与记忆使用 JSON 文件持久化，不是多用户或高并发服务。
- 云端能力依赖第三方服务的可用性、额度与模型行为。
- 角色设定与长期记忆属于提示词上下文，不能替代可靠的事实存储、家长监护或专业建议。
- 项目当前只面向 Windows x64 桌面发行版。
