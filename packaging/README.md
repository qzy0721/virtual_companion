# One-folder 发布清单

运行 `scripts/build_package.ps1` 后，发布目录为 `dist/VirtualCompanion/`，分发时需要保留整个目录。

```text
dist/VirtualCompanion/
├── VirtualCompanion.exe        # WinForms + WebView2 桌面外壳
├── *.dll                       # 自包含 .NET 运行文件
├── runtimes/                   # WebView2Loader
└── server/
    ├── VirtualCompanion.Server.exe
    └── _internal/              # PyInstaller Python 服务与只读资源
```

## 构建命令

```powershell
./scripts/build_package.ps1
```

可选参数：

- `-RunAfterBuild`：构建完成后启动桌面外壳。
- `-CleanUserData`：显式删除 `%LOCALAPPDATA%\VirtualCompanion`，默认不会删除用户数据。
- `-CopyProjectEnv`：将源码目录的 `.env` 复制到用户数据目录，仅用于本机测试，不会放入分发包。
- `-OutputDirectory <path>`：修改发布输出根目录。

## Inno Setup 安装包

在 one-folder 构建完成后执行：

```powershell
./scripts/build_installer.ps1 -AppVersion 0.1.0
```

安装包输出到 `dist/installer/VirtualCompanion-Setup-0.1.0.exe`。使用
`-RebuildApplication` 可先重新构建 Vue、Python sidecar 和 WinForms one-folder；使用
`-IsccPath <path>` 可显式指定 `ISCC.exe`。

安装器默认按当前用户安装到
`%LOCALAPPDATA%\Programs\VirtualCompanion`，无需管理员权限。卸载只删除程序文件，
保留 `%LOCALAPPDATA%\VirtualCompanion` 中的配置、角色、对话、日志和参考音频。

## 运行结构

桌面外壳选择端口并启动 `server/VirtualCompanion.Server.exe --server --port <port>`，健康检查通过后在 WebView2 中打开 `/app/`。设置保存后，sidecar 使用退出码 `75` 请求重启，外壳保持窗口与端口不变并重新拉起服务。

WebView2 用户数据保存到 `%LOCALAPPDATA%\VirtualCompanion\WebView2`。外壳使用 Evergreen WebView2 Runtime，不把 250MB 以上的 Fixed Version Runtime 放进 one-folder。Windows 11 通常已预装 Evergreen Runtime；缺失时外壳会显示安装入口。

- WebView2 Runtime 分发说明：https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
- WebView2 Runtime 下载：https://developer.microsoft.com/microsoft-edge/webview2/

## 用户可写数据

默认位置为 `%LOCALAPPDATA%\VirtualCompanion\`，可通过进程环境变量 `APP_DATA_DIR` 修改：

- `.env`：首次运行时由包内 `.env.example` 复制；已有文件不会覆盖。
- `prompts/`：首次复制并在升级时补充新文件，不覆盖用户修改。
- `backend/data/`：角色、历史和长期记忆。
- `reference_audio/`：默认与用户上传的参考音频。
- `temp_tts/`：临时合成音频。
- `logs/server.log`：当前 sidecar 运行日志。
- `WebView2/`：桌面外壳浏览器数据。

语音识别仅保留 `WS /api/asr/stream`，WebView2 只为当前 `127.0.0.1:<port>` 页面放行麦克风权限。
