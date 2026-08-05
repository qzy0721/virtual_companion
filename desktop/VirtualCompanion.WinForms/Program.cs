namespace VirtualCompanion.Desktop;

internal static class Program
{
    private const string SingleInstanceName = @"Local\VirtualCompanion.Desktop";

    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();

        using var singleInstance = new Mutex(true, SingleInstanceName, out var createdNew);
        if (!createdNew)
        {
            MessageBox.Show(
                "虚拟陪伴已经在运行。",
                "虚拟陪伴",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
            return;
        }

        try
        {
            Application.Run(new MainForm(ShellOptions.Parse(args)));
        }
        catch (Exception error)
        {
            try
            {
                var logPath = Path.Combine(Path.GetTempPath(), "VirtualCompanion-startup-crash.log");
                File.WriteAllText(logPath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}{Environment.NewLine}{error}{Environment.NewLine}");
            }
            catch (IOException)
            {
            }

            MessageBox.Show(
                $"桌面外壳启动失败：\n\n{error.Message}",
                "虚拟陪伴",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
