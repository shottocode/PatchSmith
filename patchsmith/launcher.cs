// PatchSmith.exe: opens PatchSmith.pyw (next to this .exe) with the installed Python, like any other program.
// PatchSmith compiles it (icon.eseguibile) with the Windows C# compiler, with the icon of patchsmith/icon inside.
// Python: the package's portable one (runtime\pythonw.exe, see package.py) if present, then the most recent from
// 3.10 up registered in Windows (PEP 514: user, then machine), then pythonw.exe in the PATH. If there is none, a
// dialog explains what to install (in Italian or English, like Windows).
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Windows.Forms;
using Microsoft.Win32;

static class Avvio
{
    static readonly bool Italiano = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "it";

    [STAThread]
    static int Main()
    {
        string cartella = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(cartella, "PatchSmith.pyw");
        if (!File.Exists(script))
            return Errore(Italiano ? "Manca PatchSmith.pyw accanto a PatchSmith.exe: rimetti insieme tutti i file di PatchSmith."
                                   : "PatchSmith.pyw is missing next to PatchSmith.exe: put all the PatchSmith files back together.");
        string portatile = Path.Combine(cartella, "runtime", "pythonw.exe");
        string python = File.Exists(portatile) ? portatile : (DalRegistro() ?? NelPath("pythonw.exe"));
        if (python == null)
            return Errore(Italiano ? "PatchSmith ha bisogno di Python 3.10 o più recente (gratuito, da python.org). Installalo, poi riapri PatchSmith."
                                   : "PatchSmith needs Python 3.10 or newer (free, from python.org). Install it, then open PatchSmith again.");
        try
        {
            var avvio = new ProcessStartInfo(python, "-I \"" + script + "\"");
            avvio.WorkingDirectory = cartella;
            avvio.UseShellExecute = false;
            Process.Start(avvio);
            return 0;
        }
        catch (Exception e)
        {
            return Errore((Italiano ? "Non riesco ad avviare Python (" : "Cannot start Python (") + python + "): " + e.Message);
        }
    }

    // pythonw.exe of the most recent Python from 3.10 up, from the keys
    // Software\Python\PythonCore\<version>\InstallPath
    static string DalRegistro()
    {
        string scelto = null;
        Version migliore = new Version(3, 10);
        foreach (RegistryHive radice in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
            foreach (RegistryView vista in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                try
                {
                    using (RegistryKey base_ = RegistryKey.OpenBaseKey(radice, vista))
                    using (RegistryKey core = base_.OpenSubKey(@"Software\Python\PythonCore"))
                    {
                        if (core == null)
                            continue;
                        foreach (string nome in core.GetSubKeyNames())
                        {
                            Version versione;
                            if (!Version.TryParse(nome.Split('-')[0], out versione) || versione < migliore)
                                continue;
                            using (RegistryKey dove = core.OpenSubKey(nome + @"\InstallPath"))
                            {
                                if (dove == null)
                                    continue;
                                string exe = dove.GetValue("WindowedExecutablePath") as string;
                                string cartella = dove.GetValue("") as string;
                                if (string.IsNullOrEmpty(exe) && !string.IsNullOrEmpty(cartella))
                                    exe = Path.Combine(cartella, "pythonw.exe");
                                if (!string.IsNullOrEmpty(exe) && File.Exists(exe) && (scelto == null || versione > migliore))
                                {
                                    scelto = exe;
                                    migliore = versione;
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
        return scelto;
    }

    static string NelPath(string nome)
    {
        foreach (string cartella in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            try
            {
                string p = Path.Combine(cartella.Trim().Trim('"'), nome);
                if (cartella.Trim().Length > 0 && File.Exists(p))
                    return p;
            }
            catch (ArgumentException)
            {
                // folder with invalid characters in the PATH
            }
        }
        return null;
    }

    static int Errore(string testo)
    {
        MessageBox.Show(testo, "PatchSmith", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        return 1;
    }
}
