// Patchsmith - avvia il traduttore di Patchsmith quando parte il gioco (plugin per BepInEx 5).
// XUnity.AutoTranslator manda le frasi nuove (es. quelle con numeri composte mentre si gioca) all'indirizzo
// CustomTranslate su 127.0.0.1, dove risponde il servizio di Patchsmith (smith.py servizio-xunity), che le traduce con
// il traduttore locale o con quelli online, con le pause di Patchsmith. Il comando da eseguire e' in
// BepInEx/config/patchsmith-servizio.cfg, scritto da Patchsmith; il servizio riceve il numero del processo del gioco e
// si chiude da solo quando il gioco si chiude. Se il Python indicato non c'e' piu' (spostato o aggiornato) prova il
// Python di Windows (py -3). Com'e' andata lo scrive in BepInEx/patchsmith-servizio-stato.txt, che Patchsmith legge
// dopo che si e' giocato.
// Compilato da Patchsmith con csc (C# 5) usando le librerie del gioco.
using System;
using System.Diagnostics;
using System.IO;
using BepInEx;

[BepInPlugin("patchsmith.servizio", "Patchsmith traduttore", "1.1.0")]
public class PatchsmithServizio : BaseUnityPlugin
{
    void Awake()
    {
        string cfg = Path.Combine(Paths.ConfigPath, "patchsmith-servizio.cfg");
        if (!File.Exists(cfg))
        {
            Logger.LogWarning("Manca " + cfg + ": traduttore di Patchsmith non avviato");
            Stato("cfg-mancante", "", cfg);
            return;
        }
        string programma = null, argomenti = "", cartella = null;
        foreach (string riga in File.ReadAllLines(cfg))
        {
            if (riga.StartsWith("Programma=")) programma = riga.Substring(10).Trim();
            else if (riga.StartsWith("Argomenti=")) argomenti = riga.Substring(10).Trim();
            else if (riga.StartsWith("Cartella=")) cartella = riga.Substring(9).Trim();
        }
        string script = PrimoTraVirgolette(argomenti);
        if (!string.IsNullOrEmpty(script) && !File.Exists(script))
        {
            Logger.LogWarning("Patchsmith non trovato (" + script + "): riapplica la traduzione con Patchsmith");
            Stato("patchsmith-mancante", programma, script);
            return;
        }
        string nota = "";
        if (string.IsNullOrEmpty(programma) || !File.Exists(programma))
        {
            string riserva = PythonDiWindows();
            if (riserva == null)
            {
                Logger.LogWarning("Python non trovato (" + programma + "): riapplica la traduzione con Patchsmith");
                Stato("python-mancante", programma, "");
                return;
            }
            nota = "Python indicato non trovato (" + programma + "): uso " + riserva;
            Logger.LogWarning(nota);
            programma = riserva;
            argomenti = "-3 " + argomenti;
        }
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(programma,
                argomenti + " --pid " + Process.GetCurrentProcess().Id);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            if (!string.IsNullOrEmpty(cartella) && Directory.Exists(cartella))
                info.WorkingDirectory = cartella;
            Process.Start(info);
            Logger.LogInfo("Traduttore di Patchsmith avviato");
            Stato("avviato", programma, nota);
        }
        catch (Exception e)
        {
            Logger.LogWarning("Traduttore di Patchsmith non avviato: " + e.Message);
            Stato("errore", programma, e.Message);
        }
    }

    // Il launcher di Python per Windows (pyw.exe senza finestra, altrimenti py.exe), o null.
    static string PythonDiWindows()
    {
        string windows = Environment.GetEnvironmentVariable("WINDIR");
        if (string.IsNullOrEmpty(windows)) windows = @"C:\Windows";
        foreach (string nome in new string[] { "pyw.exe", "py.exe" })
        {
            string percorso = Path.Combine(windows, nome);
            if (File.Exists(percorso)) return percorso;
        }
        return null;
    }

    static string PrimoTraVirgolette(string testo)
    {
        int inizio = testo.IndexOf('"');
        if (inizio < 0) return null;
        int fine = testo.IndexOf('"', inizio + 1);
        return fine > inizio ? testo.Substring(inizio + 1, fine - inizio - 1) : null;
    }

    // BepInEx/patchsmith-servizio-stato.txt: una riga per voce (Esito, Data, Processo, Programma, Dettaglio).
    void Stato(string esito, string programma, string dettaglio)
    {
        try
        {
            string testo = "# Scritto dal plugin di Patchsmith a ogni avvio del gioco\r\n" +
                "Esito=" + esito + "\r\n" +
                "Data=" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\r\n" +
                "Processo=" + Process.GetCurrentProcess().Id + "\r\n" +
                "Programma=" + (programma ?? "") + "\r\n" +
                "Dettaglio=" + (dettaglio ?? "").Replace("\r", " ").Replace("\n", " ") + "\r\n";
            File.WriteAllText(Path.Combine(Paths.BepInExRootPath, "patchsmith-servizio-stato.txt"), testo);
        }
        catch (Exception e)
        {
            Logger.LogWarning("Stato del traduttore di Patchsmith non scritto: " + e.Message);
        }
    }
}
