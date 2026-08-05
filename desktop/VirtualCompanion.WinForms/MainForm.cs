using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace VirtualCompanion.Desktop;

internal sealed class MainForm : Form
{
    private const string WebViewRuntimeDownloadUrl = "https://developer.microsoft.com/microsoft-edge/webview2/";

    // 无边框窗口的拖动（WM_NCHITTEST 常量）。
    private const int HtCaption = 0x0002;
    private const int WmNcLButtonDown = 0x00A1;

    // Windows 11 的 DWM 特性；不支持系统背景材质的版本静默回退为普通背景。
    private const int DwmwaSystemBackdropType = 38;
    private const int DwmwaWindowCornerPreference = 33;
    private const int DwmwaUseImmersiveDarkMode = 20;
    private const int BackdropNone = 1;
    private const int CornerRound = 2;
    private const int GripSize = 6;
    private const int WmSettingChange = 0x001A;
    private const int WmThemeChanged = 0x031A;

    private readonly CancellationTokenSource _shutdown = new();
    private readonly ServerHost _serverHost;
    private readonly NotifyIcon _trayIcon;
    private readonly RoundedTrayMenu _trayMenu;
    private readonly ToolStripMenuItem _petTrayItem;
    private readonly WebView2 _webView = new() { Visible = false };
    // WebView2 的子 HWND 在交互拉伸时可能晚一帧更新；用主题同色底板接住暴露区域。
    private readonly Panel _resizeBackdrop = new() { Dock = DockStyle.Fill, Visible = false };
    // 加载页用不透明纯色背景，模态弹窗移走后可以完整擦除旧内容。
    private readonly Panel _statusPanel = new() { Dock = DockStyle.Fill };
    private readonly LoadingIcon _iconBox;
    private readonly LoadingTitle _titleBox = new();
    private readonly LoadingCaption _copyrightLabel = new("Copyright © 2026 上海市彦瑞科技有限公司");
    private readonly LoadingLabel _statusLabel = new()
    {
        // 灰色小字状态提示（略深于版权行，保证可见）。
        ForeColor = Color.FromArgb(138, 141, 161),
        Font = new Font("Microsoft YaHei UI", 10.5F, FontStyle.Regular),
    };
    private readonly LoadingBar _loadingBar = new()
    {
        Dock = DockStyle.Bottom,
        Height = 8,
    };
    private readonly FlowLayoutPanel _actionsPanel = new()
    {
        AutoSize = true,
        FlowDirection = FlowDirection.LeftToRight,
        WrapContents = false,
        Anchor = AnchorStyles.None,
    };
    private readonly Button _retryButton = new() { Text = "重试", AutoSize = true, Visible = false };
    private readonly Button _openLogButton = new() { Text = "打开日志", AutoSize = true, Visible = false };
    private readonly Button _runtimeButton = new() { Text = "安装 WebView2", AutoSize = true, Visible = false };
    private readonly EdgeGrip[] _grips = new EdgeGrip[8];
    private readonly Size _mainSize;
    private bool _darkTheme;
    private bool _followSystemTheme = true;
    private bool _hostRunning;
    private bool _webViewInitialized;
    private bool _maximized;
    private bool _shellCommandListenerStarted;
    private bool _exitRequested;
    private bool _trayHintShown;
    private bool _petVisible;

    public MainForm(ShellOptions options)
    {
        _serverHost = new ServerHost(options);
        // 优先恢复前端上次选择的主题，让 WebView 出现前的加载页也不会闪成错误颜色。
        LoadSavedShellTheme();

        //Text = "虚拟陪伴";
        StartPosition = FormStartPosition.CenterScreen;
        // 加载页用较小的窗口，页面就绪后放大到主界面尺寸（见 ExpandToMainSize）。
        var loadingSize = ResolveInitialSize(new Size(1000, 680));
        _mainSize = ResolveInitialSize(new Size(1340, 900));
        MinimumSize = new Size(Math.Min(960, loadingSize.Width), Math.Min(640, loadingSize.Height));
        FormBorderStyle = FormBorderStyle.None;
        // 窗口边缘、WebView2 迟帧底板和无材质客户区统一使用主题纯色。
        BackColor = ResolveShellBackColor(_darkTheme);
        // 加载页纯色不透明，与窗体同色；WebView 就绪后隐藏该面板。
        _statusPanel.BackColor = BackColor;
        _resizeBackdrop.BackColor = BackColor;
        _webView.DefaultBackgroundColor = BackColor;
        // 注意：Form.Icon setter 直接引用对象（不克隆），因此必须 Clone 一份，
        // 否则 using 块结束时 appIcon 被释放，窗口显示时访问 Icon 会抛 ObjectDisposedException。
        using (var appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath))
        {
            _iconBox = new LoadingIcon((Icon)(appIcon ?? SystemIcons.Application).Clone());
            Icon = (Icon)(appIcon ?? SystemIcons.Application).Clone();
        }

        _trayMenu = new RoundedTrayMenu();
        _trayMenu.Items.Add("打开主窗口", null, (_, _) => RestoreMainWindow());
        _petTrayItem = new ToolStripMenuItem("打开桌宠");
        _petTrayItem.Click += async (_, _) => await TogglePetFromTrayAsync();
        _trayMenu.Items.Add(_petTrayItem);
        _trayMenu.Items.Add(new ToolStripSeparator());
        _trayMenu.Items.Add("退出程序", null, (_, _) => ExitApplication());
        _trayMenu.Opening += async (_, _) => await RefreshPetTrayStateAsync();
        ApplyTrayTheme();
        _trayIcon = new NotifyIcon
        {
            Icon = Icon,
            Text = "虚拟陪伴",
            ContextMenuStrip = _trayMenu,
            Visible = true,
        };
        _trayIcon.DoubleClick += (_, _) => RestoreMainWindow();

