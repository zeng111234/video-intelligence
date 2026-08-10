using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

internal static class OfflineInstallerBootstrap
{
    [STAThread]
    private static int Main()
    {
        string temporaryRoot = Path.Combine(
            Path.GetTempPath(),
            "VideoInsight-installer-" + Guid.NewGuid().ToString("N")
        );

        try
        {
            Log("bootstrap started");
            Directory.CreateDirectory(temporaryRoot);
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
                string output = installer.StandardOutput.ReadToEnd();
                string errorOutput = installer.StandardError.ReadToEnd();
                installer.WaitForExit();
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
                "安装失败：" + error.Message,
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
                if (Directory.Exists(temporaryRoot))
                {
                    Directory.Delete(temporaryRoot, true);
                }
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
            Directory.CreateDirectory(logDirectory);
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
        string systemRoot = Environment.GetEnvironmentVariable("SystemRoot");
        if (string.IsNullOrWhiteSpace(systemRoot))
        {
            systemRoot = @"C:\Windows";
        }
        string system32 = Path.Combine(systemRoot, "System32");
        string powershell = Path.Combine(system32, "WindowsPowerShell", "v1.0", "powershell.exe");
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
            using (FileStream target = new FileStream(destination, FileMode.Create, FileAccess.Write))
            {
                source.CopyTo(target);
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
}
