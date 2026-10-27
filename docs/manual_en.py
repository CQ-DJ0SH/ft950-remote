"""English manual content for gen_docs.py — a list of (kind, ...) blocks.

Kinds: h1 (chapter), h2 (section), h3 (sub-heading), p, ul [items], code,
img (path relative to docs/, width mm), diagram (name, lang), space.
"""

EN = [
    ("h1", "1  Overview"),
    ("p", "FT-950 Remote is a web remote control for the Yaesu FT-950 HF/6 m "
          "transceiver. It runs on a Raspberry Pi next to the radio, talks to it "
          "directly through a Yaesu SCU-17 interface — CAT at 38400 Bd and USB "
          "audio — and serves the whole station as one HTTPS page. No hamlib, no "
          "desktop software: every control is mapped to the radio's documented CAT "
          "command and checked by reading the value back."),
    ("p", "The focus is digital operation (FT8/FT4 with WSJT-X's own decoder) and "
          "remote voice over WebRTC, with a CW skimmer, a HackRF panadapter from a "
          "second Pi, logging to Wavelog and QRZ.com, and remote power through a "
          "Shelly relay."),
    ("img", "screenshots/vfo.png", 174),
    ("h1", "2  Features"),
    ("ul", [
        "VFO panel with an LCD modelled on the FT-950's display: FT-950 style "
        "analog meter, signal path as lit cells, mode lamps, DSP bars, "
        "seven-segment frequency, clarifier, VFO-B, PO/SWR/ALC/ID/AF bars on transmit.",
        "Title-bar switches: split, ANT 1/2, tuner and ATU tuning, CLAR/CLR, "
        "FLT · IPO · R.FLT · AGC, voice profile and FT8 profile.",
        "Tuning by mouse wheel or drag on a digit, frequency entry, band and mode "
        "keys, a frequency shuttle, CTCSS (ENC/TSQ), MON, NAR and a scaled TX power control.",
        "Push-to-talk in the browser with a watchdog, PTT-LOCK, roger beep, "
        "1750 Hz tone call, MUTE and DROP; 32 memory keys (click recalls, hold 3 s stores).",
        "Two-way audio over WebRTC/Opus: RX/TX gain, mic AGC, compressor, "
        "pre-emphasis, mic echo test, RX recorder with WAV download.",
        "FT8/FT4 decoding with jt9 on three CPU cores, WSJT-X style sequencer with "
        "auto-answer, waterfall, DXCC and worked-before flags.",
        "HF band conditions under the waterfall from real VOACAP for your locator: "
        "80-40, 30-20, 17-15, 12-10 and 6m in green / yellow / red, with a sun/moon "
        "switch for day and night.",
        "CW skimmer: every CW signal in the passband decoded at once; send through "
        "the radio's keyer with macros.",
        "HackRF waterfall relayed from TM-V71 Remote, following the amateur band of VFO A.",
        "Logging to Wavelog and QRZ.com; worked-before index from Wavelog.",
        "Radio power through a Shelly relay with auto power-off.",
        "Dark and light theme, 90 % panel zoom, installable as a PWA.",
    ]),

    ("h1", "3  Hardware and radio setup"),
    ("h2", "What you need"),
    ("ul", [
        "Raspberry Pi with Debian 13 / Raspberry Pi OS. A Raspberry Pi 5 is "
        "strongly recommended: FT8/FT4 decoding with jt9 is the heaviest job, and "
        "the Pi 5 decodes a full band in a fraction of the Pi 4's time. A Pi 4 "
        "works, but needs 4–6 s per period at depth D2 instead of 1.5–2 s.",
        "Yaesu SCU-17 between Pi and radio (USB to the Pi; CAT and RTTY/DATA "
        "cables to the radio).",
        "Optional: a second Pi with TM-V71 Remote and a HackRF One (panadapter), "
        "a Shelly relay in the radio's 13.8 V line (remote power).",
    ]),
    ("h2", "The SCU-17 on the Pi"),
    ("p", "The SCU-17 contains a CP2105 dual UART and a TI PCM2903B USB codec. "
          "The CP2105's \"Enhanced\" port (interface 0) carries CAT, the "
          "\"Standard\" port carries PTT/CW on RTS/DTR. The codec is the audio "
          "path to and from the radio's DATA jack. Use the stable "
          "/dev/serial/by-id path in Settings — it survives re-plugging."),
    ("code", "/dev/serial/by-id/usb-Silicon_Labs_CP2105_..._if00-port0   CAT, 38400 Bd 8N1\n"
             "/dev/serial/by-id/usb-Silicon_Labs_CP2105_..._if01-port0   PTT/CW (RTS/DTR)\n"
             "hw:CARD=CODEC,DEV=0                                        audio in/out"),
    ("h2", "Radio menus"),
    ("ul", [
        "026 CAT RATE = 38400 Bd (3), 027 CAT TOT = 10 ms (0).",
        "031 TX TOT as a safety net for remote operation.",
        "050 DATA TX GAIN — transmit level from the DATA jack (FT8, TUNE, voice). "
        "Adjust with TUNE until the power is reached and ALC just moves.",
        "051 DATA OUTPUT LEVEL — receive level to the Pi (the codec has no capture control).",
        "054/055 (DATA shift / VOX) as described in docs/FT8-DECODING.md.",
    ]),
    ("p", "Audio from the Pi reaches the radio on the DATA jack, which the FT-950 "
          "takes in the DATA (PKT) modes. Remote voice therefore runs in DATA-U / "
          "DATA-L — the VOICE PROFILE sets that up for the current band."),

    ("h1", "4  Installation"),
    ("code", "sudo apt install wsjtx voacapl    # jt9, ft8code, ft4code; VOACAP\n"
             "git clone https://github.com/CQ-DJ0SH/ft950-remote /opt/ft950-remote\n"
             "cd /opt/ft950-remote/backend\n"
             "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
             "mkdir -p certs && cd certs && bash ../../deploy/gen-server-cert.sh /path/to/ca\n"
             "sudo cp ../../deploy/systemd/ft950-remote.service /etc/systemd/system/\n"
             "sudo systemctl enable --now ft950-remote"),
    ("p", "The service serves https://<pi>:8444/. The certificate is signed by a "
          "private root CA (shared with TM-V71 Remote) with a random serial and the "
          "Pi's own address; the CA can be downloaded in Settings ▸ General and on "
          "the power-off plate. Browsers allow the microphone only over HTTPS, so "
          "install the CA once on every device."),
    ("p", "Then open Settings ▸ General: callsign, locator, CAT port and baud "
          "rate. Station data, logbook credentials and memory names are stored in "
          "backend/runtime.json (mode 0600) and never sent back to the browser."),

    ("h1", "5  The page"),
    ("img", "manual/titlebar.png", 174),
    ("p", "The title bar shows the logo (uploaded under Settings, never part of "
          "the repository), your callsign (shown with a slashed zero), the "
          "nameplate, the CAT and USB AUDIO lamps, the UTC clock, the theme switch, "
          "Settings and — once a Shelly is set up — the power switch."),
    ("p", "Below follow the panels VFO, PTT · MEMORY and AUDIO, DIGI, CW, HackRF "
          "and LOG. Most can be collapsed with ▲; PTT and AUDIO always stay open. "
          "The panels are shown at 90 % zoom so the whole station fits on one screen."),

    ("h1", "6  VFO panel"),
    ("img", "manual/vfo.png", 174),
    ("h2", "Title bar"),
    ("ul", [
        "LOCK and PLL lamps: dial lock, PLL unlock (a radio fault). A click on "
        "LOCK (or Enter/Space) locks or unlocks the dial (LK, verified by read-back).",
        "SPLIT: receive on VFO A, transmit on VFO B.",
        "ANT 1 / ANT 2, TUNER (internal ATU in/out), ATU TUNE (starts a tuning "
        "cycle — the radio transmits a carrier; confirmation required).",
        "FLT · IPO · R.FLT · AGC step through VRF/THRU, IPO/AMP1/AMP2, "
        "AUTO/15k/6k/3k and OFF/FAST/MID/SLOW/AUTO; the LCD shows the state.",
        "VOICE PROFILE and FT8 PROFILE: read every value, write only what "
        "differs, verify by read-back, report what changed.",
    ]),
    ("h2", "LCD"),
    ("img", "manual/lcd.png", 174),
    ("p", "Left window: the analog meter — S on receive, PO on transmit — with "
          "the FT-950's scales (S, PO 0–150 W, COMP 0–20 dB, ID, SWR 1–∞, ALC/VDD "
          "13.8 V), white on black with steel-blue bands, and the MONI/NB/"
          "TUNER/MIC EQ indicators."),
    ("p", "Right window: the signal path ANT · ATT · FLT · IPO · R.FLT · AGC "
          "as fixed cells (only the active values light up), BUSY, the mode lamps "
          "LSB USB CW AM FM RTTY PKT REV (DATA-U lights PKT + USB, CW-R lights "
          "CW + REV), TX lamp and set power, the PO/SWR/ALC/ID/VDD bars, the DSP "
          "bars, the frequency to 1 Hz, the CLARIFIER lamp, the clarifier scale (red "
          "pointer = offset, ±1 kHz, tall ticks at the ends, a small one every 200 Hz) "
          "with its value (the sign has a fixed place), VFO-B under VFO A and a "
          "digital S meter."),
    ("p", "The DSP bars CONTOUR · NOTCH · WIDTH · SHIFT are drawn as on the "
          "FT-950: a blue bracket with a red pointer in the middle, the lit "
          "segments as green dashes. CONTOUR shows two rows with a V-shaped gap at "
          "the contour position, NOTCH a dash each side of the notch frequency in "
          "two rows; their labels stay dark while the function is off."),
    ("p", "The digital S meter under VFO-B is a 40-segment bar, blue to S9 and red "
          "above, with the scale 1 … 9, +20, +40, +60 at the needle's S points; it "
          "stays dark while transmitting. As on the FT-950's LCD, every 7-segment "
          "digit place shows a dimmed 8 and unlit bar segments are dimmed, and "
          "nothing changes colour — the PWR box keeps its colours on transmit, "
          "the TX lamp shows it."),
    ("p", "PO and SWR use approximate calibration points; ID (amps) follows the "
          "meter's 5/10/15 A marks. VDD is the supply voltage (RM8, also read while "
          "transmitting), approximate: 13.8 V at the meter's 13.8 mark; its bar spans "
          "9.25–15.25 V, 0.25 V per segment. Red zones: the last three segments of "
          "PO, ID and VDD, SWR from 2:1, ALC beyond its zone, and the first three of "
          "VDD (under 10 V). "
          "Peak hold: while transmitting a held segment marks each meter's highest "
          "reading; after the transmission the bars and values show these peaks — "
          "the values in a green box, PEAK HOLD lit beside it — until the next TX "
          "starts (switchable in Settings ▸ Appearance); for VDD it keeps the lowest "
          "voltage, the sag under load. CLEAR PH next to NAR drops the held peaks "
          "at once (shown only with peak hold on). "
          "SWR counts only with some power out. Values keep a fixed number of "
          "digits (SWR as 1.5:1), so nothing jumps. "
          "The needle moves like a moving-coil meter: damped, rising in about "
          "0.35 s and falling back a little slower."),
    ("h2", "Tuning"),
    ("ul", [
        "Mouse wheel or drag on a digit of the LCD frequency.",
        "Frequency entry with ▲▼ (mic UP/DN), A⇄B and A→B.",
        "BAND and MODE keys light up at once and confirm when the radio reports back.",
        "Shuttle next to BAND (◀ DOWN · TUNE · UP ▶): hold left of the centre to "
        "tune down, right to tune up; the further out, the bigger the steps (10 Hz, "
        "100 Hz, 1 kHz, 10 kHz) — the label shows the current step.",
        "CTCSS: off / ENC (send the tone) / TSQ (tone squelch) and the 50 tones, "
        "generated by the radio. MON switches the monitor. NAR selects the narrow filters; "
        "CLEAR PH beside it clears the held LCD peaks.",
    ]),
    ("h2", "TX power and RX · FILTER · LEVEL"),
    ("p", "The red TX POWER control sets 5–100 W. RX · FILTER · LEVEL opens the "
          "groups RX (preamp, AGC, ATT), FILTER (width, shift), LEVEL (RF gain in %), "
          "CLARIFIER and DSP; each value stands right beside its slider. The roofing filter is switched with R.FLT in the title bar."),
    ("ul", [
        "CLARIFIER: ON switches the RX clarifier, CLR sets the offset to 0; the slider "
        "sets the offset (±2.5 kHz in 10 Hz steps, double-click = 0) and switches "
        "RX CLAR on. The radio's offset is read back and checked.",
        "DSP: DNF, NR and NB as switches in one row; below them NOTCH (manual "
        "notch, 10–3000 Hz in 10 Hz steps) and CONTOUR (position 1–30), each with a "
        "switch, a slider and its value. The slider only sets the frequency or "
        "position, the switch turns the function on and off. Contour gain and "
        "width stay in the radio's menu (069/070).",
    ]),

    ("h1", "7  PTT · Memory"),
    ("img", "manual/ptt.png", 174),
    ("ul", [
        "PTT: hold the button or the space bar. The browser renews a lease every "
        "second — if the page goes silent, the Pi drops PTT after 2.5 s. Maximum "
        "180 s per over. The microphone is muted unless you transmit.",
        "PTT-LOCK latches transmit; ROGER adds a roger beep; 1.750 kHz arms the "
        "repeater tone call: the next PTT sends 1750 Hz instead of the microphone, "
        "releasing PTT disarms it (FM modes).",
        "MUTE mutes the receive audio (descending chime), DROP blanks only the "
        "current over and lifts when the station stops (ascending chime).",
        "PTT → VFO shows which VFO transmits. In the installed PWA, level bars "
        "for RX and MIC stand left and right of the PTT button.",
        "Memory keys M1–M32 (2 × 8 per side): click recalls frequency and mode to VFO A, "
        "in FM with CTCSS, plus VFO B and split if the key has them; hold "
        "3 s to store VFO A in that channel (confirmation before overwriting).",
    ]),
    ("p", "Settings ▸ Memories edits all 32 channels: frequency, mode and CTCSS "
          "(mode and tone) go into the radio with MW, verified by MR; split, VFO B "
          "and a name are kept on the Pi, because the FT-950's memories have none "
          "of them. DEFAULTS (M1-M32) fills one band per key row for "
          "80/40/20/15/10/6 m — left SSB | CW calling frequencies, right FT8 | FT4 — "
          "then 10/6 m FM, the 10 m repeater (split −100 kHz), SSTV and the "
          "IARU Region 1 emergency centres."),

    ("h1", "8  Audio"),
    ("img", "manual/audio.png", 174),
    ("p", "CONNECT starts WebRTC: the radio's audio plays in the browser, the "
          "microphone goes to the radio while you transmit. RX GAIN and TX GAIN "
          "set the levels. The switches in the title row: AGC rides the microphone "
          "toward a target level; MIC TEST records your microphone through the whole "
          "path and plays it back without transmitting — at listening level, the "
          "meters keep showing the real transmit level. On a desktop the browser's "
          "own microphone processing stays on (switching it off drops the level by "
          "about 40 dB on macOS); a built-in microphone is preferred to a Bluetooth "
          "headset."),
    ("p", "REC records the receive audio on the Pi (up to 30 minutes, 16 kHz), "
          "▶ plays it, ↓ WAV downloads it. Settings ▸ Audio adds a compressor and "
          "pre-emphasis for SSB voice, the FT8 TX drive, the codec output level "
          "and the FT-950's audio menus with saved defaults."),
    ("p", "The graph is split: on top the levels of the last 30 s, below the "
          "audio spectrum 0–3 kHz (the FT-950's SSB/DATA passband) — receive "
          "audio in violet with a yellow peak hold that falls away in about 10 s, "
          "your microphone in red while you transmit or run MIC TEST. PEAK in "
          "the legend switches the peak hold. The spectrum is computed in the "
          "browser and starts with CONNECT."),
    ("diagram", "audio", "en"),

    ("h1", "9  DIGI — FT8 / FT4"),
    ("img", "manual/digi.png", 174),
    ("p", "DECODE in the panel's title bar switches decoding on and off (as in "
          "the CW skimmer). Decoding runs every period with WSJT-X's jt9 — or a "
          "faster build chosen in Settings ▸ FT8 ▸ Decoder (see below); the WSJT-X plate in "
          "the title bar shows the one that runs (2.7, 2.7 fast, 3.3 MT). The passband is split "
          "over three cores. D1/D2/D3/AUTO in the footer pick the depth: D2 "
          "(default) puts the decodes on screen about 1.5–2 s after the period on a "
          "Pi 5, D3 finds a few more weak signals in about 3 s, AUTO follows the "
          "measured run time. Received messages list SNR, time offset, "
          "frequency, message and DXCC, with worked-before colouring."),
    ("p", "Title bar: DECODE, CQ with its target, Auto Answer, Auto Hunt, the "
          "WSJT-X plate, the next transmission, Halt TRX and the TX lamp. Strip "
          "under the tables: mode (AUTO · FT8, FT8, FT4), the cycle bar (RX or TX "
          "and the seconds into the period), EVEN/ODD, DECODE with the run time "
          "and the number of decodes of the last pass and the NTP LED, then RX "
          "with the receive level and TX with TUNE. Footer: dial frequency and "
          "power, decode depth, OVL (codec overload), the TX frequency with "
          "Adopt Frequency, palette, contrast and brightness of the waterfall, "
          "and the VOACAP band conditions."),
    ("ul", [
        "Click a decode to put the RX window (green frame, if switched on in "
        "Settings ▸ FT8) there. With Adopt "
        "Frequency on (footer, next to the TX frequency) the TX box (red) moves "
        "too, and Auto Answer and Auto Hunt answer on the partner's frequency — "
        "the TX box follows while their QSO runs. A click in the waterfall sets "
        "TX by hand and switches Adopt Frequency off, so the next station does "
        "not pull it away; Shift+click moves only the RX window. "
        "double-click answers the station — in Received and in My Messages — and "
        "selects its session in Sessions, so Session Messages shows that QSO. "
        "A double-click within the first ~1.7 s of your own TX period still "
        "transmits in that period (start at once, the partner sees a slightly "
        "positive DT); later it goes out in the next one.",
        "The filters above Received Messages show all, CQ only, RR73 (with RRR) "
        "only or 73 only. The arrow icon ↓ in each list head (follow) keeps the newest row in view, Session "
        "Messages included.",
        "A failed session shows just error in the list; the reason comes as a "
        "toast (when it happens and on a click on the row) and as the tooltip.",
        "With a session marked (click its row), QRZ Visit opens the station's QRZ.com "
        "page and Map Display a world map: position from the locator (without one, the "
        "centre of the DXCC entity), great circle, distance and bearing from your "
        "locator. Both open a popup window.",
        "CQ calls CQ — to everyone (–) or directed with the choice next to it: "
        "DX, EU, NA, SA, AF, AS, OC, POTA or SOTA (CQ DX DJ0SH JO62). Auto "
        "Answer: while idle, a station calling you is answered automatically.",
        "DX Call in the My Messages head calls a station typed in: call, grid "
        "(optional; 6 characters are cut to 4, an invalid one is dropped), period "
        "auto / even / odd, then Call DX. auto answers the station's last decode "
        "if it was heard, otherwise it calls in the next period. Your own call "
        "is refused; a click on a decode fills the fields.",
        "Auto Hunt calls new stations by itself: while no QSO runs, after every "
        "full decode pass it calls the best new CQ (a plain CQ, not CQ DX/NA/…; new "
        "DXCC first, then the strongest) and waits for the QSO to end. A station "
        "that never answers is struck through in Received Messages and not called "
        "again (the list survives restarts). While on, the button is red and shows "
        "Hunt and the time left as m:ss (∞: no switch-off). It calls only while a browser shows "
        "the DIGI panel. Whom it calls, the minimum SNR, the switch-off time "
        "(default 180 min, 0 = never) and the no-reply list are in Settings ▸ FT8.",
        "The sequencer follows WSJT-X: call, report, R-report, RR73, 73; it can "
        "log automatically: Auto-log in the Sessions header (default on) logs every "
        "completed QSO to Wavelog (the QRZ.com logbook by hand, LOG QRZ.COM), "
        "otherwise it is offered in the log form. A reply that comes in while our "
        "older message is still on the air is answered next period — the QSO is not "
        "closed before our own 73. While a QSO runs, a narrow decode around the "
        "partner (FT8 ±200 Hz, FT4 ±300 Hz — FT4 needs the wider margin) has their "
        "reply within ~0.1 s of the period end, so the next message usually starts "
        "0.5–1 s into our period; without a reply there it repeats at once. It can send RRR instead of RR73 "
        "(Settings ▸ FT8).",
        "RX in the strip sets the DIGI RX level on the Pi (software gain "
        "−20 … +20 dB, double-click: 0 dB); the meter right of it shows the "
        "receive level, target −40 … −20 dBFS. TX sets the FT8/FT4 TX drive (as "
        "FT8 TX DRIVE in Settings ▸ Audio); TUNE next to it sends a steady tone "
        "on the TX frequency and counts down the seconds left. "
        "Halt TRX, right in the title bar, is active during an "
        "FT8/FT4 transmission, a sequence or while Auto Hunt is on (dimmed "
        "otherwise); it stops at once and switches Auto Hunt off. NTP in the strip "
        "is an LED: green with the clock synchronised, red without (no decoding).",
    ]),
    ("h2", "Band conditions (VOACAP)"),
    ("p", "At the bottom right of the panel a sun or a moon and five chips — "
          "80-40, 30-20, 17-15, 12-10, 6m — show how the bands are open from your "
          "locator, in green, yellow or red, as iDigi shows them. The values are "
          "computed on the Pi with VOACAP (voacapl): VOACAP predicts a path, not a "
          "place, so the question goes to a ring of 36 paths around you (12 "
          "directions × 1500 / 4000 / 9000 km) at 100 W FT8 with an isotropic "
          "antenna. Per band the reliability is the mean over the ring, and a pair "
          "is as good as its better band: green from 0.6, yellow from 0.3, red "
          "below. Hovering a chip shows both bands in per cent and the trend of the "
          "next three hours."),
    ("ul", [
        "Sun or moon follows the real time of day at your locator. A click shows "
        "the other half of the day — the mean over today's daylight or darkness "
        "hours there — with a framed switch and dashed chips; a second click goes "
        "back to now.",
        "Updates: one run covers all 24 UTC hours of the month's median ionosphere "
        "(about 0.5 s, at low priority so jt9 never waits). It is computed once per "
        "UTC day and again when the locator or the sunspot number changes. The "
        "sunspot number is NOAA SWPC's latest monthly value, fetched once a day; "
        "the last good one is kept for an offline Pi. The page asks every 5 "
        "minutes, so the next hour, sunrise and sunset show within 5 minutes.",
        "Limits: VOACAP is a monthly median of the F2 layer. It does not see flares "
        "or geomagnetic storms, and on 6 m it shows only the rare F2 openings of a "
        "solar maximum — not sporadic-E, which opens 6 m in a European summer. A "
        "red 6m chip does not rule out Es.",
        "Without voacapl, or without a locator in Settings ▸ General, the strip is "
        "not shown.",
    ]),

    ("h1", "10  CW skimmer"),
    ("img", "manual/cw.png", 174),
    ("p", "DECODE starts the skimmer. Every CW carrier in the passband gets its "
          "own decoder; the table shows pitch, RF frequency, speed, SNR and the "
          "running text. Click a row to follow it: its text appears under Text, "
          "ZERO BEAT tunes VFO A onto it."),
    ("p", "Sending uses the radio's own keyer: the text is written to keyer memory "
          "5 and played (KM/KY), in chunks of 50 characters. The radio must be in "
          "CW or CW-R with BK-IN on. Macros (CQ, CALL, 5NN, RR, 73, AGN, QRZ) fill "
          "the send line; CALL is taken from the selected signal."),

    ("h1", "11  HackRF waterfall"),
    ("img", "manual/hackrf.png", 174),
    ("p", "The HackRF One sits on the second Pi with TM-V71 Remote. FT-950 Remote "
          "relays its waterfall (Settings ▸ General ▸ HackRF source). With follow "
          "on, the view shows the whole amateur band of VFO A; one click tunes VFO "
          "A there. The HackRF is shared: what you set here also applies to the "
          "TM-V71's waterfall."),
    ("p", "In the band view TM-V71 Remote sends a fine row of just that band "
          "(8192-point FFT, 244 Hz per point, 4 blocks averaged) — 820 points for "
          "40 m instead of about 50 from the normal 2 MHz row. The mouse shows "
          "frequency and level under the pointer."),

    ("h1", "12  Log"),
    ("p", "The LOG panel logs to Wavelog (API key and station profile) and "
          "the QRZ.com logbook — each with its own button beside the callsign: LOG "
          "WAVELOG and LOG QRZ.COM (the form stays, so a QSO can go to both; a ✓ "
          "marks where it went). QRZ LOOKUP fills name, QTH and "
          "locator; NEW STATION / WORKED shows in the title bar. The worked-before index is downloaded from Wavelog and colours "
          "the FT8 list. Settings ▸ Log holds the accounts — the keys never leave the Pi."),
    ("p", "In the title bar, LEDs as in the page's own title bar: WAVELOG and QRZ "
          "green when set up, AUTO green while completed QSOs are logged "
          "automatically (to Wavelog). The LOG buttons are only outlined while the "
          "form is empty and filled once a callsign is in it — then there is "
          "something to log. Recent QSOs: QRZ Visit and Map Display for the marked row."),

    ("h1", "13  Settings"),
    ("img", "manual/set-general.png", 120),
    ("ul", [
        "General: callsign, locator, CAT port and baud rate, Shelly and auto "
        "power-off, HackRF source, logo, root CA.",
        "FT8: the decoder — jt9 (WSJT-X 2.7, Debian package), jt9-fast (the same "
        "decoder built for the Pi 5 with its OSD stage in Rust: identical decodes, "
        "about twice as fast) or jt9-33 (WSJT-X 3.3 beta, multithreaded MTft8 with "
        "a Rust OSD: about 6 % more decodes, ready about 2.4 s after the period; "
        "it sets its own depth). Builds not installed are greyed out. Then decode "
        "depth (as D1/D2/D3/AUTO in the panel) and range, closing message RR73 or "
        "RRR, Auto Hunt (whom to call: not worked on this band, never worked, DXCC "
        "new on this band, DXCC never worked; minimum SNR; switch-off time, 0 = "
        "never; the no-reply list as a table — blocked time, call, country, grid, "
        "band, SNR, tries — with fixed column heads, × to take a call off, a field "
        "to add one by hand and Clear list; these edits apply at once), the "
        "waterfall (per browser, at once: the green RX window on/off — default off — "
        "and the default palette, picked by clicking its colour sample; the Palette "
        "choice under the waterfall then lasts only until the page is reloaded), and "
        "the colours of the message lists with preview and reset.",
        "Audio: capture device, codec level, FT8 TX drive, the FT-950 audio menus, "
        "voice TX processing, WebRTC buffers.",
        "Log: Wavelog and QRZ.com.",
        "Memories: the 32 memory keys with CTCSS, split and VFO B.",
        "Appearance (per browser): hide the DIGI, CW, HackRF and Log panels (VFO, "
        "PTT and Audio always stay; hiding the HackRF stops its sweep), an accent "
        "colour per panel with Reset, and peak hold of the LCD meters (PO · SWR · ALC · "
        "ID · VDD) on/off. "
        "Changes show at once and are kept with SAVE.",
        "Pi Hardware: load, temperature, memory, disk and a 24 h graph.",
    ]),
    ("img", "manual/set-ft8.png", 120),
    ("img", "manual/set-appearance.png", 120),
    ("p", "Every tab ends with the same bar: CLOSE, plus SAVE on General, FT8, Log and Appearance. "
          "On Audio changes apply at once, on Memories each row has its own WRITE, "
          "Pi Hardware is read only."),

    ("h1", "14  Remote power"),
    ("p", "A Shelly relay (Gen1 or Gen2/Plus/Pro, detected automatically) "
          "switches the radio's 13.8 V. The power switch in the title bar turns it "
          "on and off — before cutting the supply, a transmission and the sequencer "
          "are stopped. After switching off, power stays off for at least 3 s. While "
          "the radio is off a plate covers the page. Auto power-off switches the "
          "radio off when no browser has had the page open for the set time."),

    ("h1", "15  Troubleshooting"),
    ("ul", [
        "CAT offline: check the by-id port and menu 026 = 3 (38400 Bd).",
        "No receive audio: CONNECT, then check RX GAIN and that MUTE/DROP are off.",
        "Nothing decoded: NTP must be synchronised (NTP lamp), mode DATA-U, the "
        "DIGI level in the green range.",
        "FT8 too quiet: FT8 TX drive (Settings ▸ Audio) and menu 050.",
        "No microphone: the page must be opened over HTTPS with the trusted CA.",
        "After a backend restart: reload the page and CONNECT again.",
        "No band strip under the waterfall: apt install voacapl, and set a locator "
        "in Settings ▸ General.",
    ]),

    ("h1", "16  Technical reference"),
    ("diagram", "system", "en"),
    ("h2", "Backend"),
    ("ul", [
        "FastAPI on uvicorn, systemd unit with RuntimeDirectory, StateDirectory, "
        "memory limits and Restart=always.",
        "ft950.py: CAT driver with read-back verification; radio_service.py polls "
        "frequency/mode/meters every 0.1 s and the receive chain every second.",
        "digi.py: UTC-anchored slices, jt9 in parallel sub-bands, adaptive depth, "
        "early and partner passes; sequencer.py: the QSO state machine.",
        "voice.py: WebRTC hub, PTT lease, pre-roll, AGC, compressor, pre-emphasis.",
        "cw.py: the CW skimmer; hackrf_proxy.py; shelly.py; recorder.py.",
        "propagation.py: VOACAP band conditions — the ring of 36 paths, one run per "
        "UTC day, the day/night halves; GET /api/propagation.",
    ]),
    ("h2", "Files on the Pi"),
    ("code", "backend/runtime.json      station data and credentials (0600, not in git)\n"
             "backend/ex_defaults.json  saved FT-950 audio-menu defaults\n"
             "backend/certs/            server certificate (from the private CA)\n"
             "/run/ft950                decoder slices (tmpfs)\n"
             "/var/lib/ft950            jt9 working data\n"
             "backend/itshfbc/          VOACAP working tree (makeitshfbc copy)\n"
             "backend/propagation.json  last sunspot number from NOAA"),
    ("p", "Source, issues and updates: github.com/CQ-DJ0SH/ft950-remote — GPL v3."),
]
