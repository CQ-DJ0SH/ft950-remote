# FT8/FT4 — Audiokette und Decodierung

Schwerpunkt des Projekts. Ziel ist nicht „FT8 geht auch“, sondern eine
Decodierleistung, die mit WSJT-X auf demselben Signal mithalten kann.
Deshalb: **der WSJT-X-Decoder selbst** (`jt9` aus dem Debian-Paket `wsjtx`,
Kandidat 2.7.0), nicht eine schlanke Eigenimplementierung.

## 1. Warum `jt9` und nicht `ft8_lib`

`jt9` fährt die komplette WSJT-X-Kette: Mehrfachdurchläufe mit Subtraktion
bereits decodierter Signale, A-priori-Decodierung und LDPC-Soft-Decision.
Praktische Schwelle FT8 ≈ **−21 dB** SNR (mit AP bis ~−24 dB), FT4 ≈ −17.5 dB.
Leichte C-Decoder (`ft8_lib`) liegen typisch 2–4 dB darüber und finden in
belegten Passbändern deutlich weniger. Für „sehr gute Decodierung“ ist das der
Unterschied zwischen 15 und 25 Decodes pro Zyklus.

`jt9` liest **12000 Hz, mono, 16 bit WAV** und schreibt Decode-Zeilen auf
stdout. Aufruf (Flags beim ersten Lauf mit `jt9 --help` gegenprüfen — die
Buchstaben haben sich zwischen WSJT-X-Versionen bewegt):

```
jt9 --ft8 -d 3 -L 200 -H 3000 -f 1500 /run/ft950/slice-20261024T153000.wav
#       │     │        │            └ nfqso für AP-Decodierung
#       │     │        └ volles Fenster, nicht nur 1500 ± 200 Hz
#       │     └ depth 3 = tiefste Stufe (Subtraktion + AP)
#       └ FT4: --ft4 und 7.5-s-Slices
```

## 2. Audiopfad — die eine Regel

> **Niemals aus dem Opus/WebRTC-Stream decodieren.** Opus ist psychoakustisch
> verlustbehaftet und löscht genau das, worauf FT8 lebt: kohärente Töne
> dicht unter dem Rauschen. Der Split muss **vor** dem Encoder passieren.

```
PCM2903B (hw:1,0)                  ┌──► Opus-Encoder ──► WebRTC ──► Browser
  48 000 Hz, 16 bit, stereo ──► Ring-Buffer (roh) ─┤
                                     └──► /4-Dezimation (FIR, fc≈5,2 kHz)
                                              └──► 12 000 Hz mono
                                                     ├──► WAV-Slice ──► jt9
                                                     └──► FFT ──► Wasserfall
```

48000/12000 ist **exakt 4** — eine Polyphase-FIR-Dezimation genügt, kein
Resampler mit Interpolationsfehlern. Ein Kanal des Stereo-Streams wird
verwendet (nicht gemischt; die zweite Spur ist beim PCM2903B oft nur Kopie
oder leer).

**ALSA:** Der PCM2903B hat **keinen einzigen Capture-Regler** (gemessen:
nur `PCM Playback Switch/Volume`), also auch keine AGC, die Slices
verbiegen könnte. Der RX-Pegel wird **ausschließlich** über Menü **051**
am Funkgerät eingestellt. Beide Kanäle sind identisch (Korrelation 1.000),
also wird nur L verwendet. Unter 100 Hz liegt Brumm/DC (−17 dB relativ zum
Plateau), deshalb vor der Dezimation ein Hochpass bei ~150 Hz.

## 3. Zeitbasis

* Slice-Grenzen sind UTC-Vielfache: **15 s** (FT8), **7.5 s** (FT4).
* `chrony` installieren, Ziel-Offset **< 100 ms**. Der aktuelle Offset
  (`chronyc tracking`) gehört als Zahl ins DIGI-Panel — ein Pi mit 2 s
  Drift „decodiert nichts“ und niemand sieht, warum.
