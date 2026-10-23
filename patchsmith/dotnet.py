"""Lettura di assembly .NET (exe/dll) e dei file .resources, in sola libreria standard.

Serve ai giochi XNA/FNA/MonoGame e in generale ai giochi .NET:
- PE + intestazione CLI + metadati (ECMA-335 II.24): tabelle Assembly, ManifestResource, CustomAttribute...;
- risorse incorporate (es. "RogueCastle.Resources.LocStrings.resources" dentro RogueLegacy.exe);
- formato .resources (System.Resources.ResourceWriter) in lettura e scrittura, solo stringhe;
- heap #US (stringhe scritte nel codice), per analisi.
Niente di questo esegue codice del gioco: si leggono solo byte.
"""
import struct


class ErroreDotnet(ValueError):
    pass


# --- PE + CLI ----------------------------------------------------------------------------------

# tabelle dei metadati (ECMA-335 II.22): nome, colonne. Tipi di colonna:
#   numero = byte fissi; "s" heap stringhe; "g" heap guid; "b" heap blob;
#   ("t", n) indice semplice nella tabella n; ("c", nome) indice codificato
TABELLE = {
    0x00: ("Module", [2, "s", "g", "g", "g"]),
    0x01: ("TypeRef", [("c", "ResolutionScope"), "s", "s"]),
    0x02: ("TypeDef", [4, "s", "s", ("c", "TypeDefOrRef"), ("t", 0x04), ("t", 0x06)]),
    0x03: ("FieldPtr", [("t", 0x04)]),
    0x04: ("Field", [2, "s", "b"]),
    0x05: ("MethodPtr", [("t", 0x06)]),
    0x06: ("MethodDef", [4, 2, 2, "s", "b", ("t", 0x08)]),
    0x07: ("ParamPtr", [("t", 0x08)]),
    0x08: ("Param", [2, 2, "s"]),
    0x09: ("InterfaceImpl", [("t", 0x02), ("c", "TypeDefOrRef")]),
    0x0A: ("MemberRef", [("c", "MemberRefParent"), "s", "b"]),
    0x0B: ("Constant", [2, ("c", "HasConstant"), "b"]),
    0x0C: ("CustomAttribute", [("c", "HasCustomAttribute"), ("c", "CustomAttributeType"), "b"]),
    0x0D: ("FieldMarshal", [("c", "HasFieldMarshal"), "b"]),
    0x0E: ("DeclSecurity", [2, ("c", "HasDeclSecurity"), "b"]),
    0x0F: ("ClassLayout", [2, 4, ("t", 0x02)]),
    0x10: ("FieldLayout", [4, ("t", 0x04)]),
    0x11: ("StandAloneSig", ["b"]),
    0x12: ("EventMap", [("t", 0x02), ("t", 0x14)]),
    0x13: ("EventPtr", [("t", 0x14)]),
    0x14: ("Event", [2, "s", ("c", "TypeDefOrRef")]),
    0x15: ("PropertyMap", [("t", 0x02), ("t", 0x17)]),
    0x16: ("PropertyPtr", [("t", 0x17)]),
    0x17: ("Property", [2, "s", "b"]),
    0x18: ("MethodSemantics", [2, ("t", 0x06), ("c", "HasSemantics")]),
    0x19: ("MethodImpl", [("t", 0x02), ("c", "MethodDefOrRef"), ("c", "MethodDefOrRef")]),
    0x1A: ("ModuleRef", ["s"]),
    0x1B: ("TypeSpec", ["b"]),
    0x1C: ("ImplMap", [2, ("c", "MemberForwarded"), "s", ("t", 0x1A)]),
    0x1D: ("FieldRVA", [4, ("t", 0x04)]),
    0x1E: ("EncLog", [4, 4]),
    0x1F: ("EncMap", [4]),
    0x20: ("Assembly", [4, 2, 2, 2, 2, 4, "b", "s", "s"]),
    0x21: ("AssemblyProcessor", [4]),
    0x22: ("AssemblyOS", [4, 4, 4]),
    0x23: ("AssemblyRef", [2, 2, 2, 2, 4, "b", "s", "s", "b"]),
    0x24: ("AssemblyRefProcessor", [4, ("t", 0x23)]),
    0x25: ("AssemblyRefOS", [4, 4, 4, ("t", 0x23)]),
    0x26: ("File", [4, "s", "b"]),
    0x27: ("ExportedType", [4, 4, "s", "s", ("c", "Implementation")]),
    0x28: ("ManifestResource", [4, 4, "s", ("c", "Implementation")]),
    0x29: ("NestedClass", [("t", 0x02), ("t", 0x02)]),
    0x2A: ("GenericParam", [2, 2, ("c", "TypeOrMethodDef"), "s"]),
    0x2B: ("MethodSpec", [("c", "MethodDefOrRef"), "b"]),
    0x2C: ("GenericParamConstraint", [("t", 0x2A), ("c", "TypeDefOrRef")]),
}

