"""Deutscher Handbuchinhalt für gen_docs.py — gleiche Blockstruktur wie manual_en.EN."""

DE = [
    ("h1", "1  Überblick"),
    ("p", "FT-950 Remote ist eine Web-Fernbedienung für den Yaesu FT-950, einen "
          "KW/6-m-Transceiver. Die Software läuft auf einem Raspberry Pi neben dem "
          "Funkgerät, spricht über ein Yaesu SCU-17 direkt mit ihm — CAT mit 38400 Bd "
          "und USB-Audio — und stellt die ganze Station als eine einzige HTTPS-Seite "
          "bereit. Kein Hamlib, keine Desktop-Software: Jedes Bedienelement ist auf "
          "den dokumentierten CAT-Befehl des Funkgeräts abgebildet und wird durch "
          "Zurücklesen des Werts überprüft."),
    ("p", "Der Schwerpunkt liegt auf Digitalbetrieb (FT8/FT4 mit dem Decoder von "
          "WSJT-X) und Fern-Sprechfunk über WebRTC. Dazu kommen ein CW-Skimmer, ein "
          "HackRF-Panoramaempfang von einem zweiten Pi, Logbuchführung in Wavelog und "
          "QRZ.com sowie das Ein- und Ausschalten aus der Ferne über ein Shelly-Relais."),
    ("img", "screenshots/vfo.png", 174),
    ("h1", "2  Funktionen"),
    ("ul", [
        "VFO-Feld mit einem LCD nach dem Vorbild des FT-950-Displays: Analoginstrument "
        "im Stil des FT-2000, Signalweg als beleuchtete Felder, Betriebsartenlampen, "
        "DSP-Balken, Sieben-Segment-Frequenzanzeige, Ablage (Clarifier), VFO-B sowie "
        "PO/SWR/ALC/ID/AF-Balken beim Senden.",
        "Schalter in der Titelleiste: Split, ANT 1/2, Tuner und ATU-Abstimmung, "
        "CLAR/CLR, FLT · IPO · R.FLT · AGC, VOICE PROFILE und FT8 PROFILE.",
        "Abstimmen per Mausrad oder durch Ziehen an einer Ziffer, Frequenzeingabe, "
        "Band- und Betriebsartentasten, ein Frequenz-Shuttle, CTCSS (ENC/TSQ), MON, "
        "NAR und eine skalierte Einstellung der Sendeleistung.",
        "Sendetaste im Browser mit Watchdog, PTT-LOCK, Roger-Piep, 1750-Hz-Rufton, "
        "MUTE und DROP; 32 Speichertasten (Klick ruft ab, 3 s halten speichert).",
        "Audio in beide Richtungen über WebRTC/Opus: RX/TX-Pegel, Mikrofon-AGC, "
        "Kompressor, Höhenanhebung (Pre-Emphasis), Mikrofon-Echotest, "
        "Empfangsrekorder mit WAV-Download.",
        "FT8/FT4-Dekodierung mit jt9 auf drei CPU-Kernen, Ablaufsteuerung wie in "
        "WSJT-X mit automatischem Antworten, Wasserfall, DXCC- und "
        "Worked-before-Markierung.",
        "CW-Skimmer: Alle CW-Signale im Durchlassbereich werden gleichzeitig "
        "dekodiert; gesendet wird über den eingebauten Keyer des Funkgeräts, mit Makros.",
        "HackRF-Wasserfall, weitergereicht von TM-V71 Remote, der dem Amateurfunkband "
        "von VFO A folgt.",
        "Logbuch in Wavelog und QRZ.com; Worked-before-Index aus Wavelog.",
        "Stromversorgung des Funkgeräts über ein Shelly-Relais mit automatischer "
        "Abschaltung.",
        "Dunkles und helles Design, 90 % Zoom der Felder, als PWA installierbar.",
    ]),

    ("h1", "3  Hardware und Einrichtung des Funkgeräts"),
    ("h2", "Was man braucht"),
    ("ul", [
        "Raspberry Pi mit Debian 13 / Raspberry Pi OS. Ein Raspberry Pi 5 wird "
        "dringend empfohlen: Die FT8/FT4-Dekodierung mit jt9 ist die aufwendigste "
        "Aufgabe, und der Pi 5 dekodiert ein volles Band in einem Bruchteil der Zeit "
        "eines Pi 4. Ein Pi 4 funktioniert, braucht bei Tiefe D2 aber 4–6 s pro "
        "Periode statt 1,5–2 s.",
        "Yaesu SCU-17 zwischen Pi und Funkgerät (USB zum Pi; CAT- und "
        "RTTY/DATA-Kabel zum Funkgerät).",
        "Optional: ein zweiter Pi mit TM-V71 Remote und einem HackRF One "
        "(Panoramaempfang), ein Shelly-Relais in der 13,8-V-Zuleitung des Funkgeräts "
        "(Fernein- und -ausschalten).",
    ]),
    ("h2", "Das SCU-17 am Pi"),
    ("p", "Im SCU-17 stecken ein CP2105-Doppel-UART und ein USB-Codec TI PCM2903B. "
          "Der „Enhanced“-Port des CP2105 (Interface 0) führt CAT, der "
          "„Standard“-Port PTT/CW über RTS/DTR. Der Codec ist der Audioweg zur und "
          "von der DATA-Buchse des Funkgeräts. Tragen Sie in den Einstellungen den "
          "festen Pfad unter /dev/serial/by-id ein — er bleibt auch nach erneutem "
          "Einstecken gleich."),
    ("code", "/dev/serial/by-id/usb-Silicon_Labs_CP2105_..._if00-port0   CAT, 38400 Bd 8N1\n"
             "/dev/serial/by-id/usb-Silicon_Labs_CP2105_..._if01-port0   PTT/CW (RTS/DTR)\n"
             "hw:CARD=CODEC,DEV=0                                        Audio ein/aus"),
    ("h2", "Menüs am Funkgerät"),
    ("ul", [
        "026 CAT RATE = 38400 Bd (3), 027 CAT TOT = 10 ms (0).",
        "031 TX TOT als Sicherheitsnetz für den Fernbetrieb.",
        "050 DATA TX GAIN — Sendepegel von der DATA-Buchse (FT8, TUNE, Sprache). "
        "Mit TUNE so einstellen, dass die Leistung erreicht wird und ALC gerade "
        "eben anspricht.",
        "051 DATA OUTPUT LEVEL — Empfangspegel zum Pi (der Codec hat keinen "
        "Aufnahmeregler).",
        "054/055 (DATA-Shift / VOX) wie in docs/FT8-DECODING.md beschrieben.",
    ]),
    ("p", "Audio vom Pi erreicht das Funkgerät über die DATA-Buchse, die der FT-950 "
          "in den DATA-Betriebsarten (PKT) verwendet. Fern-Sprechfunk läuft deshalb in "
          "DATA-U / DATA-L — das VOICE PROFILE richtet das für das aktuelle Band ein."),

    ("h1", "4  Installation"),
    ("code", "sudo apt install wsjtx            # jt9, ft8code, ft4code\n"
             "git clone https://github.com/CQ-DJ0SH/ft950-remote /opt/ft950-remote\n"
             "cd /opt/ft950-remote/backend\n"
             "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
             "mkdir -p certs && cd certs && bash ../../deploy/gen-server-cert.sh /path/to/ca\n"
             "sudo cp ../../deploy/systemd/ft950-remote.service /etc/systemd/system/\n"
             "sudo systemctl enable --now ft950-remote"),
    ("p", "Der Dienst ist unter https://<pi>:8444/ erreichbar. Das Zertifikat ist von "
          "einer privaten Root-CA signiert (gemeinsam mit TM-V71 Remote), mit "
          "zufälliger Seriennummer und der eigenen Adresse des Pi; die CA lässt sich "
          "unter Settings ▸ General und auf dem Ausschalt-Schild herunterladen. "
          "Browser geben das Mikrofon nur über HTTPS frei — installieren Sie die CA "
          "daher einmal auf jedem Gerät."),
    ("p", "Danach Settings ▸ General öffnen: Rufzeichen, Locator, CAT-Port und "
          "Baudrate. Stationsdaten, Logbuch-Zugangsdaten und Speichernamen werden in "
          "backend/runtime.json (Rechte 0600) abgelegt und nie an den Browser "
          "zurückgeschickt."),

    ("h1", "5  Die Seite"),
    ("img", "manual/titlebar.png", 174),
    ("p", "Die Titelleiste zeigt das Logo (wird unter Settings hochgeladen und ist "
          "nie Teil des Repositorys), Ihr Rufzeichen (mit durchgestrichener Null), das "
          "Typenschild, die Lampen CAT und USB AUDIO, die UTC-Uhr, den "
          "Design-Umschalter, Settings und — sobald ein Shelly eingerichtet ist — den "
          "Netzschalter."),
    ("p", "Darunter folgen die Felder VFO, PTT · MEMORY und AUDIO, DIGI, CW, HackRF "
          "und LOG. Die meisten lassen sich mit ▲ einklappen; PTT und AUDIO bleiben "
          "immer offen. Die Felder werden mit 90 % Zoom dargestellt, damit die ganze "
          "Station auf einen Bildschirm passt."),

    ("h1", "6  VFO-Feld"),
    ("img", "manual/vfo.png", 174),
    ("h2", "Titelleiste"),
    ("ul", [
        "Lampen LOCK und PLL: Abstimmsperre, PLL nicht eingerastet (ein Gerätefehler).",
        "SPLIT: Empfang auf VFO A, Senden auf VFO B.",
        "ANT 1 / ANT 2, TUNER (interner Antennentuner ein/aus), ATU TUNE (startet "
        "einen Abstimmvorgang — das Funkgerät sendet einen Träger; mit Rückfrage).",
        "CLAR schaltet die Empfangsablage (Clarifier), CLR setzt sie auf 0.",
        "FLT · IPO · R.FLT · AGC schalten durch VRF/THRU, IPO/AMP1/AMP2, "
        "AUTO/15k/6k/3k und OFF/FAST/MID/SLOW/AUTO; das LCD zeigt den Zustand.",
        "VOICE PROFILE und FT8 PROFILE: lesen jeden Wert, schreiben nur das, was "
        "abweicht, prüfen durch Zurücklesen und melden, was geändert wurde.",
    ]),
    ("h2", "LCD"),
    ("img", "manual/lcd.png", 174),
    ("p", "Linkes Fenster: das Analoginstrument — S-Meter beim Empfang, PO beim "
          "Senden — mit den Skalen des FT-2000 (S, PO, COMP, ID, SWR, ALC/VDD) und "
          "den Anzeigen MONI/NB/TUNER/MIC EQ."),
    ("p", "Rechtes Fenster: der Signalweg ANT · ATT · FLT · IPO · R.FLT · AGC als "
          "feste Felder (nur die aktiven Werte leuchten), BUSY, die "
          "Betriebsartenlampen LSB USB CW AM FM RTTY PKT REV (bei DATA-U leuchten PKT "
          "+ USB, bei CW-R CW + REV), TX-Lampe und eingestellte Leistung, die "
          "PO/SWR/ALC/ID/AF-Balken, die DSP-Balken, die Frequenz, die Ablageskala "
          "(rote Marke = Ablage, ±1 kHz) mit ihrem Wert sowie VFO-B."),
    ("p", "PO und SWR arbeiten mit ungefähren Kalibrierpunkten; ID (Ampere) folgt "
          "den 5/10/15-A-Marken des Instruments; AF zeigt bei Sprache den "
          "Mikrofonpegel und bei FT8/TUNE die Tonansteuerung. Nach einer Durchgabe "
          "bleiben die letzten Werte stehen. Die Nadel bewegt sich wie bei einem "
          "Drehspulinstrument: gedämpft, sie steigt in etwa 0,35 s an und fällt "
          "etwas langsamer zurück."),
    ("h2", "Abstimmen"),
    ("ul", [
        "Mausrad oder Ziehen an einer Ziffer der LCD-Frequenz.",
        "Frequenzeingabe mit ▲▼ (UP/DN am Mikrofon), A⇄B und A→B.",
        "Die BAND- und MODE-Tasten leuchten sofort auf und bestätigen, sobald das "
        "Funkgerät zurückmeldet.",
        "Shuttle neben BAND: links der Mitte halten stimmt abwärts, rechts aufwärts; "
        "je weiter außen, desto größer die Schritte (10 Hz, 100 Hz, 1 kHz, 10 kHz).",
        "CTCSS: aus / ENC (Ton senden) / TSQ (Tonsquelch) und die 50 Töne, die das "
        "Funkgerät selbst erzeugt. MON schaltet die Mithörkontrolle. NAR wählt die "
        "schmalen Filter.",
    ]),
    ("h2", "TX POWER und RX · FILTER · LEVEL"),
    ("p", "Der rote Regler TX POWER stellt die Sendeleistung von 5–100 W ein. "
          "RX · FILTER · LEVEL öffnet die Gruppe mit Vorverstärker, AGC, "
          "Roofing-Filter, Bandbreite, Shift, DSP (DNF/NR/NB), ATT, HF-Verstärkung "
          "und Leistung."),

    ("h1", "7  PTT · Speicher"),
    ("img", "manual/ptt.png", 174),
    ("ul", [
        "PTT: die Taste oder die Leertaste gedrückt halten. Der Browser erneuert "
        "jede Sekunde eine Freigabe — bleibt die Seite stumm, nimmt der Pi die PTT "
        "nach 2,5 s weg. Höchstens 180 s pro Durchgabe. Das Mikrofon ist "
        "stummgeschaltet, solange Sie nicht senden.",
        "PTT-LOCK hält das Senden fest; ROGER hängt einen Roger-Piep an; 1.750 kHz "
        "wählt den Relais-Rufton vor: Das nächste PTT sendet 1750 Hz statt des "
        "Mikrofons, beim Loslassen erlischt die Vorwahl (FM-Betriebsarten).",
        "MUTE schaltet den Empfangston stumm (absteigender Gong), DROP blendet nur "
        "die laufende Durchgabe aus und hebt sich auf, sobald die Gegenstation "
        "aufhört (aufsteigender Gong).",
        "PTT → VFO zeigt, auf welchem VFO gesendet wird. In der installierten PWA "
        "stehen links und rechts der PTT-Taste Pegelbalken für RX und MIC.",
        "Speichertasten M1–M32 (2 × 8 je Seite): Klick holt Frequenz und Betriebsart auf "
        "VFO A, in FM mit CTCSS, dazu VFO B und Split, wenn die Taste sie hat; "
        "3 s halten speichert VFO A in diesen Speicherkanal (mit Rückfrage vor dem "
        "Überschreiben).",
    ]),
    ("p", "Unter Settings ▸ Memories lassen sich alle 32 Speicherkanäle bearbeiten: "
          "Frequenz, Betriebsart und CTCSS (Art und Ton) gehen mit MW ins Funkgerät "
          "und werden mit MR geprüft; Split, VFO B und ein Name liegen auf dem Pi, "
          "weil die Speicher des FT-950 nichts davon kennen. DEFAULTS (M1-M32) belegt "
          "je Tastenzeile ein Band für 80/40/20/15/10/6 m — links SSB | CW auf den "
          "Anruffrequenzen, rechts FT8 | FT4 —, dazu FM auf 10/6 m, das 10-m-Relais "
          "(Split −100 kHz), SSTV und die Notfunk-Frequenzen der IARU Region 1."),

    ("h1", "8  Audio"),
    ("img", "manual/audio.png", 174),
    ("p", "CONNECT startet WebRTC: Der Ton des Funkgeräts wird im Browser "
          "wiedergegeben, das Mikrofon geht beim Senden zum Funkgerät. RX GAIN und "
          "TX GAIN stellen die Pegel ein. Die Schalter in der Titelzeile: AGC führt "
          "das Mikrofon auf einen Zielpegel nach; MIC TEST nimmt Ihr Mikrofon über "
          "den gesamten Signalweg auf und spielt es wieder ab, ohne zu senden — in "
          "Hörlautstärke, die Pegelanzeigen zeigen weiter den echten Sendepegel. Am "
          "Desktop bleibt die Mikrofon-Verarbeitung des Browsers an (ausgeschaltet "
          "fällt der Pegel unter macOS um etwa 40 dB); ein eingebautes Mikrofon hat "
          "Vorrang vor einem Bluetooth-Headset."),
    ("p", "REC zeichnet den Empfangston auf dem Pi auf (bis zu 30 Minuten, 16 kHz), "
          "▶ spielt ihn ab, ↓ WAV lädt ihn herunter. Settings ▸ Audio bietet "
          "zusätzlich Kompressor und Höhenanhebung für SSB-Sprache, die "
          "FT8-Sendeansteuerung, den Ausgangspegel des Codecs und die Audiomenüs des "
          "FT-950 mit gespeicherten Grundwerten."),
    ("p", "Der Graph ist geteilt: oben die Pegel der letzten 30 s, darunter das "
          "Audio-Spektrum 0–3 kHz (der SSB/DATA-Durchlassbereich des FT-950) — "
          "der Empfangston violett mit gelbem Peak Hold, der nach etwa 10 s "
          "abfällt, Ihr Mikrofon rot, solange Sie senden oder MIC TEST läuft. "
          "PEAK in der Legende schaltet den Peak Hold. Das Spektrum wird im "
          "Browser berechnet und startet mit CONNECT."),
    ("diagram", "audio", "de"),

    ("h1", "9  DIGI — FT8 / FT4"),
    ("img", "manual/digi.png", 174),
    ("p", "DECODE in der Titelzeile des Felds schaltet die Dekodierung ein und aus "
          "(wie beim CW-Skimmer). Dekodiert wird in jeder Periode mit jt9 aus WSJT-X. Der Durchlassbereich "
          "wird auf drei Kerne verteilt. Die Tiefe wählen D1/D2/D3/AUTO in der "
          "Leiste: D2 (Standard) bringt die Dekodierungen auf dem Pi 5 etwa 1,5–2 s "
          "nach Periodenende, D3 findet ein paar schwache Signale mehr und braucht "
          "etwa 3 s, AUTO richtet die Tiefe nach der gemessenen Laufzeit. "
          "Empfangene Meldungen zeigen SNR, Zeitversatz, Frequenz, Text und DXCC, "
          "farblich nach Worked-before markiert."),
    ("ul", [
        "Klick auf eine Dekodierung legt das Empfangsfenster dorthin (bei AF wandert "
        "auch TX mit); Doppelklick antwortet der Station — in Received und in My "
        "Messages.",
        "Die Filter über Received Messages zeigen alles, nur CQ, nur RR73 (samt RRR) "
        "oder nur 73. follow hält in jeder Liste die neueste Zeile im Blick, auch in "
        "Session Messages.",
        "Eine markierte Session (Klick auf die Zeile) öffnet mit QRZ die Seite der "
        "Station auf QRZ.com und mit Map eine Weltkarte: Standort nach Locator (ohne "
        "Locator die Mitte des DXCC-Gebiets), Großkreis, Entfernung und Richtung ab "
        "dem eigenen Locator. Beide öffnen ein Popup-Fenster.",
        "CQ ruft CQ; Answer ist der Schalter für automatisches Antworten: Im "
        "Ruhezustand wird eine Station, die Sie anruft, automatisch beantwortet.",
        "Die Ablaufsteuerung folgt WSJT-X: Anruf, Rapport, R-Rapport, RR73, 73; "
        "mit Auto-log im Kopf der Sessions (Standard an) wird jedes fertige QSO "
        "automatisch geloggt, sonst landet es im Logformular. Statt RR73 kann sie RRR "
        "senden (Settings ▸ FT8).",
        "Der Schieberegler mit dem Lautsprecher stellt den DIGI-Empfangspegel auf "
        "dem Pi ein, der mit der Antenne daneben den FT8/FT4-Sendepegel (wie "
        "FT8 TX DRIVE in Settings ▸ Audio); TUNE sendet einen Dauerton. Halt TRX rechts in der Titelzeile "
        "ist nur bei einer FT8/FT4-Sendung oder während einer Sequenz aktiv (sonst abgedunkelt) und bricht sofort ab.",
    ]),

    ("h1", "10  CW-Skimmer"),
    ("img", "manual/cw.png", 174),
    ("p", "DECODE startet den Skimmer. Jeder CW-Träger im Durchlassbereich bekommt "
          "einen eigenen Decoder; die Tabelle zeigt Tonhöhe, HF-Frequenz, Tempo, SNR "
          "und den laufenden Text. Ein Klick auf eine Zeile verfolgt dieses Signal: "
          "Sein Text erscheint unter Text, ZERO BEAT stimmt VFO A genau darauf ab."),
    ("p", "Gesendet wird mit dem eingebauten Keyer des Funkgeräts: Der Text wird in "
          "Keyer-Speicher 5 geschrieben und abgespielt (KM/KY), in Stücken zu 50 "
          "Zeichen. Das Funkgerät muss in CW oder CW-R stehen und BK-IN muss "
          "eingeschaltet sein. Makros (CQ, CALL, 5NN, RR, 73, AGN, QRZ) füllen die "
          "Sendezeile; CALL wird vom ausgewählten Signal übernommen."),

    ("h1", "11  HackRF-Wasserfall"),
    ("img", "manual/hackrf.png", 174),
    ("p", "Der HackRF One hängt am zweiten Pi mit TM-V71 Remote. FT-950 Remote "
          "reicht dessen Wasserfall weiter (Settings ▸ General ▸ HackRF source). Mit "
          "follow zeigt die Ansicht das ganze Amateurfunkband von VFO A; ein Klick "
          "stimmt VFO A dorthin ab. Der HackRF wird gemeinsam genutzt: Was Sie hier "
          "einstellen, gilt auch für den Wasserfall des TM-V71."),
    ("p", "In der Bandansicht schickt TM-V71 Remote eine feine Zeile nur für dieses "
          "Band (FFT mit 8192 Punkten, 244 Hz pro Punkt, über 4 Blöcke gemittelt) — "
          "820 Punkte für 40 m statt etwa 50 aus der normalen 2-MHz-Zeile. Die Maus "
          "zeigt Frequenz und Pegel unter dem Zeiger."),

    ("h1", "12  Logbuch"),
    ("p", "Das Feld LOG loggt in Wavelog (API-Schlüssel und Stationsprofil) und "
          "wahlweise ins QRZ.com-Logbuch; QRZ LOOKUP füllt Name, QTH und Locator aus. "
          "Der Worked-before-Index wird aus Wavelog geladen und färbt die FT8-Liste. "
          "Unter Settings ▸ Log stehen die Zugänge — die Schlüssel verlassen den Pi nie."),

    ("h1", "13  Einstellungen"),
    ("img", "manual/set-general.png", 120),
    ("ul", [
        "General: Rufzeichen, Locator, CAT-Port und Baudrate, Shelly und "
        "automatische Abschaltung, HackRF-Quelle, Logo, Root-CA.",
        "FT8: Dekodiertiefe (wie D1/D2/D3/AUTO im Feld) und -bereich, Abschluss "
        "RR73 oder RRR, die Farben der Meldungslisten (CQ, CQ schon gearbeitet, an "
        "mich, eigene Sendungen, NEW) mit Vorschau und Zurücksetzen.",
        "Audio: Aufnahmegerät, Codec-Pegel, FT8-Sendeansteuerung, die Audiomenüs des "
        "FT-950, Sprachaufbereitung beim Senden, WebRTC-Puffer.",
        "Log: Wavelog und QRZ.com.",
        "Memories: die 32 Speichertasten mit CTCSS, Split und VFO B.",
        "Pi Hardware: Last, Temperatur, Arbeitsspeicher, Datenträger und ein "
        "24-h-Verlauf.",
    ]),
    ("p", "Jeder Reiter endet mit derselben Leiste: CLOSE, bei General und Log dazu "
          "SAVE. Unter Audio wirken Änderungen sofort, unter Memories hat jede "
          "Zeile ihr eigenes WRITE, Pi Hardware ist nur zur Ansicht."),

    ("h1", "14  Fernein- und -ausschalten"),
    ("p", "Ein Shelly-Relais (Gen1 oder Gen2/Plus/Pro, wird automatisch erkannt) "
          "schaltet die 13,8 V des Funkgeräts. Der Netzschalter in der Titelleiste "
          "schaltet ein und aus — bevor die Versorgung getrennt wird, werden eine "
          "laufende Sendung und die Ablaufsteuerung beendet. Nach dem Ausschalten "
          "bleibt der Strom mindestens 3 s aus. Solange das Funkgerät aus ist, deckt "
          "ein Schild die Seite ab. Die automatische Abschaltung schaltet das "
          "Funkgerät aus, wenn die Seite für die eingestellte Zeit in keinem Browser "
          "mehr offen war."),

    ("h1", "15  Fehlersuche"),
    ("ul", [
        "CAT offline: by-id-Port prüfen und Menü 026 = 3 (38400 Bd).",
        "Kein Empfangston: CONNECT, dann RX GAIN prüfen und ob MUTE/DROP aus sind.",
        "Nichts dekodiert: NTP muss synchron sein (NTP-Lampe), Betriebsart DATA-U, "
        "DIGI-Pegel im grünen Bereich.",
        "FT8 zu leise: FT8-Sendeansteuerung (Settings ▸ Audio) und Menü 050.",
        "Kein Mikrofon: Die Seite muss über HTTPS mit vertrauenswürdiger CA geöffnet "
        "werden.",
        "Nach einem Neustart des Backends: Seite neu laden und erneut CONNECT.",
    ]),

    ("h1", "16  Technische Referenz"),
    ("diagram", "system", "de"),
    ("h2", "Backend"),
    ("ul", [
        "FastAPI auf uvicorn, systemd-Unit mit RuntimeDirectory, StateDirectory, "
        "Speicherbegrenzung und Restart=always.",
        "ft950.py: CAT-Treiber mit Prüfung durch Zurücklesen; radio_service.py fragt "
        "Frequenz/Betriebsart/Instrumente alle 0,1 s und die Empfangskette jede "
        "Sekunde ab.",
        "digi.py: an UTC ausgerichtete Zeitscheiben, jt9 parallel in Teilbändern, "
        "adaptive Tiefe, Früh- und Partnerdurchläufe; sequencer.py: der "
        "QSO-Zustandsautomat.",
        "voice.py: WebRTC-Hub, PTT-Freigabe, Vorlauf (Pre-Roll), AGC, Kompressor, "
        "Höhenanhebung.",
        "cw.py: der CW-Skimmer; hackrf_proxy.py; shelly.py; recorder.py.",
    ]),
    ("h2", "Dateien auf dem Pi"),
    ("code", "backend/runtime.json      Stationsdaten und Zugangsdaten (0600, nicht in git)\n"
             "backend/ex_defaults.json  gespeicherte Grundwerte der FT-950-Audiomenüs\n"
             "backend/certs/            Serverzertifikat (von der privaten CA)\n"
             "/run/ft950                Zeitscheiben für den Decoder (tmpfs)\n"
             "/var/lib/ft950            Arbeitsdaten von jt9"),
    ("p", "Quellcode, Issues und Updates: github.com/CQ-DJ0SH/ft950-remote — GPL v3."),
]
