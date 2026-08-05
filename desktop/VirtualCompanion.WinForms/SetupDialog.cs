// 首启 API 配置向导：LLM / DashScope / OSS 密钥未配置时弹出。
// 直接读写 .env（键语义与 Python 侧 config.py / settings.py 一致），
// 由外层在本地服务启动前调用——保存后即以新配置启动，无需走热重启协议。

using System.Text;

namespace VirtualCompanion.Desktop;

internal sealed class SetupDialog : Form
{
    // 与 config.py 的 missing_credentials 判断保持一致：空值或以 YOUR_ 开头的占位符视为未配置。
    private const string PlaceholderPrefix = "YOUR_";
    private const string DefaultLlmUrl = "https://api.deepseek.com/v1";
    private const string DefaultLlmModel = "deepseek-v4-flash";

    private readonly string _envPath;
    private readonly TextBox _llmKeyBox = new() { PlaceholderText = "sk-..." };
    private readonly TextBox _llmUrlBox = new();
    private readonly TextBox _llmModelBox = new();
    private readonly TextBox _dashscopeKeyBox = new() { PlaceholderText = "sk-..." };
    private readonly TextBox _ossKeyIdBox = new() { PlaceholderText = "LTAI..." };
    private readonly TextBox _ossKeySecretBox = new() { PlaceholderText = "..." };

