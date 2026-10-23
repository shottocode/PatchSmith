// Patchsmith - starts Patchsmith's translator when the game starts (plugin for BepInEx 5).
// XUnity.AutoTranslator sends the new sentences (e.g. those with numbers, built while playing) to the CustomTranslate
// address on 127.0.0.1, where Patchsmith's service answers (smith.py xunity-service) and translates them with the
// local translator or the online ones, with Patchsmith's pauses. The command to run is in
// BepInEx/config/patchsmith-servizio.cfg, written by Patchsmith; the service receives the game's process number and
// closes by itself when the game closes. If the given Python is gone (moved or updated) it tries the Windows
// Python launcher (py -3). How it went is written to BepInEx/patchsmith-servizio-stato.txt, which Patchsmith reads
// after playing.
// Compiled by Patchsmith with csc (C# 5) against the game's libraries.
// The GUID and the file names stay as they were: games translated before know the plugin by them.
using System;
using System.Diagnostics;
using System.IO;
using BepInEx;

[BepInPlugin("patchsmith.servizio", "Patchsmith translator", "1.2.0")]
public class PatchsmithServizio : BaseUnityPlugin
{
    void Awake()
    {
        string cfg = Path.Combine(Paths.ConfigPath, "patchsmith-servizio.cfg");
        if (!File.Exists(cfg))
        {
            Logger.LogWarning("Missing " + cfg + ": Patchsmith's translator not started");
            WriteState("cfg-missing", "", cfg);
            return;
        }
        string program = null, arguments = "", folder = null;
        foreach (string line in File.ReadAllLines(cfg))
        {
            // (Programma=, Argomenti=, Cartella=: the configuration written by earlier versions of Patchsmith)
            if (line.StartsWith("Program=")) program = line.Substring(8).Trim();
            else if (line.StartsWith("Programma=")) program = line.Substring(10).Trim();
            else if (line.StartsWith("Arguments=")) arguments = line.Substring(10).Trim();
            else if (line.StartsWith("Argomenti=")) arguments = line.Substring(10).Trim();
            else if (line.StartsWith("Folder=")) folder = line.Substring(7).Trim();
            else if (line.StartsWith("Cartella=")) folder = line.Substring(9).Trim();
        }
        string script = FirstQuoted(arguments);
        if (!string.IsNullOrEmpty(script) && !File.Exists(script))
        {
            Logger.LogWarning("Patchsmith not found (" + script + "): apply the translation again with Patchsmith");
            WriteState("patchsmith-missing", program, script);
            return;
        }
        string note = "";
        if (string.IsNullOrEmpty(program) || !File.Exists(program))
        {
            string fallback = WindowsPython();
            if (fallback == null)
            {
                Logger.LogWarning("Python not found (" + program + "): apply the translation again with Patchsmith");
                WriteState("python-missing", program, "");
                return;
            }
            note = "Python not found (" + program + "): using " + fallback;
            Logger.LogWarning(note);
            program = fallback;
            arguments = "-3 " + arguments;
        }
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(program,
                arguments + " --pid " + Process.GetCurrentProcess().Id);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            if (!string.IsNullOrEmpty(folder) && Directory.Exists(folder))
                info.WorkingDirectory = folder;
            Process.Start(info);
            Logger.LogInfo("Patchsmith's translator started");
            WriteState("started", program, note);
        }
        catch (Exception e)
        {
            Logger.LogWarning("Patchsmith's translator not started: " + e.Message);
            WriteState("error", program, e.Message);
        }
    }

    // The Python launcher for Windows (pyw.exe without a window, otherwise py.exe), or null.
    static string WindowsPython()
    {
        string windows = Environment.GetEnvironmentVariable("WINDIR");
        if (string.IsNullOrEmpty(windows)) windows = @"C:\Windows";
        foreach (string name in new string[] { "pyw.exe", "py.exe" })
        {
            string path = Path.Combine(windows, name);
            if (File.Exists(path)) return path;
        }
        return null;
    }

    static string FirstQuoted(string text)
    {
        int start = text.IndexOf('"');
        if (start < 0) return null;
        int end = text.IndexOf('"', start + 1);
        return end > start ? text.Substring(start + 1, end - start - 1) : null;
    }

    // BepInEx/patchsmith-servizio-stato.txt: one line per entry (Outcome, Date, Process, Program, Detail).
    void WriteState(string outcome, string program, string detail)
    {
        try
        {
            string text = "# Written by Patchsmith's plugin at every start of the game\r\n" +
                "Outcome=" + outcome + "\r\n" +
                "Date=" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\r\n" +
                "Process=" + Process.GetCurrentProcess().Id + "\r\n" +
                "Program=" + (program ?? "") + "\r\n" +
                "Detail=" + (detail ?? "").Replace("\r", " ").Replace("\n", " ") + "\r\n";
            File.WriteAllText(Path.Combine(Paths.BepInExRootPath, "patchsmith-servizio-stato.txt"), text);
        }
        catch (Exception e)
        {
            Logger.LogWarning("State of Patchsmith's translator not written: " + e.Message);
        }
    }
}
