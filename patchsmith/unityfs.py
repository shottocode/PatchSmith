"""Lettura e scrittura dei file Unity: AssetBundle (UnityFS) e file serializzati al loro interno (sola libreria
standard).

- UnityFS: intestazione, blocchi compressi (LZ4/LZ4HC in puro Python, LZMA con la libreria standard) e i file
  interni. Si riscrive senza compressione (Unity lo accetta); i dati dei file interni restano byte per byte uguali
  tranne quelli cambiati. Indice legge un bundle grande a pezzi, solo i blocchi che servono (sola lettura).
- File serializzato (versioni 17-22): tipi con il loro albero dei campi (type tree), tabella degli oggetti, dati.
  Un oggetto si legge seguendo l'albero dei campi; per cambiare dei testi si riscrivono solo quelle stringhe
  (lunghezza, testo, allineamento a 4) e si aggiornano posizioni e dimensioni nella tabella degli oggetti.
"""
import lzma
import struct


class ErroreUnity(ValueError):
    pass


class _Variabile(Exception):
    """Campo con struttura variabile (es. i dati dei riferimenti gestiti, in fondo all'oggetto)."""

    def __init__(self, percorso):
        Exception.__init__(self, "/".join(map(str, percorso)))
        self.percorso = percorso


# --- LZ4 (formato a blocchi, quello dei bundle) ----------------------------------------------------------------

def lz4_decomprimi(src, dimensione):
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        letterali = token >> 4
        if letterali == 15:
            while True:
                b = src[i]
                i += 1
                letterali += b
                if b != 255:
                    break
        dst += src[i:i + letterali]
        i += letterali
        if i >= n:
            break
        distanza = src[i] | (src[i + 1] << 8)
        i += 2
        if distanza == 0:
            raise ErroreUnity("LZ4 non valido")
        lunghezza = token & 15
        if lunghezza == 15:
            while True:
                b = src[i]
                i += 1
                lunghezza += b
                if b != 255:
                    break
        lunghezza += 4
        inizio = len(dst) - distanza
        if inizio < 0:
            raise ErroreUnity("LZ4 non valido")
        if distanza >= lunghezza:
            dst += dst[inizio:inizio + lunghezza]
        else:
            for k in range(lunghezza):
                dst.append(dst[inizio + k])
    if len(dst) != dimensione:
        raise ErroreUnity("LZ4: dimensione diversa da quella attesa")
    return bytes(dst)


def _decomprimi(dati, tipo, dimensione):
    if tipo == 0:
        return dati
    if tipo in (2, 3):
        return lz4_decomprimi(dati, dimensione)
    if tipo == 1:  # LZMA di Unity: 5 byte di proprieta', poi il flusso senza dimensione
        props = dati[0]
        lc, lp, pb = props % 9, (props // 9) % 5, (props // 9) // 5
        dizionario = struct.unpack_from("<I", dati, 1)[0]
        filtri = [{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dizionario}]
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filtri).decompress(dati[5:])[:dimensione]
    raise ErroreUnity("compressione non supportata (%d)" % tipo)


def _cstringa(dati, p):
    fine = dati.index(b"\x00", p)
    return dati[p:fine].decode("utf-8", "replace"), fine + 1


# --- UnityFS ----------------------------------------------------------------------------------------------------

