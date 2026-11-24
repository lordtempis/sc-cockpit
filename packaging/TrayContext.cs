using System;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

internal sealed class TrayContext : ApplicationContext
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string RunName = "SC Cockpit";
    private readonly string root;
    private readonly EventWaitHandle activate;
    private readonly NotifyIcon tray = new NotifyIcon();
    private readonly ContextMenuStrip menu = new ContextMenuStrip();
    private readonly ToolStripMenuItem status = new ToolStripMenuItem();
    private readonly ToolStripMenuItem startup = new ToolStripMenuItem("Start with Windows");
    private readonly ToolStripMenuItem mode = new ToolStripMenuItem();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    private readonly Icon icon;
    private Process reader;
    private string game;
    private DateTime buildStamp;
    private DateTime nextDiscovery;
    private bool demo, openPending, stopping, quitting;
    private volatile string readerStatus = "Starting reader";
    private volatile string lastError;
    private volatile int port;
    private int preferredPort;

    internal TrayContext(string root, EventWaitHandle activate, bool background, bool demo)
    {
        this.root = root;
        this.activate = activate;
        this.demo = demo;
        game = demo ? null : Launcher.FindGame();
        openPending = !background;

        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("SC.Cockpit.Icon"))
        using (var image = new Icon(stream, SystemInformation.SmallIconSize))
            icon = (Icon)image.Clone();
        status.Enabled = false;
        menu.Items.Add(status);
        menu.Items.Add("Open dashboard", null, delegate { OpenDashboard(); });
        menu.Items.Add("Locate game…", null, delegate { SelectGame(); });
        menu.Items.Add(mode);
        mode.Click += delegate { if (!stopping) SwitchReader(!demo, demo ? Launcher.FindGame() : null); };
        menu.Items.Add("Restart reader", null, delegate { if (!stopping) RestartReader(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(startup);
        startup.Click += delegate { ToggleStartup(); };
        menu.Opening += delegate {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(RunKey))
                    startup.Checked = key != null && string.Equals(key.GetValue(RunName) as string, StartupCommand(),
                                                                  StringComparison.OrdinalIgnoreCase);
                startup.Enabled = true;
            }
            catch (SecurityException) { startup.Enabled = false; }
            catch (UnauthorizedAccessException) { startup.Enabled = false; }
            catch (IOException) { startup.Enabled = false; }
            mode.Text = demo ? "Use live telemetry" : "Use demo telemetry";
            status.ToolTipText = lastError ?? "";
            startup.ToolTipText = "Start quietly when you sign in to Windows";
        };
        menu.Items.Add("Quit SC Cockpit", null, delegate { Quit(); });
        tray.Icon = icon;
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { OpenDashboard(); };
        tray.Text = "SC Cockpit · Starting";
        tray.Visible = true;
        timer.Interval = 500;
        timer.Tick += delegate { Tick(); };
        StartReader();
        timer.Start();
    }

    private static string StartupCommand()
    {
        return "\"" + Application.ExecutablePath + "\" --background";
    }

    private void ToggleStartup()
    {
        try
        {
            using (var key = Registry.CurrentUser.CreateSubKey(RunKey))
            {
                if (startup.Checked) key.DeleteValue(RunName, false);
                else key.SetValue(RunName, StartupCommand(), RegistryValueKind.String);
            }
        }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private void ShowError(string text)
    {
        MessageBox.Show(text, "SC Cockpit", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private void StartReader()
    {
        string python = Path.Combine(root, "runtime", "python.exe");
        string script = Path.Combine(root, "app", "cockpit.py");
        string source = demo ? "--demo" : game == null ? "--wait-for-game" : "--game-exe \"" + game + "\"";
        buildStamp = game == null ? DateTime.MinValue : File.GetLastWriteTimeUtc(game);
        nextDiscovery = DateTime.UtcNow.AddSeconds(10);
        lastError = null;
        port = 0;
        readerStatus = "Starting reader";
        var info = new ProcessStartInfo(python, "-B -u \"" + script + "\" " + source +
                                        " --tray --port " + preferredPort.ToString(CultureInfo.InvariantCulture));
        info.WorkingDirectory = root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardInput = info.RedirectStandardOutput = info.RedirectStandardError = true;
        info.StandardOutputEncoding = info.StandardErrorEncoding = Encoding.UTF8;
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        var child = new Process { StartInfo = info };
        child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args) {
            string line = args.Data;
            if (line == null) return;
            int value;
            if (line.StartsWith("TRAY PORT ") && int.TryParse(line.Substring(10), out value) && value > 0 && value <= 65535)
                port = value;
            else if (line.StartsWith("TRAY STATUS ")) readerStatus = line.Substring(12);
            else if (line.StartsWith("Reader setup failed:")) lastError = line;
        };
        child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args) {
            if (!string.IsNullOrEmpty(args.Data)) lastError = args.Data;
        };
        try
        {
            child.Start();
            reader = child;
            child.BeginOutputReadLine();
            child.BeginErrorReadLine();
        }
        catch (Exception ex)
        {
            child.Dispose();
            reader = null;
            lastError = ex.Message;
            readerStatus = "Reader could not start";
        }
    }

    private void Tick()
    {
        if (!quitting && activate.WaitOne(0)) OpenDashboard();
        if (reader != null && reader.HasExited)
        {
            reader.WaitForExit(); // Drain both redirected streams before starting another reader.
            bool failed = reader.ExitCode != 0;
            if (failed && lastError == null) lastError = "Reader exit code " + reader.ExitCode;
            reader.Dispose();
            reader = null;
            port = 0;
            if (quitting)
            {
                if (failed) ShowError(lastError);
                ExitThread();
                return;
            }
            if (stopping) { stopping = false; StartReader(); }
            else { readerStatus = "Reader stopped"; preferredPort = 0; }
        }
        if (stopping || quitting) return;
        if (!demo && reader != null && DateTime.UtcNow >= nextDiscovery)
        {
            nextDiscovery = DateTime.UtcNow.AddSeconds(15);
            string detected = game;
            if (game == null || !File.Exists(game)) detected = Launcher.FindGame();
            else if (readerStatus == "Waiting for telemetry") detected = Launcher.RunningGame() ?? game;
            if (!string.Equals(game, detected, StringComparison.OrdinalIgnoreCase) ||
                (game != null && File.GetLastWriteTimeUtc(game) != buildStamp))
                SwitchReader(false, detected);
        }
        if (openPending && port != 0 && !stopping) OpenDashboard();
        string label = !demo && game == null && readerStatus == "Waiting for telemetry" ? "Waiting for game installation" : readerStatus;
        status.Text = label;
        tray.Text = "SC Cockpit · " + label;
    }

    private void OpenDashboard()
    {
        openPending = true;
        if (reader == null && !quitting && !stopping) StartReader();
        int currentPort = port;
        if (currentPort == 0 || stopping || quitting) return;
        openPending = false;
        try { Process.Start(new ProcessStartInfo("http://127.0.0.1:" + currentPort + "/") { UseShellExecute = true }); }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private void SelectGame()
    {
        if (stopping) return;
        try
        {
            string selected = Launcher.ChooseGame();
            if (selected != null) SwitchReader(false, selected);
        }
        catch (Exception ex) { ShowError(ex.Message); }
    }

    private void SwitchReader(bool useDemo, string selected)
    {
        demo = useDemo;
        game = selected;
        RestartReader();
    }

    private void RestartReader()
    {
        if (reader == null) { StartReader(); return; }
        stopping = true;
        preferredPort = port;
        status.Text = "Restarting reader";
        CloseInput();
    }

    private void CloseInput()
    {
        // EOF requests normal HTTP shutdown and finalization of the current recording.
        try { reader.StandardInput.Close(); }
        catch (IOException) { }
        catch (InvalidOperationException) { }
    }

    private void Quit()
    {
        quitting = true;
        if (reader == null) { ExitThread(); return; }
        menu.Enabled = false;
        tray.Text = "SC Cockpit · Closing reader";
        CloseInput();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            timer.Dispose();
            tray.Visible = false;
            tray.Dispose();
            icon.Dispose();
            menu.Dispose();
            if (reader != null)
            {
                CloseInput();
                reader.Dispose();
            }
        }
        base.Dispose(disposing);
    }
}
