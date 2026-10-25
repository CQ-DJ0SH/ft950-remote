# FT-950 CAT — vollständige Referenz & UI-Zuordnung

Quelle: *FT-950 CAT Operation Reference Book* (Vertex Standard, 2007) —
`FT-950_CAT_Manual_ENG.pdf` in diesem Ordner, Textauszug `.txt` daneben.
Alle Angaben hier sind aus dieser Primärquelle entnommen, nicht aus dem
Gedächtnis rekonstruiert.

## 1. Protokoll

| | |
|---|---|
| Format | ASCII, 2 Buchstaben + Parameter + `;` |
| Beispiel | `FA14250000;` setzt VFO-A auf 14.250000 MHz, `FA;` liest |
| Baudrate | Menü **026**: 0 = 4800, 1 = 9600, 2 = 19200, **3 = 38400** |
| Rahmen | 8N1, kein Hardware-Handshake nötig (Menü 028 = RTS-Port) |
| Timeout | Menü **027**: 10 / 100 / 1000 / 3000 ms — **10 ms wählen** |
| Fehlerfall | **Set-Befehle: keine Quittung**, ein unzulässiger Wert wird still verworfen (`SH020` in DATA-U). **Read-Befehle auf Ungültiges: `?;`** (gemessen: `MR011;` → `?;` bei leerem Kanal) |
| Frequenzbereich | `FA`/`FB`: 0030000 – 56000000 Hz |
| Modell-ID | `ID;` → `ID0310;` |

## 2. Die 97 Befehle und ihre UI-Heimat

Spalte „UI“: **V** = großes VFO-Panel, **D** = DIGI/FT8-Drawer,
**M** = Memory-Drawer, **C** = CW/Keyer-Drawer, **X** = EX-Menü-Drawer,
**S** = Settings, **–** = nur Backend (kein eigenes Bedienelement).

