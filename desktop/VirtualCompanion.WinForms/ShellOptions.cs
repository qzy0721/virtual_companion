namespace VirtualCompanion.Desktop;

internal sealed record ShellOptions(string? ServerPath, int? Port)
{
    public static ShellOptions Parse(string[] args)
    {
        string? serverPath = null;
        int? port = null;

        for (var index = 0; index < args.Length; index++)
        {
            switch (args[index])
            {
                case "--server-path" when index + 1 < args.Length:
                    serverPath = args[++index];
                    break;
                case "--port" when index + 1 < args.Length:
                    if (!int.TryParse(args[++index], out var parsedPort) || parsedPort is < 1 or > 65535)
                    {
                        throw new ArgumentException("--port 必须是 1-65535 之间的整数");
                    }
                    port = parsedPort;
                    break;
            }
        }

        return new ShellOptions(serverPath, port);
    }
}