# indici codificati: bit di etichetta, tabelle (None = valore non usato)
CODIFICATI = {
    "TypeDefOrRef": (2, [0x02, 0x01, 0x1B]),
    "HasConstant": (2, [0x04, 0x08, 0x17]),
    "HasCustomAttribute": (5, [0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17, 0x14, 0x11, 0x1A,
                               0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2B]),
    "HasFieldMarshal": (1, [0x04, 0x08]),
    "HasDeclSecurity": (2, [0x02, 0x06, 0x20]),
    "MemberRefParent": (3, [0x02, 0x01, 0x1A, 0x06, 0x1B]),
    "HasSemantics": (1, [0x14, 0x17]),
    "MethodDefOrRef": (1, [0x06, 0x0A]),
    "MemberForwarded": (1, [0x04, 0x06]),
    "Implementation": (2, [0x26, 0x23, 0x27]),
    "CustomAttributeType": (3, [None, None, 0x06, 0x0A, None]),
    "ResolutionScope": (2, [0x00, 0x1A, 0x23, 0x01]),
    "TypeOrMethodDef": (1, [0x02, 0x06]),
}


def _cstr(dati, pos):
    fine = dati.index(b"\x00", pos)
    return dati[pos:fine].decode("utf-8", "replace")


def leggi_compresso(dati, pos):
    """Intero compresso dei blob ECMA-335 (1, 2 o 4 byte). Restituisce (valore, nuova posizione)."""
    b = dati[pos]
    if b & 0x80 == 0:
        return b, pos + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | dati[pos + 1], pos + 2
    if b & 0xE0 == 0xC0:
        return ((b & 0x1F) << 24) | (dati[pos + 1] << 16) | (dati[pos + 2] << 8) | dati[pos + 3], pos + 4
    raise ErroreDotnet("intero compresso non valido")


