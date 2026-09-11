WScript.Sleep 5000
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\TelegramBot"
WshShell.Run """C:\Users\sarmi\AppData\Local\Programs\Python\Python311\python.exe"" ""C:\TelegramBot\bot_server.py""", 0, False