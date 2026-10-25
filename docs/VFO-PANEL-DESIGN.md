# FT-950 Remote — Design des (einen) VFO-Panels

> Entwurf, Stand 2026-10-24. Grundlage: offizielles *FT-950 CAT Operation
> Reference Book* (liegt als `docs/FT-950_CAT_Manual_ENG.pdf`, Textauszug als
> `.txt` daneben) und die übernommenen Erkenntnisse aus **TM-V71 Remote**
> (`/opt/tmv71-remote`). Vollständige Befehlsliste: `docs/CAT-REFERENCE.md`.
> Decoder-Kette: `docs/FT8-DECODING.md`.

## 0. Festlegungen

| | |
|---|---|
| Gerät | Yaesu FT-950, HF + 6 m, **ein** Empfänger, zwei VFOs (A/B) |
| Anschluss | SCU-17: CAT über USB-Serial, NF über USB-Codec — **beides schon erkannt** |
| CAT | **38400 Bd**, 8N1, Terminator `;`, **direkt, ohne hamlib** |
| Schwerpunkt | **FT8/FT4 mit sehr guter Decodierung** (WSJT-X-Decoder `jt9`) |
| GUI | **ein großes VFO-Panel**, nicht zwei — Begründung §1 |

Hardware-Ist-Stand auf diesem Pi (Pi 4B, 4 GB, 4 Kerne):

```
CP2105 Dual UART  →  /dev/ttyUSB0  (if00, „Enhanced“ = CAT)      ← FA;/IF; hier
                  →  /dev/ttyUSB1  (if01, „Standard“ = PTT/CW/FSK via RTS/DTR)
PCM2903B Codec    →  card 1 „USB Audio CODEC“ (hw:1,0), RX + TX
```
Stabile Namen über udev nach Seriennummer (`01CD8BBB`), wie im V71-Projekt:
`/dev/ft950-cat`, `/dev/ft950-keys`.

## 1. Warum **ein** Panel richtig ist (und beim V71 zwei nötig waren)

Der TM-V71 hat **zwei vollständige, gleichzeitig arbeitende Bänder** — jedes mit
eigener Frequenz, Mode, Tone, Sendeleistung, Squelch und eigenem S-Meter. Zwei
symmetrische Panels waren dort keine Design-Vorliebe, sondern das Gerät.

Der FT-950 hat **eine Empfangskette**. VFO-A und VFO-B sind zwei
Frequenz-Register, kein zweiter Empfänger:

* `FR` schaltet, **welcher** VFO empfängt (0 = A, 4 = B) — nie beide.
* `FT` schaltet, welcher VFO sendet (Split), `QS` = Quick Split,
  `TS` = TXW (kurz auf der TX-Frequenz mithören), `SV` tauscht A/B.
* Alles, was am Signal gedreht wird — AGC, IPO/ATT, VRF, Roofing, Width,
  Shift, Notch, Contour, NR, NB, RF-Gain, Squelch — gehört zur **Kette**,
  nicht zum VFO. `GT`, `PA`, `RA`, `SH`, `IS` … haben kein VFO-Argument.
* `MD` setzt den Mode des **aktiven** VFO; der Mode von VFO-B ist nur über
  `OI` *lesbar*, nicht direkt setzbar.

Zwei gleichberechtigte Panels würden also eine Symmetrie behaupten, die das
Gerät nicht hat (und bei jedem Filter-Regler die Frage aufwerfen: „wirkt der
jetzt auf A oder auf B?“). Richtig ist: **ein großes Panel mit einer
Haupt-Frequenz und einer schmalen Zweit-VFO-Zeile.**

## 2. Panel-Anatomie (Querformat, ~1024 px, PWA-fähig)