class Assembly:
    """Assembly .NET letto da file (sola lettura)."""

    def __init__(self, percorso=None, dati=None):
        self.percorso = percorso
        if dati is None:
            with open(percorso, "rb") as f:
                dati = f.read()
        self.dati = dati
        self._pe()
        self._metadati()

    # PE ------------------------------------------------------------------------------------------
    def _pe(self):
        d = self.dati
        if d[:2] != b"MZ":
            raise ErroreDotnet("non e' un eseguibile Windows")
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] != b"PE\x00\x00":
            raise ErroreDotnet("intestazione PE mancante")
        self.macchina, nsez, _, _, _, dim_opz, self.caratteristiche = struct.unpack_from("<HHIIIHH", d, pe + 4)
        opz = pe + 24
        magia = struct.unpack_from("<H", d, opz)[0]
        if magia == 0x10B:
            dir_dati = opz + 96
        elif magia == 0x20B:
            dir_dati = opz + 112
        else:
            raise ErroreDotnet("intestazione opzionale PE sconosciuta")
        self.pe32_plus = magia == 0x20B
        n_dir = struct.unpack_from("<I", d, dir_dati - 4)[0]
        if n_dir <= 14:
            raise ErroreDotnet("non e' un assembly .NET (manca l'intestazione CLI)")
        self.sezioni = []
        tab = opz + dim_opz
        for k in range(nsez):
            nome = d[tab + 40 * k:tab + 40 * k + 8].rstrip(b"\x00").decode("ascii", "replace")
            vdim, vrva, rdim, rpos = struct.unpack_from("<IIII", d, tab + 40 * k + 8)
            self.sezioni.append((nome, vrva, vdim, rpos, rdim))
        cli_rva, cli_dim = struct.unpack_from("<II", d, dir_dati + 14 * 8)
        if not cli_rva:
            raise ErroreDotnet("non e' un assembly .NET (intestazione CLI vuota)")
        c = self.offset(cli_rva)
        (_, self.cli_major, self.cli_minor, self.meta_rva, self.meta_dim, self.cli_flags, self.entry_token,
         self.risorse_rva, self.risorse_dim, self.firma_rva, self.firma_dim) = struct.unpack_from("<IHHIIIIIIII", d, c)

    def offset(self, rva):
        for _, vrva, vdim, rpos, rdim in self.sezioni:
            if vrva <= rva < vrva + max(vdim, rdim):
                return rva - vrva + rpos
        raise ErroreDotnet("RVA 0x%x fuori dalle sezioni" % rva)

    @property
    def solo_32bit(self):
        """True se l'assembly richiede un processo a 32 bit (COMIMAGE_FLAGS_32BITREQUIRED senza 32BITPREFERRED)."""
        return bool(self.cli_flags & 0x2) and not (self.cli_flags & 0x20000)

    @property
    def firmato(self):
        """True se ha una firma con nome sicuro (strong name)."""
        return bool(self.cli_flags & 0x8) and self.firma_dim > 0

    # metadati ------------------------------------------------------------------------------------
    def _metadati(self):
        d = self.dati
        base = self.offset(self.meta_rva)
        if struct.unpack_from("<I", d, base)[0] != 0x424A5342:
            raise ErroreDotnet("firma dei metadati non valida")
        lung = struct.unpack_from("<I", d, base + 12)[0]
        self.versione_runtime = d[base + 16:base + 16 + lung].split(b"\x00")[0].decode("ascii", "replace")
        pos = base + 16 + lung + 2
        n_flussi = struct.unpack_from("<H", d, pos)[0]
        pos += 2
        self.flussi = {}
        for _ in range(n_flussi):
            off, dim = struct.unpack_from("<II", d, pos)
            nome = _cstr(d, pos + 8)
            pos += 8 + ((len(nome) + 4) & ~3)
            self.flussi[nome] = (base + off, dim)
        tabelle = self.flussi.get("#~") or self.flussi.get("#-")
        if not tabelle:
            raise ErroreDotnet("flusso delle tabelle dei metadati mancante")
        self._tabelle(*tabelle)

    def _heap(self, nome):
        if nome not in self.flussi:
            return b""
        pos, dim = self.flussi[nome]
        return self.dati[pos:pos + dim]

    def _tabelle(self, pos, _dim):
        d = self.dati
        heap_dim = d[pos + 6]
        valide = struct.unpack_from("<Q", d, pos + 8)[0]
        p = pos + 24
        self.righe = {}
        for t in range(64):
            if valide >> t & 1:
                self.righe[t] = struct.unpack_from("<I", d, p)[0]
                p += 4
        if heap_dim & 0x40:  # dati extra (rari)
            p += 4
        self._dim_str = 4 if heap_dim & 1 else 2
        self._dim_guid = 4 if heap_dim & 2 else 2
        self._dim_blob = 4 if heap_dim & 4 else 2
        sconosciute = [t for t in self.righe if t not in TABELLE]
        if sconosciute:
            raise ErroreDotnet("tabelle di metadati sconosciute: %s" % sconosciute)
        self._colonne, self._inizio, self._dim_riga = {}, {}, {}
        for t in sorted(self.righe):
            larghezze = [self._larghezza(c) for c in TABELLE[t][1]]
            self._colonne[t] = larghezze
            self._dim_riga[t] = sum(larghezze)
            self._inizio[t] = p
            p += self._dim_riga[t] * self.righe[t]
        self._str = self._heap("#Strings")
        self._blob = self._heap("#Blob")
        self._us = self._heap("#US")

    def _larghezza(self, colonna):
        if isinstance(colonna, int):
            return colonna
        if colonna == "s":
            return self._dim_str
        if colonna == "g":
            return self._dim_guid
        if colonna == "b":
            return self._dim_blob
        tipo, val = colonna
        if tipo == "t":
            return 2 if self.righe.get(val, 0) < 0x10000 else 4
        bit, tab = CODIFICATI[val]
        massimo = max(self.righe.get(t, 0) for t in tab if t is not None)
        return 2 if massimo < (1 << (16 - bit)) else 4

    def riga(self, tabella, indice):
        """Valori grezzi della riga 'indice' (da 1) della tabella."""
        if not 1 <= indice <= self.righe.get(tabella, 0):
            raise ErroreDotnet("riga %d fuori dalla tabella %s" % (indice, TABELLE[tabella][0]))
        p = self._inizio[tabella] + (indice - 1) * self._dim_riga[tabella]
        valori = []
        for larghezza in self._colonne[tabella]:
            valori.append(int.from_bytes(self.dati[p:p + larghezza], "little"))
            p += larghezza
        return valori

    def righe_di(self, tabella):
        return [self.riga(tabella, i) for i in range(1, self.righe.get(tabella, 0) + 1)]

    def stringa(self, indice):
        return _cstr(self._str, indice) if indice else ""

    def blob(self, indice):
        if not indice:
            return b""
        n, p = leggi_compresso(self._blob, indice)
        return self._blob[p:p + n]

    @staticmethod
    def decodifica(codificato, tipo):
        """Indice codificato -> (tabella, riga)."""
        bit, tab = CODIFICATI[tipo]
        return tab[codificato & ((1 << bit) - 1)], codificato >> bit

    # informazioni di alto livello ----------------------------------------------------------------
    def identita(self):
        """Nome, versione, cultura e chiave pubblica dell'assembly."""
        if not self.righe.get(0x20):
            raise ErroreDotnet("manca la tabella Assembly (e' un modulo, non un assembly)")
        _, ma, mi, bu, re, flags, chiave, nome, cultura = self.riga(0x20, 1)
        return {"nome": self.stringa(nome), "versione": "%d.%d.%d.%d" % (ma, mi, bu, re),
                "cultura": self.stringa(cultura), "chiave_pubblica": self.blob(chiave).hex(), "flags": flags}

    def riferimenti(self):
        """Assembly referenziati: [(nome, versione)]."""
        return [(self.stringa(r[6]), "%d.%d.%d.%d" % tuple(r[:4])) for r in self.righe_di(0x23)]

    def risorse(self):
        """Risorse incorporate: {nome: (offset nel file dei dati, lunghezza)}."""
        uscita = {}
        for offset, _flags, nome, impl in self.righe_di(0x28):
            if impl:  # risorsa in un altro file/assembly
                continue
            if not self.risorse_rva:
                raise ErroreDotnet("risorse dichiarate ma directory delle risorse assente")
            p = self.offset(self.risorse_rva) + offset
            n = struct.unpack_from("<I", self.dati, p)[0]
            if offset + 4 + n > self.risorse_dim:
                raise ErroreDotnet("risorsa %s oltre la directory delle risorse" % self.stringa(nome))
            uscita[self.stringa(nome)] = (p + 4, n)
        return uscita

    def leggi_risorsa(self, nome):
        p, n = self.risorse()[nome]
        return self.dati[p:p + n]

    def _nome_tipo(self, tabella, riga):
        if tabella == 0x01:
            _, nome, spazio = self.riga(0x01, riga)
        elif tabella == 0x02:
            _, nome, spazio, _, _, _ = self.riga(0x02, riga)
        else:
            return ""
        return (self.stringa(spazio) + "." if spazio else "") + self.stringa(nome)

    def attributi_assembly(self):
        """Attributi dell'assembly: [(nome completo del tipo, blob del valore)]."""
        uscita = []
        for genitore, tipo, valore in self.righe_di(0x0C):
            tab, r = self.decodifica(genitore, "HasCustomAttribute")
            if tab != 0x20:
                continue
            tab_c, r_c = self.decodifica(tipo, "CustomAttributeType")
            nome = ""
            if tab_c == 0x0A:
                classe, _, _ = self.riga(0x0A, r_c)
                nome = self._nome_tipo(*self.decodifica(classe, "MemberRefParent"))
            elif tab_c == 0x06:  # costruttore definito nello stesso assembly: cerco il tipo proprietario
                nome = self._tipo_del_metodo(r_c)
            uscita.append((nome, self.blob(valore)))
        return uscita

    def _tipo_del_metodo(self, metodo):
        tipi = self.righe_di(0x02)
        for i, t in enumerate(tipi):
            inizio = t[5]
            fine = tipi[i + 1][5] if i + 1 < len(tipi) else self.righe.get(0x06, 0) + 1
            if inizio <= metodo < fine:
                return self._nome_tipo(0x02, i + 1)
        return ""

    def lingua_neutra(self):
        """Valore di [assembly: NeutralResourcesLanguage("..")] e posizione di ripiego (0 = MainAssembly)."""
        for nome, valore in self.attributi_assembly():
            if nome == "System.Resources.NeutralResourcesLanguageAttribute" and valore[:2] == b"\x01\x00":
                if valore[2] == 0xFF:
                    return None, 0
                n, p = leggi_compresso(valore, 2)
                cultura = valore[p:p + n].decode("utf-8")
                p += n
                posizione = struct.unpack_from("<i", valore, p)[0] if len(valore) >= p + 4 + 2 else 0
                return cultura, posizione
        return None, None

    def versione_satelliti(self):
        """Versione che devono avere i satelliti: [SatelliteContractVersion] se c'e', altrimenti la propria."""
        for nome, valore in self.attributi_assembly():
            if (nome == "System.Resources.SatelliteContractVersionAttribute" and valore[:2] == b"\x01\x00"
                    and len(valore) > 2 and valore[2] != 0xFF):
                n, p = leggi_compresso(valore, 2)
                parti = valore[p:p + n].decode("utf-8", "replace").strip().split(".")
                # come csc per AssemblyVersion: "1.0" diventa "1.0.0.0"
                if 2 <= len(parti) <= 4 and all(x.isdigit() and int(x) < 65535 for x in parti):
                    return ".".join(str(int(x)) for x in parti + ["0"] * (4 - len(parti)))
        return self.identita()["versione"]

    def stringhe_codice(self):
        """Stringhe dell'heap #US (letterali scritti nel codice), in ordine di comparsa."""
        us, uscita, p = self._us, [], 1
        while p < len(us):
            n, q = leggi_compresso(us, p)
            if n == 0:
                p = q
                continue
            uscita.append(us[q:q + n - 1].decode("utf-16-le", "replace"))
            p = q + n
        return uscita