def _intestazione(leggi, dimensione_file):
    """Intestazione di un AssetBundle: (versione, versione del player, versione di Unity, segni, blocchi, nodi,
    inizio dei dati). leggi(posizione, quanti) restituisce i byte del file; blocchi = [(decompresso, compresso,
    segni)], nodi = [(nome, inizio, dimensione, segni)] con le posizioni nei dati decompressi."""
    testa = leggi(0, 1024)
    if not testa.startswith(b"UnityFS\x00"):
        raise ErroreUnity("non e' un AssetBundle UnityFS")
    try:
        p = 8
        versione = struct.unpack_from(">I", testa, p)[0]
        p += 4
        versione_player, p = _cstringa(testa, p)
        versione_unity, p = _cstringa(testa, p)
        _dimensione, compresso, decompresso, segni = struct.unpack_from(">qIII", testa, p)
    except (struct.error, ValueError) as e:
        raise ErroreUnity("intestazione del bundle non valida") from e
    p += 20
    if versione >= 7:
        p = (p + 15) // 16 * 16
    if segni & 0x80:  # informazioni sui blocchi in fondo al file
        info = leggi(dimensione_file - compresso, compresso)
        inizio_dati = p
    else:
        info = leggi(p, compresso)
        inizio_dati = p + compresso
    if len(info) != compresso:
        raise ErroreUnity("bundle troncato")
    try:
        info = _decomprimi(info, segni & 0x3F, decompresso)
    except (lzma.LZMAError, IndexError, struct.error) as e:
        raise ErroreUnity("indice del bundle non valido (%s)" % type(e).__name__) from e
    if segni & 0x200:
        inizio_dati = (inizio_dati + 15) // 16 * 16
    try:
        q = 16  # impronta (non usata)
        nblocchi = struct.unpack_from(">i", info, q)[0]
        q += 4
        blocchi = []
        for _ in range(nblocchi):
            blocchi.append(struct.unpack_from(">IIH", info, q))
            q += 10
        nnodi = struct.unpack_from(">i", info, q)[0]
        q += 4
        nodi = []
        for _ in range(nnodi):
            spostamento, dimensione, segni_nodo = struct.unpack_from(">qqI", info, q)
            q += 20
            nome, q = _cstringa(info, q)
            nodi.append((nome, spostamento, dimensione, segni_nodo))
    except (struct.error, ValueError) as e:
        raise ErroreUnity("indice del bundle non valido") from e
    return versione, versione_player, versione_unity, segni, blocchi, nodi, inizio_dati


class Bundle:
    """Un AssetBundle: self.file = {nome: bytes} dei file interni, nell'ordine originale."""

    def __init__(self, dati):
        (self.versione, self.versione_player, self.versione_unity, self.segni, blocchi, nodi,
         inizio_dati) = _intestazione(lambda p, n: dati[p:p + n], len(dati))
        pezzi, p = [], inizio_dati
        try:
            for decompresso_b, compresso_b, segni_b in blocchi:
                pezzi.append(_decomprimi(dati[p:p + compresso_b], segni_b & 0x3F, decompresso_b))
                p += compresso_b
        except (lzma.LZMAError, IndexError, struct.error) as e:
            raise ErroreUnity("blocco del bundle non valido (%s)" % type(e).__name__) from e
        tutto = b"".join(pezzi)
        self.nodi = [(nome, segni) for nome, _, _, segni in nodi]
        self.file = {nome: tutto[s:s + d] for nome, s, d, _ in nodi}

    def serializza(self):
        """Il bundle senza compressione (un blocco), con gli stessi file interni."""
        dati = b"".join(self.file[nome] for nome, _ in self.nodi)
        info = bytearray(16)
        info += struct.pack(">i", 1) + struct.pack(">IIH", len(dati), len(dati), 0x40)
        info += struct.pack(">i", len(self.nodi))
        p = 0
        for nome, segni in self.nodi:
            d = len(self.file[nome])
            info += struct.pack(">qqI", p, d, segni) + nome.encode("utf-8") + b"\x00"
            p += d
        segni = 0x40 | (self.segni & 0x200)  # informazioni subito dopo l'intestazione, nessuna compressione
        testa = b"UnityFS\x00" + struct.pack(">I", self.versione) + self.versione_player.encode() + b"\x00" + \
            self.versione_unity.encode() + b"\x00"
        lunghezza_testa = len(testa) + 20
        if self.versione >= 7:
            lunghezza_testa = (lunghezza_testa + 15) // 16 * 16
        inizio_dati = lunghezza_testa + len(info)
        if segni & 0x200:
            inizio_dati = (inizio_dati + 15) // 16 * 16
        totale = inizio_dati + len(dati)
        testa += struct.pack(">qIII", totale, len(info), len(info), segni)
        uscita = testa + b"\x00" * (lunghezza_testa - len(testa)) + bytes(info)
        uscita += b"\x00" * (inizio_dati - len(uscita)) + dati
        return uscita