```
┌─ VFO ────────────────────────────────────────────── LOCK ▣  FAST ▣  ⚙ ─┐
│ A │ 1.8 │ 3.5 │ 5 │ 7 │ 10 │ 14 │ 18 │ 21 │ 24.5 │ 28 │ 50 │ GEN │ ▲▼ │ A
│   │                                                                     │
│   │   ╔══════════════════════════════════════════╗  ┌────────────────┐  │
│   │   ║  RX▸A        14.074.000  MHz             ║  │S 1 3 5 7 9 +20 │  │ B
│   │   ║  ┌──┬──┬──┬──┬──┬──┬──┬──┐  Ziffern-Drag ║  │▓▓▓▓▓▓▓▓░░░ S7  │  │
│   │   ║  │ 1│ 4│ 0│ 7│ 4│ 0│ 0│ 0│  ▲/▼ pro Digit║  │PO▓▓▓ ALC▓ SWR▏ │  │ E
│   │   ║  └──┴──┴──┴──┴──┴──┴──┴──┘               ║  │[PO ALC SWR ID] │  │
│   │   ║  B  14.074.000   SPLIT ▣  TXW ▣  A⇄B  ⇨B ║  │ BUSY ▪  UNLOCK▪│  │
│   │   ║  CLAR  RX▣ TX▣  −0000 Hz  ◂▸  CLR        ║  └────────────────┘  │
│   │   ╚══════════════════════════════════════════╝                      │
│   │                                                                     │
│   │ MODE  LSB│USB│CW│CW-R│AM│FM│RTTY-L│RTTY-U│DATA-U│DATA-L│PKT-FM  NAR▣│ C
│   │                                                                     │
│   │ ┌─ RX-KETTE ───────────────────────────────────────────────────────┐ │
│   │ │ IPO│AMP1│AMP2   ATT 0│6│12│18   AGC OFF│FAST│MID│SLOW│AUTO      │ │ D
│   │ │ ROOF AUTO│15k│6k│3k   VRF ▣ ◂▸   RF-GAIN ▬▬▬▬▬  SQL ▬▬▬         │ │
│   │ │ WIDTH 3000 Hz ▬▬▬▬▬▬  SHIFT −1000…+1000 ▬▬▬  [ Passband-Grafik ]│ │
│   │ │ NOTCH ▣ 1200 Hz ▬▬  DNF ▣  NR ▣ 08 ▬▬  NB ▣ WIDE▣ 127 ▬▬        │ │
│   │ │ CONTOUR ▣ 10 ▬▬                                                  │ │
│   │ └──────────────────────────────────────────────────────────────────┘ │
│   │ ┌─ TX ─────────────────────────────────────────────────────────────┐ │
│   │ │ PWR 030 W ▬▬▬  MIC ▬▬  PROC ▣ ▬▬  VOX ▣ G▬ D▬  MONI ▣ ▬▬        │ │ F
│   │ │ TUNER OFF│ON│START   ANT 1│2   [ MOX ]  [ ⏺ PTT / CAT-TX ]      │ │
│   │ └──────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
   ▾ DIGI · FT8/FT4        ▾ MEMORY        ▾ CW/KEYER        ▾ MENU (EX)
```

Die vier Reiter unten sind einklappbare Panels im V71-Stil
(`.panel-head` + `.panel-toggle`, Zustand in `localStorage`) — das VFO-Panel
bleibt dadurch auf jedem Display vollständig sichtbar, und das DIGI-Panel kann
den ganzen Rest verdecken, wenn FT8 läuft.

### Zone A — Band-Stack
`BS00`…`BS11` pro Taste, `BU`/`BD` auf den ▲▼-Pfeilen. Tastenhalten = zweites
Stack-Register (die F-Tasten `FK1`…`FK7` entsprechen den Frontpanel-Tasten).
`02` ist in der gedruckten Tabelle übersprungen, belegt aber das 5-MHz-Band
(Menü 075 definiert dessen Scope-Startfrequenz) — als `5` mitanbieten und
Fehlschlag tolerieren.

### Zone B — Frequenzanzeige + Tuning
* **Hauptzeile = aktiver RX-VFO.** Quelle ist `IF;` (liefert Kanal, Frequenz,
  Clarifier, Mode, VFO/MEM-Status, CTCSS, Shift in *einer* Antwort —
  entspricht dem einen `FO`-Objekt des V71).
* **Zweitzeile = der andere VFO**, Quelle `OI;` (gleiches 27-Zeichen-Layout).
  Klick auf die Zeile = `SV` (tauschen); `A⇄B` = `SV`, `⇨B` = `AB`.
* **Ziffern-Drag** wie im V71 (`.freq-spin`): jede Ziffer ist ein Spinner.
  Aber **nicht** jede Bewegung als absolutes `FA` schreiben, sondern
  `EU`/`ED` benutzen: `EU0nn`/`ED0nn` dreht den Hauptknopf um `nn` Rasten
  (01–99) und das Gerät rechnet mit seiner eigenen Schrittweite (Menü 084/089).
  Das fühlt sich wie der echte Knopf an und vermeidet Rundungsstreit.
  Absolutes `FA`/`FB` nur bei Direkteingabe und beim Bandwechsel.
  `FS1` = FAST, `UP`/`DN` = Mikrofon-Raster (Menü 087/088).
  `EU1nn`/`ED1nn` dient dem CLAR/VFO-B-Knopf, `EU2nn` dem SELECT-Knopf.
* **CLAR:** `RT0/1` (RX) und `XT0/1` (TX) als zwei Lampen, Offset über
  `RU0000-9999` / `RD0000-9999` (Betrag + Richtung durch die Befehlswahl,
  **kein** vorzeichenbehafteter Absolutwert), `RC` = CLEAR.