# --- file .resources (System.Resources.ResourceWriter) -----------------------------------------

MAGIC_RISORSE = 0xBEEFCACE
LETTORE_PREDEFINITO = ("System.Resources.ResourceReader, mscorlib, Version=4.0.0.0, Culture=neutral, "
                       "PublicKeyToken=b77a5c561934e089")
INSIEME_PREDEFINITO = "System.Resources.RuntimeResourceSet"
TIPO_STRINGA = 1
TIPO_NULL = 0


def _leggi_7bit(dati, pos):
    valore = spostamento = 0
    while True:
        b = dati[pos]
        pos += 1
        valore |= (b & 0x7F) << spostamento
        if not b & 0x80:
            return valore, pos
        spostamento += 7
        if spostamento > 35:
            raise ErroreDotnet("intero a 7 bit non valido")


def _scrivi_7bit(n):
    uscita = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            uscita.append(b | 0x80)
        else:
            uscita.append(b)
            return bytes(uscita)


def _stringa_bw(dati, pos, codifica="utf-8"):
    """Stringa come la scrive BinaryWriter: lunghezza in byte (7 bit) + testo."""
    n, pos = _leggi_7bit(dati, pos)
    return dati[pos:pos + n].decode(codifica, "surrogatepass"), pos + n


def _scrivi_stringa_bw(testo, codifica="utf-8"):
    b = testo.encode(codifica, "surrogatepass")
    return _scrivi_7bit(len(b)) + b


