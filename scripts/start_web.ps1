# 在新窗口中启动服务；启动器会等待实际端口可用后自动打开浏览器。
$ScriptsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot   = Split-Path -Parent $ScriptsRoot
Start-Process -FilePath "D:/Anaconda/envs/indextts/python.exe" -ArgumentList "`"$ProjectRoot/app_launcher.py`"" -WorkingDirectory $ProjectRoot -WindowStyle Normal
