// PatchSmith.exe: apre PatchSmith.pyw (accanto a questo .exe) con il Python installato, come un programma qualsiasi.
// Lo compila PatchSmith (icona.eseguibile) con il compilatore C# di Windows, con l'icona di patchsmith/icona dentro.
// Python: quello portatile del pacchetto (runtime\pythonw.exe, vedi pacchetto.py) se c'e', poi il piu' recente dal
// 3.10 in su registrato in Windows (PEP 514: utente, poi computer), poi pythonw.exe nel PATH. Se non c'e', una finestra
// spiega cosa installare (in italiano o in inglese, come Windows).
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

    // pythonw.exe del Python piu' recente dal 3.10 in su, dalle chiavi Software\Python\PythonCore\<versione>\InstallPath
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
                    // chiave non leggibile: si guarda la prossima
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
                // cartella con caratteri non validi nel PATH
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
