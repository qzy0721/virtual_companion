using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace VirtualCompanion.Desktop;

internal sealed class ServerHost : IDisposable
{
    private const int RestartExitCode = 75;
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(2) };
    private readonly HttpClient _shellHttpClient = new() { Timeout = TimeSpan.FromSeconds(30) };
    private readonly string _serverExecutable;
    private readonly string _shellControlToken = Guid.NewGuid().ToString("N");
    private Process? _serverProcess;

    public ServerHost(ShellOptions options)
    {
        DataDirectory = ResolveDataDirectory();
        Directory.CreateDirectory(DataDirectory);
        _serverExecutable = ResolveServerExecutable(options.ServerPath);
        Port = ResolvePort(options.Port, DataDirectory);
        _shellHttpClient.DefaultRequestHeaders.Add("X-Shell-Control-Token", _shellControlToken);
    }

    public string DataDirectory { get; }
    public string ServerLogPath => Path.Combine(DataDirectory, "logs", "server.log");
    public string WebViewDataDirectory => Path.Combine(DataDirectory, "WebView2");
    public int Port { get; }
    public Uri AppUri => new($"http://127.0.0.1:{Port}/app/");

    public async Task RunAsync(
        Func<bool, Task> onReady,
        Action<string> onStatus,
        CancellationToken cancellationToken
    )
    {
        var restarting = false;

        while (!cancellationToken.IsCancellationRequested)
        {
            onStatus(restarting ? "正在应用设置并重启服务..." : "正在启动本地服务...");
            var exitCode = await RunOneProcessAsync(onReady, restarting, cancellationToken);

            if (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            if (exitCode != RestartExitCode)
            {
                throw new InvalidOperationException(
                    $"本地服务异常退出（代码 {exitCode}）。请查看日志：{ServerLogPath}"
                );
            }

            restarting = true;
            await Task.Delay(350, cancellationToken);
        }
    }

    public void Stop()
    {
        TryStopProcess(_serverProcess);
    }

    public async Task<string?> WaitForShellCommandAsync(CancellationToken cancellationToken)
    {
        var uri = new Uri($"http://127.0.0.1:{Port}/api/shell/next?timeout_seconds=20");
        using var response = await _shellHttpClient.GetAsync(uri, cancellationToken);
        if (response.StatusCode == HttpStatusCode.NoContent)
        {
            return null;
        }

        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        return document.RootElement.TryGetProperty("command", out var command)
            ? command.GetString()
            : null;
    }

    public async Task HidePetAsync(CancellationToken cancellationToken)
    {
        var uri = new Uri($"http://127.0.0.1:{Port}/api/pet/quit");
        using var response = await _httpClient.PostAsync(uri, content: null, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    public async Task ShowPetAsync(CancellationToken cancellationToken)
    {
        var uri = new Uri($"http://127.0.0.1:{Port}/api/pet/open");
        using var response = await _httpClient.PostAsync(uri, content: null, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    public async Task<bool> IsPetVisibleAsync(CancellationToken cancellationToken)
    {
        var uri = new Uri($"http://127.0.0.1:{Port}/api/pet/status");
        using var response = await _httpClient.GetAsync(uri, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        return document.RootElement.TryGetProperty("visible", out var visible)
            && visible.ValueKind == JsonValueKind.True;
    }

    public void Dispose()
    {
        Stop();
        _serverProcess?.Dispose();
        _httpClient.Dispose();
        _shellHttpClient.Dispose();
    }

    private async Task<int> RunOneProcessAsync(
        Func<bool, Task> onReady,
        bool restarting,
        CancellationToken cancellationToken
    )
    {
        using var process = StartServerProcess();
        _serverProcess = process;

        try
        {
            await WaitUntilReadyAsync(process, cancellationToken);
            await onReady(restarting);
            await process.WaitForExitAsync(cancellationToken);
            return process.ExitCode;
        }
        catch
        {
            TryStopProcess(process);
            throw;
        }
        finally
        {
            if (ReferenceEquals(_serverProcess, process))
            {
                _serverProcess = null;
            }
        }
    }

    private Process StartServerProcess()
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = _serverExecutable,
            WorkingDirectory = Path.GetDirectoryName(_serverExecutable)!,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("--server");
        startInfo.ArgumentList.Add("--port");
        startInfo.ArgumentList.Add(Port.ToString());
        startInfo.Environment["APP_DATA_DIR"] = DataDirectory;
        startInfo.Environment["SHELL_CONTROL_TOKEN"] = _shellControlToken;
        startInfo.Environment.Remove("VIRTUAL_COMPANION_LOG_PREPARED");

        return Process.Start(startInfo)
            ?? throw new InvalidOperationException("无法启动本地服务进程");
    }

    private async Task WaitUntilReadyAsync(Process process, CancellationToken cancellationToken)
    {
        var healthUri = new Uri($"http://127.0.0.1:{Port}/api/health");
        var deadline = DateTime.UtcNow.AddSeconds(60);

        while (DateTime.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (process.HasExited)
            {
                throw new InvalidOperationException(
                    $"本地服务尚未就绪便退出（代码 {process.ExitCode}）。请查看日志：{ServerLogPath}"
                );
            }

            try
            {
                using var response = await _httpClient.GetAsync(healthUri, cancellationToken);
                if (response.IsSuccessStatusCode)
                {
                    return;
                }
            }
            catch (HttpRequestException)
            {
                // 服务仍在启动。
            }
            catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                // 单次健康检查超时，继续等待总启动期限。
            }

            await Task.Delay(200, cancellationToken);
        }

        throw new TimeoutException($"本地服务在 60 秒内未就绪。请查看日志：{ServerLogPath}");
    }

    private static string ResolveDataDirectory()
    {
        var configured = Environment.GetEnvironmentVariable("APP_DATA_DIR")?.Trim();
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return Path.GetFullPath(Environment.ExpandEnvironmentVariables(configured));
        }

        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "VirtualCompanion"
        );
    }

    private static string ResolveServerExecutable(string? configuredPath)
    {
        var candidates = new[]
        {
            configuredPath,
            Environment.GetEnvironmentVariable("VIRTUAL_COMPANION_SERVER"),
            Path.Combine(AppContext.BaseDirectory, "server", "VirtualCompanion.Server.exe"),
            Path.Combine(AppContext.BaseDirectory, "VirtualCompanion.Server.exe"),
        };

        foreach (var candidate in candidates)
        {
            if (string.IsNullOrWhiteSpace(candidate))
            {
                continue;
            }

            var fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(candidate));
            if (File.Exists(fullPath))
            {
                return fullPath;
            }
        }

        throw new FileNotFoundException(
            "没有找到 VirtualCompanion.Server.exe。请使用完整发布目录，或通过 --server-path 指定。"
        );
    }

    private static int ResolvePort(int? commandLinePort, string dataDirectory)
    {
        var configuredPort = commandLinePort;
        var configuredText = Environment.GetEnvironmentVariable("APP_PORT")?.Trim();
        if (configuredPort is null && string.IsNullOrWhiteSpace(configuredText))
        {
            configuredText = ReadEnvValue(Path.Combine(dataDirectory, ".env"), "APP_PORT");
        }

        if (configuredPort is null && !string.IsNullOrWhiteSpace(configuredText))
        {
            if (!int.TryParse(configuredText, out var parsedPort) || parsedPort is < 1 or > 65535)
            {
                throw new InvalidOperationException("APP_PORT 必须是 1-65535 之间的整数，或留空自动选择");
            }
            configuredPort = parsedPort;
        }

        if (configuredPort is not null)
        {
            if (!IsPortAvailable(configuredPort.Value))
            {
                throw new InvalidOperationException($"配置的端口 {configuredPort.Value} 已被占用");
            }
            return configuredPort.Value;
        }

        for (var port = 8000; port <= 65535; port++)
        {
            if (IsPortAvailable(port))
            {
                return port;
            }
        }

        throw new InvalidOperationException("未找到可用的本地端口");
    }

    private static string? ReadEnvValue(string path, string key)
    {
        if (!File.Exists(path))
        {
            return null;
        }

        foreach (var rawLine in File.ReadLines(path))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#'))
            {
                continue;
            }

            var separator = line.IndexOf('=');
            if (separator <= 0 || !line[..separator].Trim().Equals(key, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            return line[(separator + 1)..].Trim().Trim('"', '\'');
        }

        return null;
    }

    private static bool IsPortAvailable(int port)
    {
        try
        {
            var listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            listener.Stop();
            return true;
        }
        catch (SocketException)
        {
            return false;
        }
    }

    private static void TryStopProcess(Process? process)
    {
        if (process is null)
        {
            return;
        }

        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                process.WaitForExit(3000);
            }
        }
        catch (InvalidOperationException)
        {
        }
        catch (System.ComponentModel.Win32Exception)
        {
        }
    }
}