* **MEM/QMB-Badge:** `IF`-Feld P7 unterscheidet VFO / Memory / Memory-Tune /
  QMB / QMB-MT — genau wie das `mem-badge` beim V71.
* `LK1`/`LK0` = Dial Lock, als Schloss im Panel-Kopf.

### Zone C — Mode
`MD0x` mit **11 im Gerät sinnvollen Werten**: 1 LSB, 2 USB, 3 CW, 7 CW-R,
4 FM, B FM-N, 5 AM, 6 RTTY-L (FSK), 9 RTTY-U, 8 DATA-L (PKT-L), C DATA-U
(PKT-U), A PKT-FM. `NA0/1` = Narrow-Umschaltung, die **die Bedeutung der
`SH`-Indizes verändert** (§5 der CAT-Referenz) — deshalb sitzt NAR direkt
neben MODE und nicht beim Width-Regler.
Mode-Wechsel auf `DATA-U` schaltet das Panel sichtbar in den **DIGI-Zustand**:
die Passband-Grafik wird zum Audio-Wasserfall 0–3 kHz, `[FT8-PROFIL]` erscheint.

### Zone D — RX-Kette (das Herz, einmal, nicht zweimal)
`PA` (IPO/AMP1/AMP2) · `RA` (0/6/12/18 dB) · `GT` (AGC) · `RF` (Roofing) ·
`VF` (VRF) · `SH` (Width) · `IS` (IF-Shift) · `BP` (Manual Notch) ·
`BC` (Auto Notch/DNF) · `NR`+`RL` · `NB`+`NL` · `CO` (Contour) ·
`RG` (RF-Gain) · `SQ` (Squelch) · `AG` (AF-Gain, siehe Warnung unten).

**Passband-Grafik:** ein kleiner Canvas, der `SH`+`IS`+`CO`+`BP`+`RF`
gemeinsam zeichnet (Durchlasskurve mit Kerbe und Contour-Delle). Das ist
die ehrlichste Darstellung dieser fünf voneinander abhängigen Werte — Zahlen
allein beantworten „wo liegt mein Filter jetzt?“ nicht.

> ⚠️ `AG` (AF-Gain) regelt **nur den Lautsprecher**, nicht den Pegel, der über
> das SCU-17 zum Pi geht. Für die Decodierung zählen Menü **051**
> (DATA MODE OUTPUT LEVEL) und **061** (RTTY DATA OUTPUT LEVEL). Das ist das
> FT-950-Gegenstück zur V71-Erkenntnis „RX-Audio vom 1200-Baud-Pin, nicht vom
> 9600er“ — und es gehört als Slider ins DIGI-Panel, nicht ins VFO-Panel.

### Zone E — Meter-Cluster
`SM0` (S-Meter 0–255) als S-Bar mit echter S-Skala — beim V71 musste das aus
FM-Quieting *geschätzt* werden, hier liefert das Gerät einen echten Wert.
Darunter eine zweite Bar, die im TX auf `RM` umschaltet: `RM1` S, `RM3` COMP,
`RM4` ALC, `RM5` PO, `RM6` SWR, `RM7` ID, `RM8` VDD; `MS` spiegelt die
Frontpanel-Wahl. Lampen: `BY` (Busy), `UL` (PLL unlock — gehört sichtbar ins
UI, das ist ein echter Fehlerzustand), `RI0` (Hi-SWR-Warnung).

### Zone F — TX
`PC005-100` (Leistung, 5–100 W) · `MG` · `PR`+`PL` (Prozessor, 2 = Mic-EQ) ·
`VX`+`VG`+`VD` · `ML` (Monitor) · `AC` (Tuner OFF/ON/START) · `AN` (ANT 1/2) ·
`MX` (MOX) · `TX1`/`TX0` (CAT-PTT).
PTT-Regeln **wörtlich vom V71 übernommen**: PTT nur bei verbundenem Audio,
Count-up-Timer in der Taste, Watchdog im Backend, Release bei Audio-Abbruch,
PTT-LOCK als bewusster Latch. Zusätzlich als Hardware-Netz: Menü **031**
TX TIME OUT TIMER.

## 3. Was bewusst **nicht** ins VFO-Panel kommt

