using System;
using System.Diagnostics;
using System.IO;
using Microsoft.Win32;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class OfflineInstallerBootstrap
{
    private sealed class InstallProgressForm : Form
    {
        private readonly Label statusLabel;

        internal InstallProgressForm(InstallationPaths paths)
        {
            Text = "VideoInsight 正在安装";
            Width = 620;
            Height = 260;
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            ControlBox = false;
            TopMost = true;

            statusLabel = new Label {
                Left = 28,
                Top = 26,
                Width = 540,
                Height = 28,
                Text = "正在准备安装文件…"
            };
            ProgressBar progress = new ProgressBar {
                Left = 28,
                Top = 68,
                Width = 540,
                Height = 20,
                Style = ProgressBarStyle.Marquee,
                MarqueeAnimationSpeed = 25
            };
            Label locationLabel = new Label {
                Left = 28,
                Top = 104,
                Width = 540,
                Height = 88,
                AutoEllipsis = true,
                Text = "程序位置：" + paths.InstallRoot + Environment.NewLine +
                    "数据位置：" + paths.RuntimeRoot + Environment.NewLine +
                    "安装完成后会自动创建桌面和开始菜单快捷方式。"
            };
            Controls.Add(statusLabel);
            Controls.Add(progress);
            Controls.Add(locationLabel);
        }

        internal void SetStatus(string status)
        {
            statusLabel.Text = status;
            statusLabel.Refresh();
            Application.DoEvents();
        }
    }

    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        string temporaryRoot = null;
        Mutex installerMutex = null;
        bool ownsInstallerMutex = false;
        InstallProgressForm progressForm = null;

        try
        {
            installerMutex = new Mutex(false, @"Local\VideoInsight-Installer");
            try
            {
                ownsInstallerMutex = installerMutex.WaitOne(0, false);
            }
            catch (AbandonedMutexException)
            {
                ownsInstallerMutex = true;
            }
            if (!ownsInstallerMutex)
            {
                Log("another installer is already running");
                MessageBox.Show(
                    "VideoInsight 正在安装或更新，请查看已经打开的安装窗口，不要重复启动。",
                    "安装正在进行",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                return 2;
            }
            Log("bootstrap started");
            string version = ReadTextResource("VideoInsight.Version").Trim();
            if (!Regex.IsMatch(version, @"^[0-9]+\.[0-9]+\.[0-9]+$"))
            {
                throw new InvalidOperationException("安装包版本信息无效。");
            }
            InstallationPaths installationPaths = SelectInstallationPaths();
            if (installationPaths == null)
            {
                Log("installation cancelled before changes");
                return 0;
            }
            progressForm = new InstallProgressForm(installationPaths);
            progressForm.Show();
            progressForm.Refresh();
            string temporaryParent = Path.GetDirectoryName(installationPaths.InstallRoot);
            EnsureSafeExistingPath(temporaryParent);
            Directory.CreateDirectory(temporaryParent);
            temporaryRoot = Path.Combine(
                temporaryParent,
                ".VideoInsight-installer-" + Guid.NewGuid().ToString("N")
            );
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
            Log("resources extracted");
            progressForm.SetStatus("正在关闭旧版本并安装，请不要再次打开 VideoInsight…");

            if (!File.Exists(payloadPath) || !File.Exists(installScriptPath))
            {
                throw new InvalidOperationException("安装包内容不完整。");
            }

            ProcessStartInfo startInfo = CreatePowerShellStartInfo(temporaryRoot);
            startInfo.Arguments =
                "-NoProfile -ExecutionPolicy Bypass -File \"" + installScriptPath +
                "\" -Version \"" + version + "\" -VerifierPath \"" + verifierPath +
                "\" -InstallRoot \"" + installationPaths.InstallRoot +
                "\" -RuntimeRoot \"" + installationPaths.RuntimeRoot + "\" -Quiet";

            using (Process installer = Process.Start(startInfo))
            {
                if (installer == null)
                {
                    throw new InvalidOperationException("无法启动安装程序。");
                }
                Task<string> outputTask = installer.StandardOutput.ReadToEndAsync();
                Task<string> errorTask = installer.StandardError.ReadToEndAsync();
                while (!installer.WaitForExit(200))
                {
                    Application.DoEvents();
                }
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
                progressForm.SetStatus("安装完成，正在确认快捷方式和本机数据…");
                progressForm.Close();
                progressForm = null;
                MessageBox.Show(
                    "VideoInsight 已安装并启动，自动检查全部通过。\n" +
                    "桌面和开始菜单快捷方式已经创建。\n" +
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
            if (progressForm != null)
            {
                progressForm.Close();
                progressForm = null;
            }
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
            if (progressForm != null)
            {
                progressForm.Close();
            }
            try
            {
                DeleteTemporaryRootSafely(temporaryRoot);
            }
            catch
            {
                // Windows or antivirus software may briefly retain extracted files.
            }
            if (ownsInstallerMutex && installerMutex != null)
            {
                try
                {
                    installerMutex.ReleaseMutex();
                }
                catch
                {
                }
            }
            if (installerMutex != null)
            {
                installerMutex.Dispose();
            }
        }
    }

    private sealed class InstallationPaths
    {
        internal string InstallRoot;
        internal string RuntimeRoot;
    }

    private static InstallationPaths SelectInstallationPaths()
    {
        const string uninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight";
        string existingInstallRoot = "";
        string existingRuntimeRoot = "";
        using (RegistryKey key = Registry.CurrentUser.OpenSubKey(uninstallKey, false))
        {
            if (key != null)
            {
                existingInstallRoot = Convert.ToString(key.GetValue("InstallLocation", "")).Trim();
                existingRuntimeRoot = Convert.ToString(key.GetValue("RuntimeLocation", "")).Trim();
            }
        }

        if (!string.IsNullOrWhiteSpace(existingInstallRoot) &&
            !string.IsNullOrWhiteSpace(existingRuntimeRoot))
        {
            ValidateSelectedLocation(existingInstallRoot);
            ValidateSelectedLocation(existingRuntimeRoot);
            return new InstallationPaths {
                InstallRoot = Path.GetFullPath(existingInstallRoot),
                RuntimeRoot = Path.GetFullPath(existingRuntimeRoot)
            };
        }

        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string defaultInstallRoot = string.IsNullOrWhiteSpace(existingInstallRoot)
            ? Path.Combine(localAppData, "Programs", "VideoInsight")
            : Path.GetFullPath(existingInstallRoot);
        string selectedParent;
        using (FolderBrowserDialog dialog = new FolderBrowserDialog())
        {
            dialog.Description =
                "请选择 VideoInsight 的保存位置。程序、素材和生成视频都会放在所选位置；" +
                "可以选择 C、D、E 等任意本地磁盘。";
            dialog.SelectedPath = Directory.Exists(defaultInstallRoot)
                ? defaultInstallRoot
                : Path.GetDirectoryName(defaultInstallRoot);
            dialog.ShowNewFolderButton = true;
            if (dialog.ShowDialog() != DialogResult.OK)
            {
                return null;
            }
            selectedParent = Path.GetFullPath(dialog.SelectedPath);
        }

        string selectedInstallRoot = string.Equals(
            Path.GetFileName(selectedParent.TrimEnd(Path.DirectorySeparatorChar)),
            "VideoInsight",
            StringComparison.OrdinalIgnoreCase
        ) ? selectedParent : Path.Combine(selectedParent, "VideoInsight");
        string selectedRuntimeRoot;
        if (string.Equals(
            Path.GetFullPath(selectedInstallRoot),
            Path.GetFullPath(defaultInstallRoot),
            StringComparison.OrdinalIgnoreCase
        ) && !string.IsNullOrWhiteSpace(existingInstallRoot))
        {
            selectedRuntimeRoot = Path.Combine(localAppData, "VideoInsight");
        }
        else
        {
            selectedRuntimeRoot = Path.Combine(
                Path.GetDirectoryName(selectedInstallRoot),
                "VideoInsight-Data"
            );
        }
        ValidateSelectedLocation(selectedInstallRoot);
        ValidateSelectedLocation(selectedRuntimeRoot);
        return new InstallationPaths {
            InstallRoot = Path.GetFullPath(selectedInstallRoot),
            RuntimeRoot = Path.GetFullPath(selectedRuntimeRoot)
        };
    }

    private static void ValidateSelectedLocation(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
        {
            throw new InvalidOperationException("请选择本机磁盘上的完整安装位置。");
        }
        string fullPath = Path.GetFullPath(path);
        string driveRoot = Path.GetPathRoot(fullPath);
        if (string.IsNullOrWhiteSpace(driveRoot) || fullPath.StartsWith(@"\\"))
        {
            throw new InvalidOperationException("安装位置不能是网络共享目录。");
        }
        DriveInfo drive = new DriveInfo(driveRoot);
        if (!drive.IsReady || drive.DriveType != DriveType.Fixed)
        {
            throw new InvalidOperationException("请选择电脑内置的本地磁盘，不能使用U盘或网络盘。");
        }
        EnsureSafeExistingPath(fullPath);
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

    private static ProcessStartInfo CreatePowerShellStartInfo(string workingDirectory)
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
        startInfo.WorkingDirectory = workingDirectory;
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