        BuildStatusPanel();
        ApplyLoadingTheme();
        BuildGrips();
        // 注意：ClientSize 赋值会同步触发 OnResize → LayoutGrips，
        // 因此必须放在 grips 创建之后。
        ClientSize = loadingSize;
        Controls.Add(_resizeBackdrop);
        Controls.Add(_webView);
        Controls.Add(_statusPanel);
        Controls.AddRange(_grips);

        DpiChanged += (_, _) => UpdateMaximizedBounds();
        Activated += (_, _) => RefreshLoadingSurface();
        Shown += (_, _) => StartHost();
        FormClosing += OnFormClosing;
        FormClosed += (_, _) => DisposeTrayIcon();
        _retryButton.Click += (_, _) => StartHost();
        _openLogButton.Click += (_, _) => OpenPath(_serverHost.ServerLogPath);
        _runtimeButton.Click += (_, _) => OpenPath(WebViewRuntimeDownloadUrl);
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        ApplyBackdrop();
        UpdateMaximizedBounds();
    }

    protected override void OnPaintBackground(PaintEventArgs e)
    {
        e.Graphics.Clear(BackColor);
    }

    protected override void WndProc(ref Message message)
    {
        base.WndProc(ref message);

        if (message.Msg is WmSettingChange or WmThemeChanged)
        {
            var systemDark = IsSystemDarkTheme();
            if (_followSystemTheme)
            {
                ApplyShellTheme(systemDark, followsSystem: true, persist: true);
            }
            PushSystemTheme(systemDark);
        }
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        var maximized = WindowState == FormWindowState.Maximized;
        if (maximized != _maximized)
        {
            _maximized = maximized;
            SetGripsVisible(!maximized);
            PushWindowState();
        }

        LayoutGrips();
        LayoutWebView();
    }

    private void BuildStatusPanel()
    {
        StyleActionButton(_retryButton);
        StyleActionButton(_openLogButton);
        StyleActionButton(_runtimeButton);
        _actionsPanel.Controls.Add(_retryButton);
        _actionsPanel.Controls.Add(_openLogButton);
        _actionsPanel.Controls.Add(_runtimeButton);

        // 图标 + 大标题区：手动布局（TableLayoutPanel 对 Anchor=None 控件会异常拉伸）。
        var content = new LoadingPanel { Dock = DockStyle.Fill };
        content.Resize += (_, _) =>
        {
            // 图标中心位于上方 38% 处（偏下），大标题在其下方、间隔拉开，
            // 版权小字在标题下方。
            var iconTop = Math.Max((int)(content.ClientSize.Height * 0.38) - _iconBox.Height / 2, 0);
            _iconBox.SetBounds(
                (content.ClientSize.Width - _iconBox.Width) / 2,
                iconTop,
                _iconBox.Width,
                _iconBox.Height
            );
            var titleTop = iconTop + _iconBox.Height + 28;
            _titleBox.SetBounds(0, titleTop, content.ClientSize.Width, _titleBox.Height);
            _copyrightLabel.SetBounds(
                0,
                titleTop + _titleBox.Height + 8,
                content.ClientSize.Width,
                _copyrightLabel.Height
            );
        };
        content.Controls.Add(_iconBox);
        content.Controls.Add(_titleBox);
        content.Controls.Add(_copyrightLabel);

        // 底部状态区：状态文字 → 操作按钮（加载条独立贴底，不在此容器内）。
        var bottom = new LoadingTableLayoutPanel
        {
            Dock = DockStyle.Bottom,
            AutoSize = true,
            ColumnCount = 1,
            RowCount = 2,
            Padding = new Padding(24, 0, 24, 12),
        };
        bottom.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        bottom.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        bottom.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        _statusLabel.Margin = new Padding(0, 0, 0, 8);
        bottom.Controls.Add(_statusLabel, 0, 0);
        bottom.Controls.Add(_actionsPanel, 0, 1);

        _statusPanel.Controls.Add(content);
        _statusPanel.Controls.Add(bottom);
        // 加载条最后添加：贴窗口最底部，宽度平铺整个 x 轴。
        _statusPanel.Controls.Add(_loadingBar);
    }

    private static void StyleActionButton(Button button)
    {
        button.FlatStyle = FlatStyle.Flat;
        button.FlatAppearance.BorderSize = 1;
        button.FlatAppearance.BorderColor = Color.FromArgb(190, 199, 255);
        button.FlatAppearance.MouseOverBackColor = Color.FromArgb(243, 244, 255);
        button.BackColor = Color.White;
        button.ForeColor = Color.FromArgb(89, 105, 212);
        button.Padding = new Padding(16, 6, 16, 6);
        button.Margin = new Padding(6);
        button.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
    }

    private void BuildGrips()
    {
        var specs = new (HitTest HitTest, Cursor Cursor)[]
        {
            (HitTest.Left, Cursors.SizeWE),
            (HitTest.Right, Cursors.SizeWE),
            (HitTest.Top, Cursors.SizeNS),
            (HitTest.Bottom, Cursors.SizeNS),
            (HitTest.TopLeft, Cursors.SizeNWSE),
            (HitTest.BottomRight, Cursors.SizeNWSE),
            (HitTest.TopRight, Cursors.SizeNESW),
            (HitTest.BottomLeft, Cursors.SizeNESW),
        };

        for (var index = 0; index < _grips.Length; index++)
        {
            _grips[index] = new EdgeGrip(specs[index].HitTest, specs[index].Cursor)
            {
                BackColor = BackColor,
            };
            _grips[index].ResizeStarted += (_, _) => SetResizeBackdropVisible(true);
            _grips[index].ResizeCompleted += (_, _) => SetResizeBackdropVisible(false);
        }
    }

    private void SetResizeBackdropVisible(bool visible)
    {
        if (_resizeBackdrop.Visible == visible)
        {
            return;
        }

        _resizeBackdrop.Visible = visible;
        if (visible)
        {
            _resizeBackdrop.SendToBack();
            _resizeBackdrop.Invalidate();
            _resizeBackdrop.Update();
        }
        else
        {
            Invalidate();
        }
        BringGripsToFront();
    }

    private void RefreshLoadingSurface()
    {
        if (!_statusPanel.Visible)
        {
            return;
        }

        _statusPanel.Invalidate(true);
        _statusPanel.Update();
    }

    private void SetGripsVisible(bool visible)
    {
        foreach (var grip in _grips)
        {
            grip.Visible = visible;
        }
    }

    private void BringGripsToFront()
    {
        foreach (var grip in _grips)
        {
            grip.BringToFront();
        }
    }

    private void LayoutGrips()
    {
        if (_grips[0] is null)
        {
            return;
        }

        var width = ClientSize.Width;
        var height = ClientSize.Height;

        // left, right, top, bottom, top-left, bottom-right, top-right, bottom-left
        _grips[0].SetBounds(0, GripSize, GripSize, height - 2 * GripSize);
        _grips[1].SetBounds(width - GripSize, GripSize, GripSize, height - 2 * GripSize);
        _grips[2].SetBounds(GripSize, 0, width - 2 * GripSize, GripSize);
        _grips[3].SetBounds(GripSize, height - GripSize, width - 2 * GripSize, GripSize);
        _grips[4].SetBounds(0, 0, GripSize, GripSize);
        _grips[5].SetBounds(width - GripSize, height - GripSize, GripSize, GripSize);
        _grips[6].SetBounds(width - GripSize, 0, GripSize, GripSize);
        _grips[7].SetBounds(0, height - GripSize, GripSize, GripSize);
    }

    // WebView2 的输入走 Chromium 内部通道，WinForms 控件无法可靠地在其上接收鼠标，
    // 因此普通窗口把 WebView2 四周让出 GripSize 边缘（露出窗体背景色边框），
    // 缩放把手放在该边缘区域；最大化时 WebView2 铺满、把手隐藏。
    private void LayoutWebView()
    {
        var inset = WindowState == FormWindowState.Maximized ? 0 : GripSize;
        _webView.SetBounds(inset, inset, ClientSize.Width - 2 * inset, ClientSize.Height - 2 * inset);
    }

    // 读取系统深浅色（Windows 应用模式）：1=浅色，0=深色。
    private static bool IsSystemDarkTheme()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            );
            return key?.GetValue("AppsUseLightTheme") is int value && value == 0;
        }
        catch
        {
            return false;
        }
    }

    private string ShellThemePath => Path.Combine(_serverHost.DataDirectory, "shell-theme.json");

    private void LoadSavedShellTheme()
    {
        var systemDark = IsSystemDarkTheme();
        _darkTheme = systemDark;

        try
        {
            if (!File.Exists(ShellThemePath))
            {
                return;
            }

            using var document = JsonDocument.Parse(File.ReadAllText(ShellThemePath));
            var root = document.RootElement;
            _followSystemTheme = !root.TryGetProperty("followsSystem", out var followsSystem)
                || followsSystem.ValueKind != JsonValueKind.False;
            if (!_followSystemTheme
                && root.TryGetProperty("dark", out var dark)
                && dark.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                _darkTheme = dark.GetBoolean();
            }
        }
        catch (IOException)
        {
        }
        catch (JsonException)
        {
        }
    }

    // 加载页配色跟随系统深浅（与前端星夜蓝黑深色主题同源）。
    private void ApplyLoadingTheme()
    {
        ApplyLoadingBackground(_statusPanel, BackColor);
        _titleBox.ApplyDark(_darkTheme);
        _copyrightLabel.ApplyDark(_darkTheme);
        _statusLabel.ApplyDark(_darkTheme);
        _loadingBar.ApplyDark(_darkTheme);
        ApplyActionButtonTheme(_retryButton);
        ApplyActionButtonTheme(_openLogButton);
        ApplyActionButtonTheme(_runtimeButton);
    }

    private static void ApplyLoadingBackground(Control root, Color color)
    {
        root.BackColor = color;
        foreach (Control child in root.Controls)
        {
            if (child is not Button)
            {
                ApplyLoadingBackground(child, color);
            }
        }
    }

    private void ApplyActionButtonTheme(Button button)
    {
        button.FlatAppearance.BorderColor = _darkTheme
            ? Color.FromArgb(94, 105, 180)
            : Color.FromArgb(190, 199, 255);
        button.FlatAppearance.MouseOverBackColor = _darkTheme
            ? Color.FromArgb(43, 46, 66)
            : Color.FromArgb(243, 244, 255);
        button.BackColor = _darkTheme ? Color.FromArgb(30, 32, 45) : Color.White;
        button.ForeColor = _darkTheme
            ? Color.FromArgb(166, 175, 255)
            : Color.FromArgb(89, 105, 212);
    }

    private void ApplyShellTheme(bool dark, bool followsSystem, bool persist)
    {
        _darkTheme = dark;
        _followSystemTheme = followsSystem;
        BackColor = ResolveShellBackColor(dark);
        _statusPanel.BackColor = BackColor;
        _resizeBackdrop.BackColor = BackColor;
        _webView.DefaultBackgroundColor = BackColor;
        foreach (var grip in _grips)
        {
            if (grip is not null)
            {
                grip.BackColor = BackColor;
                grip.Invalidate();
            }
        }
        ApplyLoadingTheme();
        ApplyTrayTheme();

        if (IsHandleCreated)
        {
            ApplyBackdrop();
        }
        Invalidate(true);

        if (!persist)
        {
            return;
        }

        try
        {
            File.WriteAllText(
                ShellThemePath,
                JsonSerializer.Serialize(new { followsSystem = _followSystemTheme, dark = _darkTheme })
            );
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static Color ResolveShellBackColor(bool dark) => dark
        ? Color.FromArgb(25, 26, 40)
        : Color.FromArgb(246, 247, 251);

    private void ApplyBackdrop()
    {
        // 禁用 Mica/Acrylic 等系统背景材质，仅保留 DWM 圆角；客户区由 WinForms
        // 使用 ResolveShellBackColor 返回的 Light/Dark 纯色完整绘制。
        var darkMode = _darkTheme ? 1 : 0;
        var backdrop = BackdropNone;
        var corner = CornerRound;
        _ = DwmSetWindowAttribute(Handle, DwmwaUseImmersiveDarkMode, ref darkMode, sizeof(int));
        _ = DwmSetWindowAttribute(
            Handle,
            DwmwaSystemBackdropType,
            ref backdrop,
            sizeof(int)
        );
        _ = DwmSetWindowAttribute(Handle, DwmwaWindowCornerPreference, ref corner, sizeof(int));
        Invalidate();
    }

    // 初始窗口尺寸按主屏工作区裁剪，避免在分辨率/DPI 较小的电脑上超出屏幕
    // （无边框窗口没有系统标题栏，超屏后将无法拖回）。
    // 注意：Screen.WorkingArea 是物理像素，ClientSize 是逻辑像素，需按 DPI 换算。
    private Size ResolveInitialSize(Size desired)
    {
        var area = Screen.PrimaryScreen?.WorkingArea;
        if (area is null)
        {
            return desired;
        }

        var scale = DeviceDpi / 96.0;
        var maxWidth = (int)(area.Value.Width / scale);
        var maxHeight = (int)(area.Value.Height / scale);
        return new Size(Math.Min(desired.Width, maxWidth), Math.Min(desired.Height, maxHeight));
    }

    private void UpdateMaximizedBounds()
    {
        if (IsHandleCreated)
        {
            MaximizedBounds = Screen.FromHandle(Handle).WorkingArea;
        }
    }

    private async void StartHost()
    {
        if (_hostRunning || _shutdown.IsCancellationRequested)
        {
            return;
        }

        _hostRunning = true;

        // 首启配置向导：密钥未配置时弹窗（可跳过，之后在「服务设置」页面补填）。
        // 放在服务启动前——保存后直接以新配置启动，无需走热重启协议。
        // 弹窗覆盖的是纯色不透明的加载页（_statusPanel），移走后可以完整擦除。
        if (!SetupDialog.IsConfigured(_serverHost.DataDirectory))
        {
            using var setupDialog = new SetupDialog(_serverHost.DataDirectory);
            setupDialog.ShowDialog(this);
            // 模态窗口移走后立即擦除加载页所有子控件表面，避免等待下一次普通 WM_PAINT。
            RefreshLoadingSurface();
        }

        ShowLoading("正在启动本地服务...");

        try
        {
            await _serverHost.RunAsync(HandleServerReadyAsync, ShowLoading, _shutdown.Token);
        }
        catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
        {
        }
        catch (Exception error)
        {
            LogStartupError(error);
            ShowError(error.Message, error is WebView2RuntimeNotFoundException);
        }
        finally
        {
            _hostRunning = false;
        }
    }

    private async Task HandleServerReadyAsync(bool restarting)
    {
        ShowLoading(restarting ? "服务已重启，正在恢复页面..." : "正在载入应用...");
        await EnsureWebViewAsync();

        if (restarting && _webView.CoreWebView2 is not null)
        {
            _webView.CoreWebView2.Reload();
        }
        else
        {
            _webView.Source = _serverHost.AppUri;
        }

        // 页面就绪：停止加载动画、隐藏加载面板（含加载条）、放大窗口到主界面尺寸。
        _loadingBar.Running = false;
        _statusPanel.Visible = false;
        ApplyBackdrop();
        ExpandToMainSize();
        _webView.Visible = true;
        BringGripsToFront();
        _webView.Focus();
        StartShellCommandListener();
    }

    private void StartShellCommandListener()
    {
        if (_shellCommandListenerStarted)
        {
            return;
        }

        _shellCommandListenerStarted = true;
        _ = ListenForShellCommandsAsync();
    }

    private async Task ListenForShellCommandsAsync()
    {
        while (!_shutdown.IsCancellationRequested)
        {
            try
            {
                var command = await _serverHost.WaitForShellCommandAsync(_shutdown.Token);
                if (command == "show-main")
                {
                    RestoreMainWindow();
                }
            }
            catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
            {
                return;
            }
            catch (HttpRequestException)
            {
                // Server 热重启期间连接会短暂中断，等待新进程就绪后继续监听。
                await Task.Delay(500, _shutdown.Token);
            }
            catch (TaskCanceledException) when (!_shutdown.IsCancellationRequested)
            {
                await Task.Delay(250, _shutdown.Token);
            }
            catch (JsonException)
            {
                await Task.Delay(250, _shutdown.Token);
            }
        }
    }

    private void RestoreMainWindow()
    {
        if (IsDisposed || _shutdown.IsCancellationRequested)
        {
            return;
        }
        if (InvokeRequired)
        {
            BeginInvoke(RestoreMainWindow);
            return;
        }

        ShowInTaskbar = true;
        if (!Visible)
        {
            Show();
        }
        if (WindowState == FormWindowState.Minimized)
        {
            WindowState = FormWindowState.Normal;
        }

        BringToFront();
        Activate();
        _ = SetForegroundWindow(Handle);
        _webView.Focus();
    }

    private void HideToTray()
    {
        ShowInTaskbar = false;
        Hide();
        if (_trayHintShown)
        {
            return;
        }

        _trayHintShown = true;
        _trayIcon.ShowBalloonTip(
            2500,
            "虚拟陪伴仍在运行",
            "主窗口已隐藏到系统托盘，桌宠会继续运行。",
            ToolTipIcon.Info
        );
    }

    private async Task TogglePetFromTrayAsync()
    {
        var actionName = _petVisible ? "关闭" : "打开";
        _petTrayItem.Enabled = false;
        try
        {
            if (_petVisible)
            {
                await _serverHost.HidePetAsync(_shutdown.Token);
                SetPetTrayState(false);
            }
            else
            {
                await _serverHost.ShowPetAsync(_shutdown.Token);
                SetPetTrayState(true);
            }
        }
        catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
        {
        }
        catch (HttpRequestException)
        {
            _trayIcon.ShowBalloonTip(
                2200,
                $"无法{actionName}桌宠",
                "本地服务暂时不可用，请稍后再试。",
                ToolTipIcon.Warning
            );
        }
        catch (TaskCanceledException)
        {
            _trayIcon.ShowBalloonTip(
                2200,
                $"无法{actionName}桌宠",
                "本地服务响应超时，请稍后再试。",
                ToolTipIcon.Warning
            );
        }
        finally
        {
            _petTrayItem.Enabled = true;
        }
    }

    private async Task RefreshPetTrayStateAsync()
    {
        try
        {
            SetPetTrayState(await _serverHost.IsPetVisibleAsync(_shutdown.Token));
        }
        catch (OperationCanceledException) when (_shutdown.IsCancellationRequested)
        {
        }
        catch (HttpRequestException)
        {
        }
        catch (TaskCanceledException)
        {
        }
        catch (JsonException)
        {
        }
    }

    private void SetPetTrayState(bool visible)
    {
        _petVisible = visible;
        _petTrayItem.Text = visible ? "关闭桌宠" : "打开桌宠";
    }

    private void ApplyTrayTheme()
    {
        _trayMenu.ApplyTheme(_darkTheme);
    }

    private void ExitApplication()
    {
        _exitRequested = true;
        Close();
    }

    private void DisposeTrayIcon()
    {
        _trayIcon.Visible = false;
        _trayIcon.Dispose();
        _trayMenu.Dispose();
    }

    // 加载页用小窗口，主界面就绪后放大到主界面尺寸并保持居中。
    private void ExpandToMainSize()
    {
        if (WindowState != FormWindowState.Normal || ClientSize == _mainSize)
        {
            return;
        }

        var area = Screen.FromHandle(Handle).WorkingArea;
        SetBounds(
            area.Left + (area.Width - _mainSize.Width) / 2,
            area.Top + (area.Height - _mainSize.Height) / 2,
            _mainSize.Width,
            _mainSize.Height
        );
    }

    private async Task EnsureWebViewAsync()
    {
        if (_webViewInitialized)
        {
            return;
        }

        _ = CoreWebView2Environment.GetAvailableBrowserVersionString();
        Directory.CreateDirectory(_serverHost.WebViewDataDirectory);
        var environment = await CoreWebView2Environment.CreateAsync(
            browserExecutableFolder: null,
            userDataFolder: _serverHost.WebViewDataDirectory
        );
        await _webView.EnsureCoreWebView2Async(environment);

        var core = _webView.CoreWebView2;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreDevToolsEnabled = Debugger.IsAttached;
        core.Settings.IsZoomControlEnabled = true;
        core.NavigationStarting += OnNavigationStarting;
        core.NavigationCompleted += (_, _) =>
        {
            PushWindowState();
            PushSystemTheme();
            // WebView2 在初始化/导航时会把自身 HWND 树提升到 Z 序顶层，
            // 每次都把缩放把手重新提到最上层，否则鼠标会被 WebView2 吃掉。
            BringGripsToFront();
        };
        core.NewWindowRequested += OnNewWindowRequested;
        core.PermissionRequested += OnPermissionRequested;
        core.WebMessageReceived += OnWebMessageReceived;
        // 注入脚本在文档创建瞬间执行，此时 <html> 元素可能尚未解析，
        // 因此立即尝试，失败则等 DOMContentLoaded 再补。
        await core.AddScriptToExecuteOnDocumentCreatedAsync(
            "(function(){function apply(){document.documentElement.classList.add('desktop-shell')}" +
            "if(document.documentElement)apply();else document.addEventListener('DOMContentLoaded',apply)})()"
        );
        _webViewInitialized = true;
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        if (args.Uri == "about:blank" || IsTrustedLocalUri(args.Uri))
        {
            return;
        }

        args.Cancel = true;
        OpenPath(args.Uri);
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        args.Handled = true;
        if (IsTrustedLocalUri(args.Uri))
        {
            _webView.CoreWebView2.Navigate(args.Uri);
        }
        else
        {
            OpenPath(args.Uri);
        }
    }

    private void OnPermissionRequested(object? sender, CoreWebView2PermissionRequestedEventArgs args)
    {
        if (args.PermissionKind == CoreWebView2PermissionKind.Microphone && IsTrustedLocalUri(args.Uri))
        {
            args.State = CoreWebView2PermissionState.Allow;
        }
    }

    private bool IsTrustedLocalUri(string value)
    {
        return Uri.TryCreate(value, UriKind.Absolute, out var uri)
            && uri.Scheme == Uri.UriSchemeHttp
            && uri.Host == "127.0.0.1"
            && uri.Port == _serverHost.Port;
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        string? type;
        bool? requestedDark = null;
        var followsSystem = false;
        try
        {
            using var document = JsonDocument.Parse(args.WebMessageAsJson);
            if (!document.RootElement.TryGetProperty("type", out var typeElement))
            {
                return;
            }
            type = typeElement.GetString();
            if (type == "app-theme"
                && document.RootElement.TryGetProperty("dark", out var dark)
                && dark.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                requestedDark = dark.GetBoolean();
                followsSystem = document.RootElement.TryGetProperty("followsSystem", out var follows)
                    && follows.ValueKind == JsonValueKind.True;
            }
        }
        catch (JsonException)
        {
            return;
        }

        switch (type)
        {
            case "drag":
                // 交给系统拖动窗口（顺带获得 Aero Snap 吸附）。
                ReleaseCapture();
                SendMessage(Handle, WmNcLButtonDown, HtCaption, IntPtr.Zero);
                break;
            case "minimize":
                WindowState = FormWindowState.Minimized;
                break;
            case "toggle-maximize":
                WindowState = WindowState == FormWindowState.Maximized
                    ? FormWindowState.Normal
                    : FormWindowState.Maximized;
                break;
            case "close":
                Close();
                break;
            case "get-state":
                PushWindowState();
                break;
            case "app-theme":
                if (requestedDark is not null)
                {
                    ApplyShellTheme(requestedDark.Value, followsSystem, persist: true);
                }
                break;
        }
    }

    private void PushWindowState()
    {
        if (_webView.CoreWebView2 is null)
        {
            return;
        }

        var payload = JsonSerializer.Serialize(new
        {
            type = "window-state",
            maximized = WindowState == FormWindowState.Maximized,
        });
        _webView.CoreWebView2.PostWebMessageAsJson(payload);
    }

    // 把系统深浅色推送给前端：主题偏好为「跟随系统」时，前端据此切换。
    private void PushSystemTheme(bool? dark = null)
    {
        if (_webView.CoreWebView2 is null)
        {
            return;
        }

        _webView.CoreWebView2.PostWebMessageAsJson(
            JsonSerializer.Serialize(new { type = "system-theme", dark = dark ?? IsSystemDarkTheme() })
        );
    }

    private void ShowLoading(string message)
    {
        _statusLabel.Text = message;
        _loadingBar.Running = true;
        _retryButton.Visible = false;
        _openLogButton.Visible = false;
        _runtimeButton.Visible = false;
        _statusPanel.Visible = true;
        ApplyBackdrop();
        _statusPanel.BringToFront();
    }

    private void LogStartupError(Exception error)
    {
        try
        {
            var logPath = Path.Combine(_serverHost.DataDirectory, "logs", "startup-error.log");
            Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
            File.AppendAllText(
                logPath,
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}]{Environment.NewLine}{error}{Environment.NewLine}{Environment.NewLine}"
            );
        }
        catch (IOException)
        {
        }
    }

    private void ShowError(string message, bool runtimeMissing)
    {
        _statusLabel.Text = message;
        _loadingBar.Running = false;
        _retryButton.Visible = true;
        _openLogButton.Visible = !runtimeMissing;
        _runtimeButton.Visible = runtimeMissing;
        _statusPanel.Visible = true;
        ApplyBackdrop();
        _statusPanel.BringToFront();
    }

    private static void OpenPath(string pathOrUrl)
    {
        try
        {
            Process.Start(new ProcessStartInfo(pathOrUrl) { UseShellExecute = true });
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "无法打开", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs args)
    {
        if (!_exitRequested && args.CloseReason == CloseReason.UserClosing)
        {
            args.Cancel = true;
            HideToTray();
            return;
        }

        _shutdown.Cancel();
        _serverHost.Stop();
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    private sealed class RoundedTrayMenu : ContextMenuStrip
    {
        private const int CornerRadius = 12;

        public RoundedTrayMenu()
        {
            AutoSize = true;
            ShowImageMargin = false;
            DropShadowEnabled = true;
            Padding = new Padding(6);
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
        }

        public void ApplyTheme(bool dark)
        {
            BackColor = dark ? Color.FromArgb(36, 38, 55) : Color.FromArgb(250, 250, 255);
            ForeColor = dark ? Color.FromArgb(232, 233, 242) : Color.FromArgb(37, 37, 54);
            Renderer = new RoundedTrayRenderer(dark);
            foreach (ToolStripItem item in Items)
            {
                item.ForeColor = ForeColor;
                if (item is ToolStripMenuItem)
                {
                    item.Padding = new Padding(10, 6, 10, 6);
                }
            }
            Invalidate(true);
        }

        protected override void OnSizeChanged(EventArgs e)
        {
            base.OnSizeChanged(e);
            if (Width <= 0 || Height <= 0)
            {
                return;
            }
            using var path = CreateRoundedPath(new Rectangle(0, 0, Width, Height), CornerRadius);
            var oldRegion = Region;
            Region = new Region(path);
            oldRegion?.Dispose();
        }

        private static GraphicsPath CreateRoundedPath(Rectangle bounds, int radius)
        {
            var diameter = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
            path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
            path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return path;
        }

        private sealed class RoundedTrayRenderer : ToolStripProfessionalRenderer
        {
            private readonly Color _background;
            private readonly Color _border;
            private readonly Color _hover;
            private readonly Color _separator;

            public RoundedTrayRenderer(bool dark)
            {
                RoundedEdges = true;
                _background = dark ? Color.FromArgb(36, 38, 55) : Color.FromArgb(250, 250, 255);
                _border = dark ? Color.FromArgb(78, 83, 125) : Color.FromArgb(190, 199, 255);
                _hover = dark ? Color.FromArgb(52, 56, 82) : Color.FromArgb(237, 239, 255);
                _separator = dark ? Color.FromArgb(62, 65, 84) : Color.FromArgb(225, 227, 239);
            }

            protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e)
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                var bounds = new Rectangle(0, 0, e.ToolStrip.Width - 1, e.ToolStrip.Height - 1);
                using var path = CreateRoundedPath(bounds, CornerRadius);
                using var brush = new SolidBrush(_background);
                using var pen = new Pen(_border);
                e.Graphics.FillPath(brush, path);
                e.Graphics.DrawPath(pen, path);
            }

            protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e)
            {
                // 背景绘制时已画圆角边框。
            }

            protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e)
            {
                if (!e.Item.Selected)
                {
                    return;
                }
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                var bounds = new Rectangle(2, 1, e.Item.Width - 4, e.Item.Height - 2);
                using var path = CreateRoundedPath(bounds, 7);
                using var brush = new SolidBrush(_hover);
                e.Graphics.FillPath(brush, path);
            }

            protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e)
            {
                using var pen = new Pen(_separator);
                var y = e.Item.Height / 2;
                e.Graphics.DrawLine(pen, 8, y, e.Item.Width - 8, y);
            }
        }
    }

    /// <summary>加载页容器：每次重绘都擦除背景，避免模态窗口移走后留下旧合成帧。</summary>
    private sealed class LoadingPanel : Panel
    {
        public LoadingPanel()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint, true);
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
        }
    }

    /// <summary>加载页表格容器：用于单元格居中，并负责擦除自己的背景。</summary>
    private sealed class LoadingTableLayoutPanel : TableLayoutPanel
    {
        public LoadingTableLayoutPanel()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint, true);
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
        }
    }

    /// <summary>
    /// 加载页大标题：逐字绘制（GDI+ 不支持 letter-spacing），
    /// 字间距均匀，整行横跨 x 轴（两端对齐）。不绘制背景，露出窗体背景色。
    /// </summary>
    private sealed class LoadingTitle : Control
    {
        private const float FontSize = 26F;
        private const float Spacing = 10F;
        private static readonly Color TitleColorLight = Color.FromArgb(77, 78, 97);
        private static readonly Color TitleColorDark = Color.FromArgb(182, 186, 205);

        public LoadingTitle()
        {
            Font = new Font("Microsoft YaHei UI", FontSize, FontStyle.Bold);
            ForeColor = TitleColorLight;
            Height = 56;
            // 宽度由外部手动布局（横跨整条 x 轴）。
            SetStyle(ControlStyles.UserPaint, true);
        }

        public void ApplyDark(bool dark)
        {
            ForeColor = dark ? TitleColorDark : TitleColorLight;
            Invalidate();
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            const string text = "虚拟陪伴助手";
            if (text.Length == 0)
            {
                return;
            }

            // 用 GDI（TextRenderer）逐字绘制：ClearType 在透明合成表面上会失效，
            // 且 GDI+ 的 DrawString 不支持 letter-spacing，需要手动排版。
            var charWidths = new float[text.Length];
            var totalCharWidth = 0f;
            for (var i = 0; i < text.Length; i++)
            {
                charWidths[i] = TextRenderer.MeasureText(g, text[i].ToString(), Font).Width;
                totalCharWidth += charWidths[i];
            }

            // 居中布局：整体水平居中，字间距固定且较小。
            var totalWidth = totalCharWidth + Spacing * (text.Length - 1);
            var x = Math.Max((Width - totalWidth) / 2f, 0f);
            var lineHeight = TextRenderer.MeasureText(g, "阿", Font).Height;
            var y = (Height - lineHeight) / 2f;

            for (var i = 0; i < text.Length; i++)
            {
                TextRenderer.DrawText(g, text[i].ToString(), Font, new Point((int)x, (int)y), ForeColor);
                x += charWidths[i] + Spacing;
            }
        }
    }

    /// <summary>加载页版权小字：单行居中、灰色弱化，不绘制背景（露出窗体背景色）。</summary>
    private sealed class LoadingCaption : Control
    {
        private static readonly Color CaptionLight = Color.FromArgb(169, 173, 188);
        private static readonly Color CaptionDark = Color.FromArgb(109, 112, 137);
        private readonly string _text;

        public LoadingCaption(string text)
        {
            _text = text;
            Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
            ForeColor = CaptionLight;
            Height = 20;
            SetStyle(ControlStyles.UserPaint, true);
        }

        public void ApplyDark(bool dark)
        {
            ForeColor = dark ? CaptionDark : CaptionLight;
            Invalidate();
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            TextRenderer.DrawText(
                g,
                _text,
                Font,
                ClientRectangle,
                ForeColor,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter
            );
        }
    }

    /// <summary>
    /// 自绘图标：不绘制背景（露出窗体背景色），直接绘制高清 PNG。
    /// 注意：不能开启 OptimizedDoubleBuffer —— 双缓冲通过 GDI BitBlt 合成到屏幕，
    /// 不支持 alpha 通道，透明像素会变成黑色（之前黑底的真正原因）。
    /// </summary>
    private sealed class LoadingIcon : Control
    {
        private readonly Image _image;

        public LoadingIcon(Icon fallbackIcon)
        {
            Size = new Size(196, 196);
            Anchor = AnchorStyles.None; // TableLayoutPanel cell 中居中
            SetStyle(ControlStyles.UserPaint, true);

            _image = LoadImage(fallbackIcon);
        }

        private static Image LoadImage(Icon fallbackIcon)
        {
            var pngPath = ResolvePngPath();
            if (pngPath is not null)
            {
                // 缩放到显示尺寸后释放原图，避免 9.8MB 高清图常驻内存。
                using var source = Image.FromFile(pngPath);
                return new Bitmap(source, 196, 196);
            }

            // 回退：Icon.ToBitmap() 会把 ico 的 mask 正确转换为 alpha 位图。
            using (fallbackIcon)
            {
                return fallbackIcon.ToBitmap();
            }
        }

        private static string? ResolvePngPath()
        {
            foreach (var candidate in new[]
            {
                Path.Combine(AppContext.BaseDirectory, "ico.png"),
                Path.Combine(Environment.CurrentDirectory, "ico.png"),
            })
            {
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }

            // 开发调试：exe 位于 bin\Debug\...\win-x64，向上查找项目根的 ico.png。
            var directory = new DirectoryInfo(AppContext.BaseDirectory);
            for (var i = 0; i < 6 && directory is not null; i++)
            {
                var candidate = Path.Combine(directory.FullName, "ico.png");
                if (File.Exists(candidate))
                {
                    return candidate;
                }
                directory = directory.Parent;
            }

            return null;
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            e.Graphics.DrawImage(_image, new Rectangle(0, 0, Width, Height));
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _image.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    /// <summary>自绘文字：不绘制背景（露出窗体背景色），避免 Label 透明链/双缓冲产生的黑底。</summary>
    private sealed class LoadingLabel : Control
    {
        private static readonly Color TextLight = Color.FromArgb(138, 141, 161);
        private static readonly Color TextDark = Color.FromArgb(130, 133, 156);
        private static readonly Color BgLight = Color.FromArgb(246, 247, 251);
        private static readonly Color BgDark = Color.FromArgb(25, 26, 40);
        private Color _bgColor = BgLight;

        public LoadingLabel()
        {
            Anchor = AnchorStyles.None; // TableLayoutPanel cell 中居中
            SetStyle(ControlStyles.UserPaint, true);
            ForeColor = TextLight;
            // 不用 AutoSize（自绘控件 + TableLayoutPanel 下 AutoSize 重算不可靠），
            // 改为文本变化时手动重算尺寸。
            UpdateSize();
        }

        public void ApplyDark(bool dark)
        {
            ForeColor = dark ? TextDark : TextLight;
            _bgColor = dark ? BgDark : BgLight;
            Invalidate();
        }

        protected override void OnTextChanged(EventArgs e)
        {
            base.OnTextChanged(e);
            UpdateSize();
        }

        private void UpdateSize()
        {
            var size = TextRenderer.MeasureText(Text, Font, new Size(640, 0), TextFormatFlags.WordBreak);
            Size = size;
            Invalidate();
        }

        protected override void OnPaintBackground(PaintEventArgs e)
        {
            // 填充与窗体背景一致的颜色（与窗体背景色一致，深浅主题下几乎无差）。
            // 若不擦除旧内容，状态文字切换时会新旧叠加（字堆叠）。
            e.Graphics.Clear(_bgColor);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            TextRenderer.DrawText(
                e.Graphics,
                Text,
                Font,
                ClientRectangle,
                ForeColor,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak
            );
        }
    }
}