MAX_BLOCCO = 256 * 1024 * 1024  # blocchi LZ4 piu' grandi (decompressi) non si leggono: troppa memoria in una volta
# (i blocchi senza compressione e quelli LZMA grandi si leggono a pezzi da _PEZZO_LZMA)
_PEZZO_LZMA = 16 * 1024 * 1024


class Indice:
    """Un AssetBundle letto dal file a pezzi, senza decomprimerlo tutto (es. data.unity3d da centinaia di MB): solo
    l'indice dei blocchi e dei file interni (self.nodi = [(nome, inizio, dimensione, segni)]); pezzi() restituisce
    a poco a poco i byte decompressi di una parte."""

    def __init__(self, percorso):
        self._f = open(percorso, "rb")
        try:
            dimensione = self._f.seek(0, 2)
            (self.versione, _player, self.versione_unity, self.segni, self.blocchi, self.nodi,
             self.inizio_dati) = _intestazione(self._leggi, dimensione)
        except BaseException:
            self._f.close()
            raise

    def _leggi(self, posizione, quanti):
        self._f.seek(max(0, posizione))
        return self._f.read(quanti)

    def chiudi(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.chiudi()

    def _senza_compressione(self, posizione, quanti):
        """I byte di un blocco senza compressione, a pezzi da 16 MB: un solo blocco puo' essere grande quanto il
        bundle (anche GB). Un file troncato da' quello che c'e'."""
        letti = 0
        while letti < quanti:
            pezzo = self._leggi(posizione + letti, min(_PEZZO_LZMA, quanti - letti))
            if not pezzo:
                return
            letti += len(pezzo)
            yield pezzo

    def _blocco(self, posizione, compresso, decompresso, tipo):
        """I byte decompressi di un blocco, in uno o piu' pezzi (LZMA a pezzi da 16 MB)."""
        if tipo == 0:
            yield from self._senza_compressione(posizione, compresso)
            return
        if tipo == 1 and decompresso > _PEZZO_LZMA:  # LZMA grande (Unity vecchi): a pezzi, anche in lettura
            testa = self._leggi(posizione, 5)
            if len(testa) < 5:
                raise ErroreUnity("bundle troncato")
            props = testa[0]
            filtri = [{"id": lzma.FILTER_LZMA1, "lc": props % 9, "lp": (props // 9) % 5, "pb": (props // 9) // 5,
                       "dict_size": struct.unpack_from("<I", testa, 1)[0]}]
            d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filtri)
            letti, resto = 5, decompresso
            while resto > 0 and not d.eof:  # flusso finito prima del previsto: un'altra decompress() darebbe EOFError
                entrata = b""
                if d.needs_input:
                    entrata = self._leggi(posizione + letti, min(4 * 1024 * 1024, compresso - letti))
                    letti += len(entrata)
                    if not entrata:
                        break
                pezzo = d.decompress(entrata, max_length=min(_PEZZO_LZMA, resto))
                if pezzo:
                    resto -= len(pezzo)
                    yield pezzo
                elif not entrata and not d.needs_input:
                    break
            return
        if decompresso > MAX_BLOCCO:
            raise ErroreUnity("blocco troppo grande (%d MB)" % (decompresso // (1024 * 1024)))
        yield _decomprimi(self._leggi(posizione, compresso), tipo, decompresso)

    def pezzi(self, inizio=0, fine=None):
        """[(posizione, byte)] decompressi che coprono i dati da 'inizio' a 'fine' (posizioni nei dati
        decompressi, es. un file interno: inizio, inizio + dimensione). Si leggono solo i blocchi che servono."""
        if fine is None:
            fine = sum(b[0] for b in self.blocchi)
        pos_c, pos_u = self.inizio_dati, 0
        for decompresso, compresso, segni in self.blocchi:
            if pos_u >= fine:
                break
            if pos_u + decompresso > inizio and segni & 0x3F == 0:  # senza compressione: solo la parte che serve
                a = max(inizio, pos_u)
                for pezzo in self._senza_compressione(pos_c + a - pos_u,
                                                      min(fine, pos_u + min(decompresso, compresso)) - a):
                    yield a, pezzo
                    a += len(pezzo)
            elif pos_u + decompresso > inizio:
                p = pos_u
                try:
                    for pezzo in self._blocco(pos_c, compresso, decompresso, segni & 0x3F):
                        a, b = max(inizio, p), min(fine, p + len(pezzo))
                        if a < b:
                            yield a, pezzo[a - p:b - p]
                        p += len(pezzo)
                except (lzma.LZMAError, IndexError, struct.error) as e:  # dati rovinati
                    raise ErroreUnity("blocco del bundle non valido (%s)" % type(e).__name__) from e
            pos_c += compresso
            pos_u += decompresso


# --- file serializzato ------------------------------------------------------------------------------------------

_SEMPLICI = {"SInt8": "b", "UInt8": "B", "char": "B", "bool": "?", "SInt16": "h", "UInt16": "H", "short": "h",
             "unsigned short": "H", "SInt32": "i", "UInt32": "I", "int": "i", "unsigned int": "I", "Type*": "I",
             "SInt64": "q", "UInt64": "Q", "long long": "q", "unsigned long long": "Q", "FileSize": "Q",
             "float": "f", "double": "d"}


class Nodo:
    __slots__ = ("tipo", "nome", "dimensione", "livello", "allinea", "figli")

    def __init__(self, tipo, nome, dimensione, livello, allinea):
        self.tipo, self.nome, self.dimensione, self.livello, self.allinea = tipo, nome, dimensione, livello, allinea
        self.figli = []


_STRINGHE_COMUNI = None


def _stringhe_comuni():
    """Le stringhe predefinite dei type tree (quando il bit alto dell'offset e' 1)."""
    global _STRINGHE_COMUNI
    if _STRINGHE_COMUNI is None:
        testo = ("AABB\x00AnimationClip\x00AnimationCurve\x00AnimationState\x00Array\x00Base\x00BitField\x00bitset\x00"
                 "bool\x00char\x00ColorRGBA\x00Component\x00data\x00deque\x00double\x00dynamic_array\x00FastPropertyName"
                 "\x00first\x00float\x00Font\x00GameObject\x00Generic Mono\x00GradientNEW\x00GUID\x00GUIStyle\x00int\x00"
                 "list\x00long long\x00map\x00Matrix4x4f\x00MdFour\x00MonoBehaviour\x00MonoScript\x00m_ByteSize\x00"
                 "m_Curve\x00m_EditorClassIdentifier\x00m_EditorHideFlags\x00m_Enabled\x00m_ExtensionPtr\x00"
                 "m_GameObject\x00m_Index\x00m_IsArray\x00m_IsStatic\x00m_MetaFlag\x00m_Name\x00m_ObjectHideFlags\x00"
                 "m_PrefabInternal\x00m_PrefabParentObject\x00m_Script\x00m_StaticEditorFlags\x00m_Type\x00m_Version\x00"
                 "Object\x00pair\x00PPtr<Component>\x00PPtr<GameObject>\x00PPtr<Material>\x00PPtr<MonoBehaviour>\x00"
                 "PPtr<MonoScript>\x00PPtr<Object>\x00PPtr<Prefab>\x00PPtr<Sprite>\x00PPtr<TextAsset>\x00PPtr<Texture>"
                 "\x00PPtr<Texture2D>\x00PPtr<Transform>\x00Prefab\x00Quaternionf\x00Rectf\x00RectInt\x00RectOffset\x00"
                 "second\x00set\x00short\x00size\x00SInt16\x00SInt32\x00SInt64\x00SInt8\x00staticvector\x00string\x00"
                 "TextAsset\x00TextMesh\x00Texture\x00Texture2D\x00Transform\x00TypelessData\x00UInt16\x00UInt32\x00"
                 "UInt64\x00UInt8\x00unsigned int\x00unsigned long long\x00unsigned short\x00vector\x00Vector2f\x00"
                 "Vector3f\x00Vector4f\x00m_ScriptingClassIdentifier\x00Gradient\x00Type*\x00int2_storage\x00"
                 "int3_storage\x00BoundsInt\x00m_CorrespondingSourceObject\x00m_PrefabInstance\x00m_PrefabAsset\x00"
                 "FileSize\x00Hash128\x00RenderingLayerMask\x00")
        _STRINGHE_COMUNI, p = {}, 0
        for parte in testo.split("\x00")[:-1]:
            _STRINGHE_COMUNI[p] = parte
            p += len(parte.encode("utf-8")) + 1
    return _STRINGHE_COMUNI


def _testo_albero(buffer, offset):
    if offset & 0x80000000:
        return _stringhe_comuni().get(offset & 0x7FFFFFFF, "?")
    return _cstringa(buffer, offset)[0]


class Oggetto:
    __slots__ = ("path_id", "inizio", "dimensione", "tipo", "pos_inizio", "pos_dimensione")


class FileSerializzato:
    """File serializzato di Unity (versioni 17-22, little endian, con type tree)."""

    def __init__(self, dati):
        self.dati = dati
        ms, fs, versione, inizio_dati = struct.unpack_from(">IIII", dati, 0)
        self.versione = versione
        if not 17 <= versione <= 22:
            raise ErroreUnity("versione del file serializzato non supportata (%d)" % versione)
        if dati[16] != 0:
            raise ErroreUnity("file serializzato big endian non supportato")
        p = 20
        if versione >= 22:
            ms, fs, inizio_dati, _ = struct.unpack_from(">IqqQ", dati, p)
            p += 28
        self.inizio_dati = inizio_dati
        self.versione_unity, p = _cstringa(dati, p)
        p += 4  # piattaforma
        self.con_alberi = dati[p] != 0
        p += 1
        ntipi = struct.unpack_from("<i", dati, p)[0]
        p += 4
        self.tipi = []
        for _ in range(ntipi):
            tipo, p = self._leggi_tipo(p, False)
            self.tipi.append(tipo)
        nogg = struct.unpack_from("<i", dati, p)[0]
        p += 4
        self.oggetti = []
        for _ in range(nogg):
            p = (p + 3) // 4 * 4
            o = Oggetto()
            o.path_id = struct.unpack_from("<q", dati, p)[0]
            p += 8
            o.pos_inizio = p
            if versione >= 22:
                o.inizio = struct.unpack_from("<q", dati, p)[0]
                p += 8
            else:
                o.inizio = struct.unpack_from("<I", dati, p)[0]
                p += 4
            o.pos_dimensione = p
            o.dimensione, o.tipo = struct.unpack_from("<Ii", dati, p)
            p += 8
            self.oggetti.append(o)

    def _leggi_tipo(self, p, riferimento):
        d = self.dati
        classe = struct.unpack_from("<i", d, p)[0]
        p += 4 + 1 + 2  # classe, stripped, indice dello script
        if (riferimento and True) or classe == 114:
            p += 16
        p += 16  # impronta
        radice = None
        if self.con_alberi:
            nnodi, lbuffer = struct.unpack_from("<ii", d, p)
            p += 8
            dim_nodo = 32 if self.versione >= 19 else 24
            grezzi = [struct.unpack_from("<HBBIIiii", d, p + k * dim_nodo) for k in range(nnodi)]
            p += nnodi * dim_nodo
            buffer = d[p:p + lbuffer]
            p += lbuffer
            pila = []
            for _ver, livello, _flags, off_tipo, off_nome, dimensione, _indice, meta in grezzi:
                nodo = Nodo(_testo_albero(buffer, off_tipo), _testo_albero(buffer, off_nome), dimensione, livello,
                            bool(meta & 0x4000))
                while pila and pila[-1].livello >= livello:
                    pila.pop()
                if pila:
                    pila[-1].figli.append(nodo)
                else:
                    radice = nodo
                pila.append(nodo)
            if self.versione >= 21:
                if riferimento:
                    for _ in range(3):
                        _, p = _cstringa(d, p)
                else:
                    ndip = struct.unpack_from("<i", d, p)[0]
                    p += 4 + 4 * ndip
        return {"classe": classe, "albero": radice}, p

    def dati_oggetto(self, o):
        return self.dati[self.inizio_dati + o.inizio:self.inizio_dati + o.inizio + o.dimensione]

    def stringhe(self, o, valori=()):
        """[(percorso dei campi, inizio, fine)] di tutte le stringhe dell'oggetto (posizioni nei suoi dati). Con
        'valori' (nomi di campi numerici, es. 'm_Unicode') anche [(percorso, 'valore', numero)] di quei campi."""
        albero = self.tipi[o.tipo]["albero"]
        if albero is None:
            raise ErroreUnity("il file non ha gli alberi dei campi (type tree)")
        uscita = []
        try:
            _leggi(albero, self.dati_oggetto(o), 0, [], uscita, set(valori))
        except _Variabile as e:
            # campi a struttura variabile (es. 'references' dei riferimenti gestiti, sempre in fondo): le stringhe
            # lette fin li' valgono; quelle dentro quei campi non si toccano
            uscita.append((("...",) + tuple(e.percorso), None, None))
        return uscita

    def con_testi_nuovi(self, nuovi):
        """Il file con le stringhe cambiate: nuovi = {path_id: {(inizio, fine): testo}}. Si riscrivono solo quelle
        stringhe; gli oggetti si spostano (allineati a 8 come fa Unity) e la tabella degli oggetti si aggiorna."""
        testa = bytearray(self.dati[:self.inizio_dati])
        corpo = bytearray()
        for o in sorted(self.oggetti, key=lambda x: x.inizio):
            vecchio = self.dati_oggetto(o)
            cambi = nuovi.get(o.path_id)
            if cambi:
                nuovo, ultimo = bytearray(), 0
                for (a, b), testo in sorted(cambi.items()):
                    nuovo += vecchio[ultimo:a]
                    t = testo.encode("utf-8")
                    nuovo += struct.pack("<i", len(t)) + t
                    nuovo += b"\x00" * (-len(nuovo) % 4)
                    vecchia_fine = b + (-b % 4)  # la stringa vecchia con il suo allineamento
                    ultimo = vecchia_fine
                nuovo += vecchio[ultimo:]
                vecchio = bytes(nuovo)
            corpo += b"\x00" * (-len(corpo) % 8)
            inizio = len(corpo)
            corpo += vecchio
            if self.versione >= 22:
                struct.pack_into("<q", testa, o.pos_inizio, inizio)
            else:
                struct.pack_into("<I", testa, o.pos_inizio, inizio)
            struct.pack_into("<I", testa, o.pos_dimensione, len(vecchio))
        totale = len(testa) + len(corpo)
        if self.versione >= 22:
            struct.pack_into(">q", testa, 24, totale)
        else:
            struct.pack_into(">I", testa, 4, totale)
        return bytes(testa + corpo)


def _leggi(nodo, d, p, percorso, uscita, valori=frozenset()):
    """Legge il campo 'nodo' dalla posizione p; registra le stringhe; restituisce la posizione dopo il campo."""
    percorso = percorso + [nodo.nome]
    if nodo.tipo == "string":
        n = struct.unpack_from("<i", d, p)[0]
        uscita.append((tuple(percorso), p, p + 4 + n))
        p += 4 + n
        if nodo.allinea or (nodo.figli and nodo.figli[0].allinea):
            p += -p % 4
        return p
    if not nodo.figli:
        if nodo.tipo in _SEMPLICI:
            if nodo.nome in valori:
                uscita.append((tuple(percorso), "valore", struct.unpack_from("<" + _SEMPLICI[nodo.tipo], d, p)[0]))
            p += struct.calcsize("<" + _SEMPLICI[nodo.tipo])
        elif nodo.dimensione > 0:
            p += nodo.dimensione
        else:
            raise _Variabile(percorso)
    elif nodo.figli[0].tipo == "Array" or nodo.tipo == "Array":
        vettore = nodo if nodo.tipo == "Array" else nodo.figli[0]
        n = struct.unpack_from("<i", d, p)[0]
        p += 4
        elemento = vettore.figli[1]
        if not elemento.figli and elemento.tipo in _SEMPLICI:
            p += n * struct.calcsize("<" + _SEMPLICI[elemento.tipo])
        else:
            for k in range(n):
                p = _leggi(elemento, d, p, percorso + [k], uscita, valori)
        if vettore.allinea and vettore is not nodo:
            p += -p % 4
    else:
        for figlio in nodo.figli:
            p = _leggi(figlio, d, p, percorso, uscita, valori)
    if nodo.allinea:
        p += -p % 4
    return p
