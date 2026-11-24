using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
using System.Reflection;
using System.ComponentModel;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
#if SINGLE_FILE
using System.IO.Compression;
#endif

[assembly: AssemblyTitle("SC Cockpit")]
[assembly: AssemblyProduct("SC Cockpit")]

internal static class Launcher
{
    private static string ApplicationRoot()
    {
#if SINGLE_FILE
        var assembly = Assembly.GetExecutingAssembly();
        string cache = Path.Combine(Path.GetDirectoryName(Settings), "bin");
        string target = Path.Combine(cache, assembly.ManifestModule.ModuleVersionId.ToString("N"));
        if (Directory.Exists(target)) return target;

        Directory.CreateDirectory(cache);
        string stage = Path.Combine(cache, "tmp-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        try
        {
            using (var stream = assembly.GetManifestResourceStream("SC.Cockpit.Payload"))
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
                archive.ExtractToDirectory(stage);
            try { Directory.Move(stage, target); }
            catch (IOException) { if (!Directory.Exists(target)) throw; }
        }
        finally
        {
            if (Directory.Exists(stage)) Directory.Delete(stage, true);
        }
        return target;
#else
        return AppDomain.CurrentDomain.BaseDirectory;
#endif
    }

    private static readonly string Settings = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SC-Cockpit", "game-path.txt");

    internal static bool IsGame(string path)
    {
        return File.Exists(path) && string.Equals(Path.GetFileName(path), "StarCitizen.exe", StringComparison.OrdinalIgnoreCase);
    }

    internal static string RunningGame()
    {
        foreach (var process in Process.GetProcessesByName("StarCitizen"))
        {
            using (process)
            {
                try
                {
                    var module = process.MainModule;
                    string exe = module == null ? null : module.FileName;
                    if (IsGame(exe)) return exe;
                }
                catch (Win32Exception) { }
                catch (InvalidOperationException) { }
            }
        }
        return null;
    }

    private static string LauncherGame()
    {
        string log = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            @"rsilauncher\logs\log.log");
        try
        {
            using (var stream = new FileStream(log, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                // The most recent LIVE launch has priority over older libraries.
                stream.Seek(Math.Max(0, stream.Length - 2 * 1024 * 1024), SeekOrigin.Begin);
                string text;
                using (var reader = new StreamReader(stream, Encoding.UTF8)) text = reader.ReadToEnd();
                var launches = Regex.Matches(text, @"Launching Star Citizen LIVE from \(([^\r\n""]+?\\LIVE)\)",
                    RegexOptions.IgnoreCase | RegexOptions.RightToLeft);
                foreach (Match launch in launches)
                {
                    // The launch message is stored as a JSON-escaped string.
                    string directory = launch.Groups[1].Value.Replace(@"\\", @"\");
                    if (!Path.IsPathRooted(directory)) continue;
                    string exe = Path.Combine(directory, "Bin64", "StarCitizen.exe");
                    if (IsGame(exe)) return exe;
                }
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (ArgumentException) { }
        return null;
    }

    internal static string FindGame()
    {
        string running = RunningGame();
        if (running != null) return running;
        string usual = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            @"Roberts Space Industries\StarCitizen\LIVE\Bin64\StarCitizen.exe");
        try
        {
            if (File.Exists(Settings))
            {
                string saved = File.ReadAllText(Settings).Trim();
                if (IsGame(saved)) return saved;
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return LauncherGame() ?? (IsGame(usual) ? usual : null);
    }

    internal static string ChooseGame()
    {
        using (var picker = new OpenFileDialog())
        {
            picker.Title = "Select your Star Citizen executable";
            picker.Filter = "Star Citizen (StarCitizen.exe)|StarCitizen.exe";
            picker.RestoreDirectory = true;
            if (picker.ShowDialog() != DialogResult.OK) return null;
            string path = picker.FileName;
            if (!IsGame(path)) throw new IOException("Please select StarCitizen.exe.");
            Directory.CreateDirectory(Path.GetDirectoryName(Settings));
            File.WriteAllText(Settings, path);
            return path;
        }
    }

    [STAThread]
    public static int Main(string[] args)
    {
        try
        {
            bool background = Array.IndexOf(args, "--background") >= 0;
            using (var activate = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\SC-Cockpit.Open"))
            using (var instance = new Mutex(false, @"Local\SC-Cockpit.Tray"))
            {
                bool owner;
                try { owner = instance.WaitOne(0); }
                catch (AbandonedMutexException) { owner = true; }
                if (!owner)
                {
                    if (!background) activate.Set();
                    return 0;
                }
                try
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    using (var tray = new TrayContext(ApplicationRoot(), activate, background,
                                                     Array.IndexOf(args, "--demo") >= 0))
                        Application.Run(tray);
                }
                finally { instance.ReleaseMutex(); }
            }
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "SC Cockpit", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