* Slice-Länge exakt: 180 000 Samples (FT8) bzw. 90 000 (FT4).
* Decodierung läuft **nebenläufig** zum nächsten Capture; auf dem Pi 4
  braucht `jt9 -d 3` je Slice erfahrungsgemäß einige Sekunden — die
  gemessene Decodierzeit pro Zyklus gehört ebenfalls ins Panel.

## 4. Geräte-Profil „FT8“ (ein Knopf, alle CAT-Befehle)

| Was | CAT | Warum |
|---|---|---|
| Mode DATA-U | `MD0C` | Mikrofon stumm, NF aus der DATA-Buchse, Pegel über Menü 050/051 |
| Width 2400 Hz | `SH013` | größtes Fenster in DATA-U (PKT-Tabelle). `SH020` wird in DATA-U **stillschweigend ignoriert** (gemessen, §10). 3000 Hz gibt es nur in USB mit `SH020` |
| Narrow aus | `NA00` | sonst gilt die Narrow-Tabelle |
| IF-Shift 0 | `IS0+0000` | Fenster symmetrisch |
| Roofing 3 kHz | `RF03` | kein Übersteuern durch Nachbarsignale |
| DNF / Notch / NR / NB **aus** | `BC00` `BP0000` `NR00` `NB00` | alle vier sind nichtlinear und zerstören schwache Signale im belegten Passband |
| Contour aus | `CO0000` | ebenso |
| VRF aus | `VF00…` | µ-Tune schmalt die Vorselektion ein, Pegel driftet |
| AGC MID oder SLOW | `GT02` / `GT03` | FAST pumpt an starken Signalen und moduliert die Schwachen zu |
| IPO, ATT nach Band | `PA00`, `RA00/01` | ADC aus der Kompression halten |
| Squelch zu | `SQ0000` | Squelch darf den Decoder nie stumm schalten |
| Leistung | `PC025` | Pegel über Menü 050 so, dass ALC gerade anspricht |
| Menü 054/055 **lesen, nicht blind setzen** | `EX054;` `EX055;` | dieses Gerät steht auf +1500/+1500; damit liegt das 2400-Hz-Fenster gemessen bei ~300–2600 Hz. Der Treiber liest beide Werte und rechnet Dial ↔ RF selbst |
| TX-Timeout | `EX031` 5 min | Hardware-Netz unter dem Software-Watchdog |

Pegelziel im Panel: eine dBFS-Anzeige mit grünem Zielband (Vorbild: die
30–60-dB-Skala in WSJT-X). Zu leise kostet Empfindlichkeit, zu laut erzeugt
Intermodulation im Codec — beides sieht man erst an der Decode-Zahl.

## 5. Senden

`jt9` decodiert nur. Zum Senden:

1. `ft8code "CQ DJ0SH JO31"` (ebenfalls aus dem `wsjtx`-Paket) liefert die
   Tonfolge: **FT8** 79 Symbole, 8-GFSK, 6.25 Baud, 6.25 Hz Tonabstand,
   12.64 s, ~50 Hz Bandbreite. **FT4** 105 Symbole, 4-GFSK, 20.833 Baud,
   4.48 s, ~83 Hz.
2. Tonfolge mit Gauß-geformtem Phasenübergang (BT ≈ 2.0) bei 48 kHz
   synthetisieren und auf `hw:1,0` ausgeben — Hard-Keying erzeugt
   Seitenbänder und Splatter.
3. PTT per `TX1` / `TX0`, mit **Watchdog im Backend** (V71-Lehre), Count-up
   in der Taste und Freigabe nur bei verbundenem Audio.
4. Start exakt auf der Zyklusgrenze; Retune (`FA`) nur in den letzten
   ~500 ms **vor** einem Zyklus, nie während eines Capture-Fensters.

## 6. Auto-Sequencer & Logging