| Cmd | Funktion | Set / Read | Parameter (Kurzform) | UI |
|---|---|---|---|---|
| `AB` | VFO-A → VFO-B | S | — | V |
| `AC` | Antennentuner | S/R | P3: 0 off, 1 on, 2 Tuning-Start | V |
| `AG` | AF-Gain | S/R | 000–255 — **nur Lautsprecher** | S |
| `AI` | Auto-Information | S/R | 0 off, 1 on (beim Ausschalten auto 0) | – |
| `AM` | VFO-A → Memory-Kanal | S | — | M |
| `AN` | Antennenwahl | S/R | 1 = ANT1, 2 = ANT2 | V |
| `BC` | Auto-Notch (DNF) | S/R | 0 off, 1 on | V |
| `BD` | Band abwärts | S | — | V |
| `BI` | Break-in | S/R | 0 off, 1 on | C |
| `BP` | Manual Notch | S/R | P2 0: on/off · P2 1: 001–300 (×10 Hz) | V |
| `BS` | Bandwahl | S | 00 1.8 · 01 3.5 · (02 5) · 03 7 · 04 10 · 05 14 · 06 18 · 07 21 · 08 24.5 · 09 28 · 10 50 · 11 GEN | V |
| `BU` | Band aufwärts | S | — | V |
| `BY` | Busy | R | 0 off, 1 on | V |
| `CH` | Memory-Kanal ±1 | S | 0 up, 1 down | M |
| `CN` | CTCSS-Tonnummer | S/R | 00–49 (Tabelle, 67.0–254.1 Hz) | V |
| `CO` | Contour | S/R | P2 0: on/off · P2 1: 01–30 (Frequenz) | V |
| `CS` | CW-Spot | S/R | 0 off, 1 on | C |
| `CT` | CTCSS | S/R | 0 off, 1 ENC/DEC, 2 ENC | V |
| `DA` | Dimmer | S/R | 00–15 | S |
| `DN` | Mic-Down | S | — | V |
| `DP` | Display-Seite (DMU-2000) | S/R | 0 Clock … 6 Memory-Liste | S |
| `ED` | Encoder abwärts | S | P1 0 Main / 1 CLAR-VFO-B / 2 SELECT, P2 01–99 Rasten | V |
| `EK` | ENT-Taste | S | — | V |
| `EU` | Encoder aufwärts | S | wie `ED` | V |
| `EX` | Menü | S/R | 001–118 + Parameter (Tabelle §4) | X |
| `FA` | Frequenz VFO-A | S/R | 8 Ziffern Hz | V |
| `FB` | Frequenz VFO-B | S/R | 8 Ziffern Hz | V |
| `FK` | Funktionstaste | S | 1–7 = F1…F7 (Bandtasten) | V |
| `FR` | Function RX | S/R | 0 A-RX · 1 A-Mute · 4 B-RX · 5 B-Mute | V |
| `FS` | Fast Step | S/R | 0 off, 1 on | V |
| `FT` | Function TX | S/R | Set 0/1 Toggle, 2 = A-TX, 3 = B-TX; Read 0/1 | V |
| `GT` | AGC | S/R | **Set** 0 off,1 fast,2 mid,3 slow,4 auto — **Read** 0–3 wie Set, 4 auto-fast, 5 auto-mid, 6 auto-slow | V |
| `ID` | Identifikation | R | `0310` | – |
| `IF` | Information | R | Kanal, VFO-A-Freq, Clar ±0000–9999, RX/TX-Clar, Mode, VFO/MEM/MT/QMB/QMB-MT, CTCSS, Tone, Shift | V |
| `IS` | IF-Shift | S/R | `IS0±1000` Hz | V |
| `KM` | Keyer-Memory | S/R | Kanal 1–5 + bis 50 Zeichen Text | C |
| `KP` | Key Pitch | S/R | 00 = 300 Hz … 15 = 1050 Hz (50-Hz-Schritte) | C |
| `KR` | Keyer | S/R | 0 off, 1 on | C |
| `KS` | Key Speed | S/R | 004–060 WPM | C |
| `KY` | CW-Keying | S | 1–5 Keyer-Memory, 6–A Message-Keyer abspielen — **kein Freitext** | C |
| `LK` | Dial Lock | S/R | 0 off, 1 on | V |
| `LM` | Load Message (DVS-6) | S/R | 0 Stop, 1–5 Kanal | S |
| `MA` | Memory → VFO-A | S | — | M |
| `MC` | Memory-Kanal | S/R | 000–099 regulär, 100–117 = P1L/P1U…P9L/P9U | M |
| `MD` | Betriebsart | S/R | 1 LSB · 2 USB · 3 CW · 4 FM · 5 AM · 6 RTTY-L · 7 CW-R · 8 DATA-L · 9 RTTY-U · A PKT-FM · B FM-N · C DATA-U · D AM-N | V |
| `MG` | Mic-Gain | S/R | 000–255 | V |
| `MK` | Mode-Taste | S | 0/1 SSB · 2 CW · 3/4 AM-FM · 5/6 RTTY-PKT | – |
| `ML` | Monitor | S/R | P1 0: on/off · P1 1: Level 001–255 | V |
| `MR` | Memory lesen | R | 27-Zeichen-Kanalobjekt | M |
| `MS` | Meter-Umschalter | S/R | 0 COMP · 1 ALC · 3 SWR · 4 ID · 5 VDD | V |
| `MW` | Memory schreiben | S | 27-Zeichen-Kanalobjekt | M |
| `MX` | MOX | S/R | 0 off, 1 on (Read-Antwort heißt im Handbuch `NX`) | V |
| `NA` | Narrow | S/R | 0 off, 1 on — **verändert die `SH`-Tabelle** | V |
| `NB` | Noise Blanker | S/R | 0 off, 1 on, 2 wide | V |
| `NL` | NB-Level | S/R | 000–255 | V |
| `NR` | Noise Reduction | S/R | 0 off, 1 on | V |
| `OI` | Gegen-VFO-Info | R | wie `IF`, aber VFO-B | V |
| `OS` | Repeater-Shift | S/R | 0 simplex, 1 +, 2 − — **nur in FM** | V |
| `PA` | Vorverstärker | S/R | 0 IPO, 1 AMP1, 2 AMP2 | V |
| `PB` | Play Back (DVS-6) | S/R | 0 Stop, 1–5 Kanal | S |
| `PC` | Sendeleistung | S/R | 005–100 (W) | V |
| `PL` | Prozessor-Level | S/R | 000–100 | V |
| `PR` | Speech Processor | S/R | 0 off, 1 on, 2 Mic-EQ | V |
| `PS` | Power | S/R | 0 off, 1 on — **Dummy-Byte, dann 1–2 s später senden** | S |
| `QI` | QMB speichern | S | — | M |
| `QR` | QMB abrufen | S | — | M |
| `QS` | Quick Split | S | — (Offset über Menü 030) | V |
| `RA` | RF-Attenuator | S/R | 0 off, 1 6 dB, 2 12 dB, 3 18 dB | V |
| `RC` | Clar löschen | S | — | V |
| `RD` | Clar Minus-Offset | S | 0000–9999 Hz | V |
| `RF` | Roofing-Filter | S/R | **Set** 0 auto,1 15k,2 6k,3 3k — **Read** 1 15k,2 6k,3 3k,4 auto-15k,5 auto-6k,6 auto-3k | V |
| `RG` | RF-Gain | S/R | 000–255 | V |
| `RI` | Radio-Information | R | P1 0 Hi-SWR · 1 Mic-EQ · 3 REC · 4 PLAY → 0/1 | V |
| `RL` | NR-Level | S/R | 01–15 | V |
| `RM` | Meter lesen | R | P1 0 S/PO · 1 S · 2 Frontpanel-Wahl · 3 COMP · 4 ALC · 5 PO · 6 SWR · 7 ID · 8 VDD → 0–255 | V |
| `RO` | Rotor | S/R | 0 off,1 CCW,2 CW,3/4 Speed ∓1 %; Antwort Richtung 0–450°, Speed 0–100 % | S |
| `RS` | Radio-Status | R | 0 normal, 1 Menü-Modus, 2 DMU-Menü-Read | – |
| `RT` | RX-Clarifier | S/R | 0 off, 1 on | V |
| `RU` | Clar Plus-Offset | S | 0000–9999 Hz | V |
| `SC` | Scan | S/R | 0 off, 1 up, 2 down | M |
| `SD` | Break-in-Delay | S/R | 0000 = Full Break-in, 0030–3000 ms | C |
| `SF` | CLAR/VFO-B-Knopf-Funktion | S/R | 0 BAND, 1 MHz, 2 GRP, 3 MCH, 8 off (nur lesbar) | S |
| `SH` | Width | S/R | 00–20, Bedeutung **modus- und `NA`-abhängig** (§3) | V |
| `SM` | S-Meter | R | 000–255 | V |
| `SQ` | Squelch | S/R | 000–255 | V |
| `SV` | VFO tauschen | S | — | V |
| `TS` | TXW | S/R | 0 off, 1 on | V |
| `TX` | TX-Set | S/R | Set 0 = TX aus, 1 = CAT-TX ein; Read 2 = Radio sendet | V |
| `UL` | PLL-Unlock | R | 0 locked, 1 unlocked | V |
| `UP` | Mic-Up | S | — | V |
| `VD` | VOX-Delay | S/R | 0030–3000 ms | V |
| `VF` | VRF-Filter | S/R | on/off, ±Shift, Step 0–9; Read: Position 000–255 | V |
| `VG` | VOX-Gain | S/R | 000–100 | V |
| `VM` | VFO-A → Memory | S | — | M |
| `VS` | VFO-Wahl | S/R | 0 = VFO-A, 1 = VFO-B | V |
| `VX` | VOX | S/R | 0 off, 1 on | V |
| `XT` | TX-Clarifier | S/R | 0 off, 1 on | V |

