using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class OfflineInstallerBootstrap
{
    [STAThread]
    private static int Main()
    {
        string temporaryParent = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Temp"
        );
        string temporaryRoot = Path.Combine(
            temporaryParent,
            "VideoInsight-installer-" + Guid.NewGuid().ToString("N")
        );

        try
        {
            Log("bootstrap started");
            EnsureSafeExistingPath(temporaryParent);
            Directory.CreateDirectory(temporaryRoot);
            EnsureSafeExistingPath(temporaryRoot);
            string payloadPath = WriteResource(
                "VideoInsight.Payload",
                Path.Combine(temporaryRoot, "payload.zip")
            );
            string installScriptPath = WriteResource(
                "VideoInsight.InstallScript",
                Path.Combine(temporaryRoot, "install_windows_desktop.ps1")
            );
            WriteResource(
                "VideoInsight.UninstallScript",
                Path.Combine(temporaryRoot, "uninstall_windows_desktop.ps1")
            );
            string verifierPath = WriteResource(
                "VideoInsight.VerifierScript",
                Path.Combine(temporaryRoot, "verify_windows_install.ps1")
            );
            string version = ReadTextResource("VideoInsight.Version").Trim();
            if (!Regex.IsMatch(version, @"^[0-9]+\.[0-9]+\.[0-9]+$"))
            {
                throw new InvalidOperationException("安装包版本信息无效。");
            }
            Log("resources extracted");

            if (!File.Exists(payloadPath) || !File.Exists(installScriptPath))
            {
                throw new InvalidOperationException("安装包内容不完整。");
            }

            ProcessStartInfo startInfo = CreatePowerShellStartInfo();
            startInfo.Arguments =
                "-NoProfile -ExecutionPolicy Bypass -File \"" + installScriptPath +
                "\" -Version \"" + version + "\" -VerifierPath \"" + verifierPath + "\" -Quiet";

            using (Process installer = Process.Start(startInfo))
            {
                if (installer == null)
                {
                    throw new InvalidOperationException("无法启动安装程序。");
                }
                Task<string> outputTask = installer.StandardOutput.ReadToEndAsync();
                Task<string> errorTask = installer.StandardError.ReadToEndAsync();
                installer.WaitForExit();
                Task.WaitAll(outputTask, errorTask);
                string output = outputTask.Result;
                string errorOutput = errorTask.Result;
                Log("install script exit " + installer.ExitCode);
                if (installer.ExitCode != 0)
                {
                    string details = string.IsNullOrWhiteSpace(errorOutput) ? output : errorOutput;
                    throw new InvalidOperationException(
                        string.IsNullOrWhiteSpace(details) ? "安装脚本执行失败。" : details.Trim()
                    );
                }
                MessageBox.Show(
                    "VideoInsight 已安装并启动，自动检查全部通过。\n" +
                    "桌面已生成验收报告，无需再输入命令。",
                    "VideoInsight 安装和检查完成",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                return 0;
            }
        }
        catch (Exception error)
        {
            Log("bootstrap failed: " + error);
            MessageBox.Show(
                "安装没有完成，程序已停止并尽可能恢复原版本。\n" +
                "请把安装日志交给技术人员处理，不需要输入任何命令。",
                "VideoInsight 安装失败",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }
        finally
        {
            try
            {
                DeleteTemporaryRootSafely(temporaryRoot);
            }
            catch
            {
                // Windows or antivirus software may briefly retain extracted files.
            }
        }
    }

    private static void Log(string message)
    {
        try
        {
            string logDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VideoInsight",
                "data",
                "logs"
            );
            EnsureSafeExistingPath(Path.GetDirectoryName(logDirectory));
            Directory.CreateDirectory(logDirectory);
            EnsureSafeExistingPath(logDirectory);
            File.AppendAllText(
                Path.Combine(logDirectory, "installer-bootstrap.log"),
                DateTime.Now.ToString("s") + " " + message + Environment.NewLine
            );
        }
        catch
        {
        }
    }

    private static ProcessStartInfo CreatePowerShellStartInfo()
    {
        string systemRoot = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        if (string.IsNullOrWhiteSpace(systemRoot))
        {
            throw new InvalidOperationException("无法从 Windows 已知目录读取系统 PowerShell。 ");
        }
        string system32 = Path.Combine(systemRoot, "System32");
        string powershell = Path.Combine(system32, "WindowsPowerShell", "v1.0", "powershell.exe");
        EnsureSafeExistingPath(powershell);
        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = powershell;
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = true;
        startInfo.WindowStyle = ProcessWindowStyle.Hidden;
        startInfo.RedirectStandardOutput = true;
        startInfo.RedirectStandardError = true;
        startInfo.EnvironmentVariables["PATH"] = string.Join(
            ";",
            system32,
            systemRoot,
            Path.Combine(system32, "Wbem"),
            Path.Combine(system32, "WindowsPowerShell", "v1.0")
        );
        return startInfo;
    }

    private static string WriteResource(string resourceName, string destination)
    {
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName))
        {
            if (source == null)
            {
                throw new InvalidOperationException("缺少安装资源：" + resourceName);
            }
            using (FileStream target = new FileStream(
                destination,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None
            ))
            {
                source.CopyTo(target);
                target.Flush(true);
            }
        }
        return destination;
    }

    private static string ReadTextResource(string resourceName)
    {
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName))
        {
            if (source == null)
            {
                throw new InvalidOperationException("缺少安装资源：" + resourceName);
            }
            using (StreamReader reader = new StreamReader(source))
            {
                return reader.ReadToEnd();
            }
        }
    }

    private static void EnsureSafeExistingPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidOperationException("安装路径无效。");
        }
        string fullPath = Path.GetFullPath(path);
        string root = Path.GetPathRoot(fullPath);
        string cursor = root;
        string relative = fullPath.Substring(root.Length);
        foreach (string segment in relative.Split(
            new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
            StringSplitOptions.RemoveEmptyEntries
        ))
        {
            cursor = Path.Combine(cursor, segment);
            if (!Directory.Exists(cursor) && !File.Exists(cursor))
            {
                break;
            }
            FileAttributes attributes = File.GetAttributes(cursor);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidOperationException("安装路径不能经过链接或目录联接。");
            }
        }
    }

    private static void DeleteTemporaryRootSafely(string temporaryRoot)
    {
        if (!Directory.Exists(temporaryRoot))
        {
            return;
        }
        DirectoryInfo root = new DirectoryInfo(temporaryRoot);
        if ((root.Attributes & FileAttributes.ReparsePoint) != 0)
        {
            return;
        }
        foreach (FileSystemInfo entry in root.GetFileSystemInfos())
        {
            if ((entry.Attributes & FileAttributes.ReparsePoint) != 0 || entry is DirectoryInfo)
            {
                return;
            }
        }
        foreach (FileInfo file in root.GetFiles())
        {
            file.Delete();
        }
        root.Delete(false);
    }
}