Zustandsmaschine wie WSJT-X: CQ → Antwort (Grid) → Report → R-Report → RR73
→ 73, mit „TX halten bis bestätigt“, Zeitlimit pro Schritt, und Abbruch bei
doppelter Antwort. Geloggt wird über das **vom V71 übernommene
`logbook.py`** (ADIF + Wavelog-REST + QRZ-Lookup) — der Wavelog-Container auf
diesem Pi ist entfernt, die URL ist also frei konfigurierbar.
Zusätzlich: Upload zu PSK Reporter aus den Decode-Zeilen.

## 7. Wasserfall — direkt aus dem V71-Projekt

`hackrf_sdr.py` + das Canvas-Rendering im Frontend (interpolierter Wasserfall,
Auto-Rauschboden, LEVEL-Regler, Peak-Hold, Hover-Readout, Doppelklick =
abstimmen) passen fast unverändert — nur die Datenquelle wird die
12-kHz-Audio-FFT statt des HackRF-Spektrums, die Achse 0–3 kHz statt MHz, und
die Zeitachse rastet auf die 15-s-Zyklen. Decodes werden als Marker auf ihrer
Audiofrequenz eingezeichnet.

## 8. Messbar machen

Ohne Zahlen ist „sehr gute Decodierung“ eine Behauptung. Das Panel zeigt
dauerhaft: Decodes pro Zyklus, SNR-Histogramm, Zeitoffset, Codec-dBFS,
`jt9`-Laufzeit. Zur Abnahme: dieselben Slices (sie liegen als WAV vor)
parallel durch WSJT-X auf einem anderen Rechner schicken und die Decode-Zahl
vergleichen — Gleichstand ist das Ziel, alles darunter ist ein Bug in der
Audiokette, nicht im Decoder.

## 9. Pakete

```bash
sudo apt-get install -y wsjtx chrony sox libsamplerate0 portaudio19-dev python3-venv
# wsjtx liefert jt9, jt4, jt65, wsprd, ft8code — die GUI wird nicht gestartet
```

## 10. Messprotokoll am echten Gerät (2026-10-24)

Ausgangslage: 14.080000 MHz (FT4, 20 m), `MD0C` (DATA-U), `NA00`, `SH011`,
`RF03` (3 kHz), `PA01` (AMP1), `GT06` (AUTO-SLOW), `EX051` = 030,
`EX054`/`EX055` = +1500/+1500. Rauschen und belegtes Band wurden 5–6 s lang
am Codec aufgenommen (48 kHz) und als Median-PSD über 8192-Punkt-FFTs
ausgewertet. Der Median unterdrückt die FT4-Signale, übrig bleibt die Filterform.

| Einstellung | wirksames Fenster (−20 dB) | Bemerkung |
|---|---|---|
| `SH011` (Ist-Zustand) | **~600 – 2300 Hz** | ≈ 1700 Hz, PKT-Tabelle; Ränder des FT4-Bands gehen verloren |
| `SH013` | **~300 – 2600 Hz** | ≈ 2400 Hz, Maximum in DATA-U |
| `SH020` | — | in DATA-U **stillschweigend verworfen**, Read-back blieb `SH011` |

Pegel bei `EX051` = 030: RMS ≈ −35 dBFS, Spitze ≈ −22 dBFS. Das ist genug
Headroom und keine Übersteuerung. Nach dem Test wurde wieder `SH011`
gesetzt und per Read-back bestätigt.

**Folgerung:** Mit `SH013` gewinnt man ~700 Hz Decodierfenster (≈ +40 %),
ohne an Menüs oder am Sendepfad zu rühren. Für die vollen 200–3000 Hz müsste
man in USB wechseln. Ob der DATA-Eingang des SCU-17 dann noch moduliert,
ist ungeprüft und wird vor jedem TX-Test geklärt.