## 3. Die `SH`-Falle (Width)

Derselbe Index bedeutet je nach Mode und `NA` eine andere Bandbreite:

| Index | SSB-N | SSB-W | CW-N | CW-W | RTTY/PKT-N | RTTY/PKT-W |
|---|---|---|---|---|---|---|
| 00 | 1800 | 2400 | 500 | 2400 | 300 | 500 |
| 03 | 600 | — | 100 | — | 100 | — |
| 04 | 850 | — | 200 | — | 200 | — |
| 05 | 1100 | — | 300 | — | 300 | — |
| 06 | 1350 | — | 400 | — | 400 | — |
| 07 | 1500 | — | 500 | 500 | 500 | 500 |
| 08 | 1650 | — | — | 800 | — | 800 |
| 09 | 1800 | 1800 | — | 1200 | — | 1200 |
| 10–13 | — | 1950/2100/2250/2400 | — | 1400/1700/2000/2400 | — | 1400/1700/2000/2400 |
| 14–20 | — | 2450/2500/2600/2700/2800/2900/**3000** | — | — | — | — |
| 01/02 | 200/400 | — | — | — | — | — |

Konsequenz für die UI: **immer Hertz anzeigen**, nie den Index; der Slider muss
seine Skala bei jedem Mode- und `NA`-Wechsel neu aufbauen.

**Am Gerät gemessen (2026-10-24):** DATA-U (`MD0C`) benutzt die
**RTTY/PKT-Spalte**, nicht SSB. `SH011` ergibt dort 1700 Hz, `SH013` 2400 Hz
(Maximum), und `SH020` wird **ohne Rückmeldung ignoriert**. Die Indizes 14–20,
also bis 3000 Hz, gibt es nur in LSB/USB. Der Treiber muss den erlaubten
Index-Bereich deshalb aus (Mode, `NA`) ableiten und nach jedem `SH` zurücklesen.

## 4. Menüpunkte (`EX`), die das Projekt wirklich braucht

| Nr. | Funktion | Wert |
|---|---|---|
| 026 | CAT Baud Rate | **3 = 38400** |
| 027 | CAT Time-out Timer | **0 = 10 ms** |
| 028 | CAT RTS Port | je SCU-17-Verdrahtung |
| 031 | TX Time out Timer | 00–30 min — **Sicherheitsnetz, z. B. 5** |
| 043/044 | CW Break-in Mode / Delay | semi/full, 0030–3000 ms |
| 045 | CW Pitch | 00–15 (300–1050 Hz) |
| 050 | **DATA MODE TX GAIN** | 000–100 — Sendepegel FT8 |
| 051 | **DATA MODE OUTPUT LEVEL** | 000–100 — RX-Pegel zum SCU-17 |
| 052/053 | DATA VOX Delay / Gain | für VOX-PTT statt CAT-PTT |
| 054 | PACKET FREQUENCY DISPLAY OFFSET | lesen; dieses Gerät: **+1500** |
| 055 | PACKET CARRIER POINT | lesen; dieses Gerät: **+1500** (zentriert das Fenster auf ~1450 Hz Audio) |
| 061 | RTTY DATA OUTPUT LEVEL | 000–100 |
| 064 | SSB TX BPF | 0 = 100–3000 Hz |
| 084/089 | Main-Dial-Step | 1/5/10 Hz bzw. 10/100 Hz (FM) |
| 087/088 | Mic-Up/Down-Step AM/FM | für `UP`/`DN` |
| 110–113 | Tuner-Wahl, TX-Power, AM-Carrier, Power-Control-Modus | |

Das EX-Drawer liest diese Tabelle aus einer JSON-Datei (118 Einträge, Typ +
Wertebereich pro Nummer) und baut daraus generisch Regler — kein Handcode
pro Menüpunkt.

## 5. Stolperfallen, kompakt

1. **Set-Befehle quittieren nicht.** Gültigkeit nur per Read-back. Ein *Read*
   auf etwas Ungültiges (leerer Speicher) bekommt dagegen sofort `?;` — der
   Treiber wertet das als `FT950Rejected`, statt bis zum Timeout zu warten.
2. **Set- und Read-Wertebereiche weichen ab** bei `GT` (0–4 vs. 0–6) und
   `RF` (0–3 vs. 1–6) → zwei getrennte Mappings, nie eine Tabelle für beides.
3. **`SH` ist kontextabhängig** (§3).
4. **`KY` kann keinen Freitext senden.** Workaround: Text per `KM` in Speicher
   1–5 schreiben (50 Zeichen), dann `KY1`…`KY5` abspielen; längere Texte über
   die fünf Speicher rotieren. (Menü 048 „CW PC KEYING“ beachten.)
5. **Ein Objektformat für vier Befehle** (`IF`/`OI`/`MR`/`MW`) → ein Parser.
6. **Clarifier ist richtungsbehaftet über die Befehlswahl** (`RU`/`RD`), nicht
   über ein Vorzeichen im Parameter.
7. **`OS` nur in FM**, sonst wirkungslos.
8. **`MD`-Codes `D` (AM-N)** erscheinen in der `MD`-Tabelle, aber nicht in der
   Mode-Liste von `IF`/`OI`/`MR` → unbekannte Codes tolerieren, nie crashen.
9. **`PS1`** braucht das Dummy-Byte-Ritual (1–2 s Fenster).
10. **`BS02`** ist nicht gedruckt, existiert aber als 5-MHz-Band (Menü 075).
11. **`AI1`** bricht synchrone Transaktionen → vorerst `AI0` + Polling.

## 6. Live-Antworten dieses Geräts (Parser-Testdaten)

```
IF;  → IF00414080000+000000C00000;
       └┬┘└──┬───┘└─┬─┘│││││└┬┘│
        │    │      │  ││││└ CTCSS 0 · Tone 00 · Shift 0 (simplex)
        │    │      │  │││└ P7 0 = VFO
        │    │      │  ││└ P6 C = DATA-U
        │    │      │  │└ TX-Clar 0
        │    │      │  └ RX-Clar 0
        │    │      └ Clar +0000 Hz
        │    └ 14 080 000 Hz
        └ Kanal 004
OI;  → OI00429540000-999000B01000;   VFO-B: 29.540 MHz, FM-N, CTCSS ENC/DEC, Clar −9990
SH0; → SH011;   GT0; → GT06; (AUTO-SLOW, nur lesbar)   RF0; → RF03;   PA0; → PA01;
AN0; → AN010;   VF0; → VF000000;   BY; → BY10;   SM0; → SM0124;   MS; → MS3;
```

Bestätigt: Nutzlast immer 24 Zeichen zwischen Präfix und `;`, Frequenz
**8-stellig** (auch bei `FB`). Lesen ohne Fehlerquittung funktioniert
zuverlässig mit einem einzigen 38400-Bd-Read, ~5 ms pro Antwort.

**Speicher (gemessen 2026-10-24):** `MRnnn;` liefert dieselbe 24-Zeichen-Nutzlast
wie `IF`, aber das Kanalfeld P1 zeigt den **gerade aktiven** Speicherplatz
(hier immer `004`), nicht den abgefragten — die Daten selbst gehören zum
abgefragten Kanal. Leere Kanäle antworten `?;`. 16 Kanäle lesen dauert 0,25 s.
Abruf in der UI: lesen und als `FA` + `MD` auf VFO-A setzen — das Gerät bleibt
im VFO-Betrieb, kein Speicher wird geschrieben.