| Drawer | Inhalt | CAT |
|---|---|---|
| **DIGI · FT8/FT4** | Wasserfall 0–3 kHz, Decode-Tabelle, Auto-Sequencer, Band-Hopping, Datenpegel | `FA`,`MD`,`TX`,`PC`, EX 050/051/054/055 |
| **MEMORY** | 000–099 + P1L…P9U (100–117), CSV-Im/Export, Scan | `MC`,`MR`,`MW`,`MA`,`AM`,`VM`,`QI`,`QR`,`CH`,`SC` |
| **CW/KEYER** | Keyer, Speed, Pitch, Spot, 5 Textspeicher, Break-in | `KR`,`KS`,`KP`,`CS`,`KM`,`KY`,`BI`,`SD` |
| **MENU (EX)** | alle 118 Menüpunkte als generische Tabelle aus einer JSON-Definition | `EX`,`RS` |
| **Settings** | Serial, Audio-Device, Zertifikat, Update, Power (`PS`) | `PS`,`ID`,`AI`,`DA`,`DP`,`SF`,`RO` |

Damit ist **jeder** der 97 CAT-Befehle genau einer UI-Heimat zugeordnet; die
Matrix steht vollständig in `docs/CAT-REFERENCE.md`.

## 4. Vom V71 übernommene Architektur (unverändert gut)

* **Direkter serieller Treiber** (`backend/app/ft950.py`), ein `threading.RLock`
  serialisiert, FastAPI ruft über `asyncio.to_thread` — das Gerät verarbeitet
  keine parallelen Kommandos.
* **Ein Objekt-Parser** für die vier 27-Zeichen-Antworten `IF`/`OI`/`MR`/`MW`
  (wie `ChannelData`/`_parse_freq_object` beim V71, dort für `FO`/`ME`).
* **Service-Layer mit Poll-Loop + WS-Broadcast** (`radio_service.py`-Muster),
  REST für Aktionen, `WS /ws` für Status, Pydantic-Modelle.
* **Vanilla-JS-SPA ohne Build-Step**, DSEG-7-Segment-Font, einklappbare Panels,
  Light/Dark, PWA mit Swipe-Deck, `Cache-Control: no-cache` auf allen Statics.
* **systemd + HTTPS** mit eigener CA und In-UI-Zertifikatsinstallation,
  `Restart=always`, `MemoryHigh/MemoryMax` (die 1-GB-Pi-Lehre).
* **Fehler als HTTP 400 mit Klartext** über einen zentralen Exception-Handler.
* **Arbeitsregeln:** kein Auto-Push, README/Commits auf Englisch, kein
  Hersteller-Logo im Repo, Service-Restart nur bei Backend-Änderungen.

### Was anders sein **muss**

1. **Kein `?`-Quittungszeichen.** Der TM-V71 antwortet auf jeden ungültigen
   Befehl mit `?`, das war die Fehlererkennung. Der FT-950 **schweigt** bei
   Set-Befehlen. Konsequenz: Schreiben ist „fire & verify“ — nach dem Set den
   betroffenen Read ausführen (meist `IF;`) und das Ergebnis vergleichen. Ein
   Timeout bedeutet hier *Verbindung tot*, nicht *Wert abgelehnt*.
2. **`AI1` (Auto-Information)** schickt Statusänderungen unaufgefordert. Das
   mischt sich in die Antworten eigener Reads und bricht das streng
   synchrone `transact()`-Modell des V71. Empfehlung: **`AI0` + Poll-Loop**
   (bei 38400 Bd kostet `IF;` ~8 ms; 5 Hz für `IF`, 10 Hz für `SM`/`RM` im
   TX ist mühelos drin). `AI1` erst, wenn ein Reader-Thread mit Dispatch nach
   Befehlspräfix steht.
3. **Menü 027 CAT TIME-OUT TIMER auf 10 ms** stellen: ein halb angekommener
   Befehl verfällt dann sofort und frisst nicht den nächsten.
4. **Menü 028 CAT RTS PORT** und das SCU-17: auf dem zweiten Port ist RTS die
   PTT-Leitung. pyserial deshalb explizit mit `rtscts=False` öffnen und
   RTS/DTR bewusst setzen — sonst tastet das Öffnen des Ports den Sender.
5. **Kein GPIO-Relais nötig:** `PS1`/`PS0` schaltet das Gerät. Eigenheit:
   vor `PS1` ein Dummy-Byte senden, dann zwischen 1 s und 2 s später den
   Befehl.

## 5. Umsetzungsreihenfolge

1. `ft950.py` — Treiber: `transact()`, `IF`/`OI`-Parser, Read-back-Verify,
   `ID;` muss **`ID0310;`** liefern (Modell-Sanity-Check).
2. Service-Layer + `GET /api/status` + `WS /ws`, Poll-Loop.
3. Das VFO-Panel aus §2, Zone für Zone, gegen echtes Gerät.
4. Audio-Splitter (roh für Decoder, Opus für Browser) — `docs/FT8-DECODING.md`.
5. DIGI-Panel mit `jt9`-Decoder, dann Auto-Sequencer und Logging.
6. Memory/CW/EX-Drawer.
