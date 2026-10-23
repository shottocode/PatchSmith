// PatchSmith.exe: opens PatchSmith.pyw (next to this .exe) with the installed Python, like any other program.
// PatchSmith compiles it (icon.executable) with the Windows C# compiler, with the icon of patchsmith/icon inside.
// Python: the package's portable one (runtime\pythonw.exe, see package.py) if present, then the most recent from
// 3.10 up registered in Windows (PEP 514: user, then machine), then pythonw.exe in the PATH. If there is none, a
// dialog explains what to install (in Italian or English, like Windows).
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    static readonly bool Italian = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "it";

    [STAThread]
    static int Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(folder, "PatchSmith.pyw");
        if (!File.Exists(script))
            return Error(Italian ? "Manca PatchSmith.pyw accanto a PatchSmith.exe: rimetti insieme tutti i file di PatchSmith."
                                 : "PatchSmith.pyw is missing next to PatchSmith.exe: put all the PatchSmith files back together.");
        string portable = Path.Combine(folder, "runtime", "pythonw.exe");
        string python = File.Exists(portable) ? portable : (FromRegistry() ?? InPath("pythonw.exe"));
        if (python == null)
            return Error(Italian ? "PatchSmith ha bisogno di Python 3.10 o più recente (gratuito, da python.org). Installalo, poi riapri PatchSmith."
                                 : "PatchSmith needs Python 3.10 or newer (free, from python.org). Install it, then open PatchSmith again.");
        try
        {
            var start = new ProcessStartInfo(python, "-I \"" + script + "\"");
            start.WorkingDirectory = folder;
            start.UseShellExecute = false;
            Process.Start(start);
            return 0;
        }
        catch (Exception e)
        {
            return Error((Italian ? "Non riesco ad avviare Python (" : "Cannot start Python (") + python + "): " + e.Message);
        }
    }

    // pythonw.exe of the most recent Python from 3.10 up, from the keys
    // Software\Python\PythonCore\<version>\InstallPath
    static string FromRegistry()
    {
        string chosen = null;
        Version best = new Version(3, 10);
        foreach (RegistryHive hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
            foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                try
                {
                    using (RegistryKey base_ = RegistryKey.OpenBaseKey(hive, view))
                    using (RegistryKey core = base_.OpenSubKey(@"Software\Python\PythonCore"))
                    {
                        if (core == null)
                            continue;
                        foreach (string name in core.GetSubKeyNames())
                        {
                            Version version;
                            if (!Version.TryParse(name.Split('-')[0], out version) || version < best)
                                continue;
                            using (RegistryKey where = core.OpenSubKey(name + @"\InstallPath"))
                            {
                                if (where == null)
                                    continue;
                                string exe = where.GetValue("WindowedExecutablePath") as string;
                                string folder = where.GetValue("") as string;
                                if (string.IsNullOrEmpty(exe) && !string.IsNullOrEmpty(folder))
                                    exe = Path.Combine(folder, "pythonw.exe");
                                if (!string.IsNullOrEmpty(exe) && File.Exists(exe) && (chosen == null || version > best))
                                {
                                    chosen = exe;
                                    best = version;
                                }
                            }
                        }
                    }
                }
                catch (Exception)
                {
                    // unreadable key: try the next one
                }
            }
        return chosen;
    }

    static string InPath(string name)
    {
        foreach (string folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            try
            {
                string p = Path.Combine(folder.Trim().Trim('"'), name);
                if (folder.Trim().Length > 0 && File.Exists(p))
                    return p;
            }
            catch (ArgumentException)
            {
                // folder with invalid characters in the PATH
            }
        }
        return null;
    }

    static int Error(string text)
    {
        MessageBox.Show(text, "PatchSmith", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        return 1;
    }
}