    public SetupDialog(string dataDirectory)
    {
        _envPath = Path.Combine(dataDirectory, ".env");
        PrefillFromEnv();

        Text = "虚拟陪伴助手 — API 密钥配置";
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        ShowInTaskbar = false;
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = new Font("Microsoft YaHei UI", 9F);
        ClientSize = new Size(560, 470);

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(28, 20, 28, 16),
            ColumnCount = 2,
            RowCount = 9,
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 200));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (var i = 0; i < layout.RowCount; i++)
        {
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        }

        var header = new Label
        {
            Text = "首次启动需要配置 API 密钥，才能使用语音识别、TTS 与对话服务。",
            AutoSize = true,
            Margin = new Padding(0, 0, 0, 8),
        };
        layout.Controls.Add(header, 0, 0);
        layout.SetColumnSpan(header, 2);

        AddField(layout, 1, "LLM API Key（必填）", _llmKeyBox);
        AddField(layout, 2, "LLM API URL", _llmUrlBox);
        AddField(layout, 3, "LLM 模型", _llmModelBox);
        AddField(layout, 4, "DashScope API Key（必填）", _dashscopeKeyBox);
        AddField(layout, 5, "OSS AccessKey ID", _ossKeyIdBox);
        AddField(layout, 6, "OSS AccessKey Secret", _ossKeySecretBox);

        var hint = new Label
        {
            Text = "必填：LLM 与 DashScope 密钥；OSS 仅在使用 CosyVoice 声音复刻时需要，可稍后在应用内「服务设置」页面补充或修改。",
            AutoSize = true,
            ForeColor = Color.FromArgb(138, 141, 161),
            Margin = new Padding(0, 10, 0, 8),
        };
        layout.Controls.Add(hint, 0, 7);
        layout.SetColumnSpan(hint, 2);

        var saveButton = new Button { Text = "保存并启动", AutoSize = true };
        saveButton.Click += OnSave;
        var skipButton = new Button { Text = "跳过，稍后配置", AutoSize = true };
        skipButton.Click += (_, _) => DialogResult = DialogResult.Cancel;

        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.RightToLeft,
        };
        actions.Controls.Add(saveButton);
        actions.Controls.Add(skipButton);
        layout.Controls.Add(actions, 0, 8);
        layout.SetColumnSpan(actions, 2);

        Controls.Add(layout);
        AcceptButton = saveButton;
        CancelButton = skipButton;
    }

    /// <summary>LLM 与 DashScope 密钥均有效才视为已配置（OSS 仅 CosyVoice 需要，不阻塞）。</summary>
    public static bool IsConfigured(string dataDirectory)
    {
        var envPath = Path.Combine(dataDirectory, ".env");
        return IsKeyValid(ReadEnvValue(envPath, "LLM_API_KEY"))
            && IsKeyValid(ReadEnvValue(envPath, "DASHSCOPE_API_KEY"));
    }

    private static bool IsKeyValid(string? value)
    {
        var clean = (value ?? "").Trim();
        if (clean.Length == 0)
        {
            return false;
        }
        return !clean.StartsWith(PlaceholderPrefix, StringComparison.OrdinalIgnoreCase);
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

    private void PrefillFromEnv()
    {
        _llmKeyBox.Text = ReadEnvValue(_envPath, "LLM_API_KEY") ?? "";
        _llmUrlBox.Text = ReadEnvValue(_envPath, "LLM_API_URL") ?? DefaultLlmUrl;
        _llmModelBox.Text = ReadEnvValue(_envPath, "LLM_MODEL") ?? DefaultLlmModel;
        _dashscopeKeyBox.Text = ReadEnvValue(_envPath, "DASHSCOPE_API_KEY") ?? "";
        _ossKeyIdBox.Text = ReadEnvValue(_envPath, "OSS_ACCESS_KEY_ID") ?? "";
        _ossKeySecretBox.Text = ReadEnvValue(_envPath, "OSS_ACCESS_KEY_SECRET") ?? "";
    }

    private static void AddField(TableLayoutPanel layout, int row, string label, TextBox box)
    {
        layout.Controls.Add(
            new Label
            {
                Text = label,
                AutoSize = true,
                TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(0, 6, 8, 6),
            },
            0,
            row
        );
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 6, 0, 6);
        layout.Controls.Add(box, 1, row);
    }

    private void OnSave(object? sender, EventArgs e)
    {
        var llmKey = _llmKeyBox.Text.Trim();
        var dashscopeKey = _dashscopeKeyBox.Text.Trim();
        if (llmKey.Length == 0 || dashscopeKey.Length == 0)
        {
            MessageBox.Show(
                this,
                "请至少填写 LLM API Key 与 DashScope API Key。",
                "配置不完整",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning
            );
            return;
        }

        WriteEnv(_envPath, new Dictionary<string, string>
        {
            ["LLM_API_KEY"] = llmKey,
            ["LLM_API_URL"] = _llmUrlBox.Text.Trim(),
            ["LLM_MODEL"] = _llmModelBox.Text.Trim(),
            ["DASHSCOPE_API_KEY"] = dashscopeKey,
            ["OSS_ACCESS_KEY_ID"] = _ossKeyIdBox.Text.Trim(),
            ["OSS_ACCESS_KEY_SECRET"] = _ossKeySecretBox.Text.Trim(),
        });
        DialogResult = DialogResult.OK;
    }

    private static void WriteEnv(string path, IReadOnlyDictionary<string, string> updates)
    {
        // 与 Python 侧 dotenv set_key 兜底逻辑一致：已存在的键原位替换，
        // 其余行（含注释）原样保留，新键追加到文件末尾。UTF-8 无 BOM。
        var lines = File.Exists(path) ? File.ReadAllLines(path) : Array.Empty<string>();
        var remaining = new Dictionary<string, string>(updates, StringComparer.OrdinalIgnoreCase);
        var output = new List<string>(lines.Length + updates.Count);

        foreach (var rawLine in lines)
        {
            var trimmed = rawLine.Trim();
            var separator = trimmed.IndexOf('=');
            if (trimmed.Length > 0 && !trimmed.StartsWith('#') && separator > 0)
            {
                var key = trimmed[..separator].Trim();
                if (remaining.Remove(key, out var value))
                {
                    output.Add($"{key}={value}");
                    continue;
                }
            }
            output.Add(rawLine);
        }
        foreach (var pair in remaining)
        {
            output.Add($"{pair.Key}={pair.Value}");
        }

        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
        File.WriteAllLines(path, output, new UTF8Encoding(false));
    }
}