**Übernommen (2026-10-24, per Read-back bestätigt):** `SH013` (2400 Hz),
`EX031` = 05 (TX-Timeout 5 min), `EX027` = 0 (CAT-Timeout 10 ms); der Link
läuft danach unverändert. Ob der FT-950 die Breite pro Band oder Mode
speichert, ist noch ungeprüft. Das FT8-Profil setzt `SH013` deshalb bei
jedem Wechsel nach DATA-U erneut und prüft es per Read-back.

## 11. Erste Live-Decodes der eigenen Kette (2026-10-24, 14:03–14:04 UTC)

14.080 MHz FT4, `SH013`, `jt9 -5 -d 3`, 5 Zyklen:

| | |
|---|---|
| Decodes | **31**, 5–8 pro Zyklus, SNR +13 … **−16 dB** |
| mittleres \|DT\| | 0,04 – 0,12 s, der UTC-Anker aus der minimalen Chunk-Ankunft genügt |
| `jt9`-Laufzeit | 0,6 – 1,4 s, der erste Lauf 2,5 s inkl. FFTW-Planung |
| Wasserfall | 10 Frames/s, 1024 Bins à 2,93 Hz |

**4 der 31 Decodes** (526 Hz ×2, 2493/2494 Hz) liegen außerhalb des
früheren `SH011`-Fensters (600–2300 Hz). Das bestätigt die Breitenänderung
direkt an der Decode-Zahl.

Offen: FT8-Test auf 14.074 (das Gerät stand während der Entwicklung auf
FT4), und ein Early-Decode-Lauf bei ~13,5 s für FT8. WSJT-X macht das, damit
die Antwort pünktlich im nächsten Zyklus starten kann. Ohne ihn liegen die
Decodes erst ~1,5 s nach Zyklusende vor.

## 12. FT8 live und Decodier-Tiefe auf dem Pi 4 (2026-10-24, 14.074 MHz)

Volles 20-m-Band, ~34 Signale pro Zyklus. `jt9 -8` auf denselben Slice:

| Tiefe | Laufzeit | Decodes |
|---|---|---|
| 1 | 3,8 s | 27 |
| 2 | 8,1 s | 33 |
| 3 | 13 – 15 s | 34 |
| 3, parallel zu d2 | 16,2 s | 34 (nur DD2MB −16 dB zusätzlich) |
| 3, 4 Teilbänder parallel | 9,6 s | 33, verliert genau dieses Signal |

Tiefe 3 passt auf dem Pi 4 bei vollem Band nicht in den 15-s-Zyklus.
Teilbänder helfen kaum: Jeder `jt9` sucht die Synchronisation trotzdem im
ganzen Spektrum, und die Subtraktion über die Teilbandgrenzen fehlt.
Deshalb ist die Tiefe **adaptiv**: Über 60 % der Periode → eine Stufe
weniger, unter 35 % → eine mehr. Startwerte sind FT8 = 2 und FT4 = 3.
Im Dienst ohne Browser auf dem Pi ergeben sich **33–34 Decodes in
6,4–7,8 s** bei Tiefe 2. Ein auf dem Pi laufender Browser (Test-Chromium)
verlängert das auf 12–14 s. Die Web-UI gehört deshalb auf ein anderes Gerät.

## 13. Erster Sendetest (TUNE, 2026-10-24)

Erster TUNE auf Sendung (18:16 UTC): **viel zu schwach**. Die Pegelkette:

| Stufe | Wert | Wirkung |
|---|---|---|
| TUNE-Ton (TX-Pegel) | 50 % | −6 dBFS (100 % ≈ 0 dBFS) |
| Codec-Wiedergabe (ALSA `PCM`) | **74 % = −20 dB** | alter ALSA-Zustand, nicht vom Projekt gesetzt |
| Gerätemenü 050 DATA TX GAIN | 030 | |