def hash_nome(nome):
    """FastResourceComparer.HashFunction di .NET (djb2 con xor, sui caratteri UTF-16)."""
    h = 5381
    for unita in struct.unpack("<%dH" % (len(nome.encode("utf-16-le", "surrogatepass")) // 2),
                               nome.encode("utf-16-le", "surrogatepass")):
        h = (((h << 5) + h) & 0xFFFFFFFF) ^ unita
    return struct.unpack("<i", struct.pack("<I", h))[0]


def leggi_resources(dati):
    """File .resources -> {"lettore", "insieme", "versione", "tipi", "voci": [(nome, codice_tipo, valore)]}.

    Le voci sono nell'ordine della sezione dati. I valori non stringa restano byte grezzi (non toccati)."""
    try:
        return _leggi_resources(dati)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise ErroreDotnet("file .resources non valido (%s)" % e) from e


def _leggi_resources(dati):
    if len(dati) < 12 or struct.unpack_from("<I", dati, 0)[0] != MAGIC_RISORSE:
        raise ErroreDotnet("non e' un file .resources")
    versione_testa, salto = struct.unpack_from("<ii", dati, 4)
    if versione_testa != 1:
        raise ErroreDotnet("intestazione .resources versione %d non supportata" % versione_testa)
    lettore, p = _stringa_bw(dati, 12)
    insieme, p = _stringa_bw(dati, p)
    if p != 12 + salto:
        raise ErroreDotnet("intestazione .resources non coerente")
    versione, n, n_tipi = struct.unpack_from("<iii", dati, p)
    p += 12
    if versione not in (1, 2):
        raise ErroreDotnet(".resources versione %d non supportata" % versione)
    tipi = []
    for _ in range(n_tipi):
        t, p = _stringa_bw(dati, p)
        tipi.append(t)
    inizio_pad = p
    while p % 8:
        p += 1
    if dati[inizio_pad:p] != b"PADPADP"[:p - inizio_pad]:
        raise ErroreDotnet("riempimento .resources inatteso")
    hash_ = struct.unpack_from("<%di" % n, dati, p)
    p += 4 * n
    posizioni = struct.unpack_from("<%di" % n, dati, p)
    p += 4 * n
    inizio_dati = struct.unpack_from("<i", dati, p)[0]
    inizio_nomi = p + 4
    voci = []
    for h, pos_nome in zip(hash_, posizioni):
        nome, q = _stringa_bw(dati, inizio_nomi + pos_nome, "utf-16-le")
        if hash_nome(nome) != h:
            raise ErroreDotnet("hash del nome '%s' non corrisponde" % nome)
        off_dato = struct.unpack_from("<i", dati, q)[0]
        voci.append((off_dato, nome))
    ordine_hash = [nome for _, nome in voci]  # ordine dell'elenco degli hash (conta per i nomi con hash uguale)
    voci.sort()
    risultato = []
    for k, (off_dato, nome) in enumerate(voci):
        q = inizio_dati + off_dato
        fine = inizio_dati + voci[k + 1][0] if k + 1 < len(voci) else len(dati)
        if versione == 2:
            codice, q2 = _leggi_7bit(dati, q)
        else:
            codice_v1, q2 = _leggi_7bit(dati, q)  # v1: indice nella tabella dei tipi (-1 = null)
            codice = TIPO_STRINGA if 0 <= codice_v1 < len(tipi) and tipi[codice_v1].startswith("System.String") \
                else ("v1", codice_v1)
        if codice == TIPO_STRINGA:
            valore, q3 = _stringa_bw(dati, q2)
            if q3 != fine:
                raise ErroreDotnet("stringa della risorsa '%s' non coerente" % nome)
        else:
            valore = dati[q2:fine]
        risultato.append((nome, codice, valore))
    return {"lettore": lettore, "insieme": insieme, "versione": versione, "tipi": tipi,
            "voci": risultato, "ordine_hash": ordine_hash}


def scrivi_resources(info, nuove=None):
    """Riscrive un .resources versione 2 come ResourceWriter. nuove: {nome: testo} sostituisce le stringhe.

    Le voci non stringa vengono copiate identiche. Ordine e intestazioni come nel file letto."""
    if info["versione"] != 2:
        raise ErroreDotnet("scrittura supportata solo per .resources versione 2")
    nuove = nuove or {}
    testa = _scrivi_stringa_bw(info["lettore"]) + _scrivi_stringa_bw(info["insieme"])
    uscita = bytearray(struct.pack("<Iii", MAGIC_RISORSE, 1, len(testa)) + testa)
    voci = info["voci"]
    uscita += struct.pack("<iii", 2, len(voci), len(info["tipi"]))
    for t in info["tipi"]:
        uscita += _scrivi_stringa_bw(t)
    k = 0
    while len(uscita) % 8:
        uscita += b"PAD"[k % 3:k % 3 + 1]
        k += 1
    nomi, dati_sez, elenco = bytearray(), bytearray(), []
    rango = {nome: k for k, nome in enumerate(info.get("ordine_hash", []))}
    for k, (nome, codice, valore) in enumerate(voci):
        elenco.append((hash_nome(nome), rango.get(nome, len(rango) + k), len(nomi)))
        nomi += _scrivi_stringa_bw(nome, "utf-16-le") + struct.pack("<i", len(dati_sez))
        if codice == TIPO_STRINGA:
            testo = nuove.get(nome, valore)
            dati_sez += _scrivi_7bit(TIPO_STRINGA) + _scrivi_stringa_bw(testo)
        else:
            dati_sez += _scrivi_7bit(codice) + valore
    # hash in ordine crescente; a parita' di hash resta l'ordine del file letto (qualsiasi ordine e' valido:
    # ResourceReader controlla tutti i nomi con lo stesso hash)
    elenco.sort(key=lambda v: (v[0], v[1]))
    uscita += struct.pack("<%di" % len(elenco), *[h for h, _, _ in elenco])
    uscita += struct.pack("<%di" % len(elenco), *[p for _, _, p in elenco])
    uscita += struct.pack("<i", len(uscita) + 4 + len(nomi))
    return bytes(uscita + nomi + dati_sez)


def stringhe_resources(info):
    """{nome: testo} delle sole risorse di tipo stringa."""
    return {nome: valore for nome, codice, valore in info["voci"] if codice == TIPO_STRINGA}