Am DATA-Eingang kamen also ~−32 dBFS an. Mit dem Codec auf **100 % (0 dB)**
funktioniert es (zweiter TUNE um 18:19 UTC, vom Operator bestätigt), mit sonst
unveränderten Werten: TX-Pegel 50 %, Menü 050 = 030, 15 W, DATA-U.

Der Codec-Pegel wird in `runtime.json` gespeichert und bei jedem Dienststart
gesetzt, weil ALSA beim Booten seinen eigenen Zustand zurückholt.
Feinabgleich für FT8: PO soll die eingestellte Leistung erreichen, ALC sich
höchstens knapp bewegen. Nachregeln erst mit dem TX-Pegel, dann mit Menü 050.

## 14. Decoder-Benchmark Pi 5 gegen Pi 4 (2026-10-25)

Nach dem Hardwaretausch läuft FT-950 Remote auf einem Pi 5. Getestet wurden
20 echte Slices vom 20-m-Band (14.074 MHz, 12:06–12:11 UTC, 24–35 Signale).
Auf beiden Pis lief derselbe `jt9`-Aufruf wie in der App (nice 5, 200–3000 Hz,
`-c DJ0SH -G JO62`). „par3“ heißt drei überlappende Teilbänder parallel, so
decodiert die App auf 4 Kernen. „single“ ist ein `jt9` über das ganze Band.
„Anteil“ bezieht sich auf alle Meldungen, die irgendein Lauf in dem Slice
gefunden hat.

| Tiefe | Variante | Pi 5 Median | Pi 5 max | Pi 4 Median | Pi 4 max | Decodes | Anteil |
|---|---|---|---|---|---|---|---|
| 1 | par3 | 0,6 s | 1,2 s | 1,8 s | 3,7 s | 24,6 | 81 % |
| 2 | par3 | **1,5 s** | 2,2 s | 4,1 s | 6,2 s | 28,3 | 92 % |
| 3 | par3 | 2,9 s | 4,6 s | 8,4 s | 10,4 s | 29,9 | 97 % |
| 1 | single | 0,9 s | 2,4 s | 2,4 s | 4,6 s | 25,1 | 82 % |
| 2 | single | 2,8 s | 3,4 s | 7,2 s | 8,9 s | 28,9 | 94 % |
| 3 | single | 6,2 s | 8,0 s | 14,1 s | 20,7 s | 30,2 | 99 % |

* Beide Pis finden exakt dieselben Meldungen. Der Pi 5 ist durchgehend 2,5–3× schneller.
* D2 → D3 bringt +1,8 Decodes pro Periode (die schwächsten Signale), D1 → D2 +3,8.
* Die Teilbänder kosten etwa 1–2 % der Decodes gegenüber einem Lauf übers ganze Band
  (Subtraktion über die Grenzen fehlt) und sparen dafür etwa die Hälfte der Zeit.
* Pi 4, D3 single: In 8 von 20 Slices über 15 s, damit fällt die Periode aus.
* Live auf dem Pi 5 (40 Zyklen, AUTO): D3 im Median 2,9 s, 30 Decodes.

Seitdem ist die Tiefe im FT8-Panel umschaltbar (D1/D2/D3/AUTO) und steht
standardmäßig auf **D2**: Die Decodes sind nach ~1,5 s da, 92 % der Signale
werden gefunden, und es bleibt Luft für Früh- und Partnerdurchläufe während
eines QSOs. D3 lohnt sich auf dem Pi 5 für die Jagd nach schwachen Signalen.

Messbedingungen: Auf dem Pi 5 lief der Live-Decoder weiter. Der Benchmark
wartete vor jedem Lauf, bis kein Live-`jt9` aktiv war. Bei „D3 single“
überschnitten sich trotzdem 16 von 20 Läufen, die Pi-5-Zeiten dort sind
also eher zu hoch. Auf dem Pi 4 liefen TM-V71 Remote, pyMC und ein
untätiges headless Chromium. Temperatur Pi 5 danach 49 °C, keine Drosselung.
