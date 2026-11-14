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
        "auto-answer, a queue for several callers to one CQ, waterfall, DXCC and "
        "worked-before flags; spots of the stations heard to PSK Reporter.",
        "HF band conditions under the waterfall from real VOACAP for your locator: "
        "80-40, 30-20, 17-15, 12-10 and 6m in green / yellow / red, with a sun/moon "
        "switch for day and night.",
        "RTTY skimmer: every RTTY signal in the passband decoded at once (45.45 Bd, "
        "170 Hz); transmit as AFSK through the DATA input, with macros.",
        "CW skimmer: every CW signal in the passband decoded at once; send through "
        "the radio's keyer with macros, WPM slider and a browser sidetone (MON); a CW "
        "assistant moves a QSO on with one key (QSO or contest style, serial numbers "
        "on demand, auto CQ, caller detection, logging).",
        "JTTY, WSJT-X 3.2+'s keyboard mode: typed like RTTY at any time, with FEC "
        "(readable to about -15 dB); every message in the passband, conversation per "
        "station, macros, transmit through the DATA input.",
        "HackRF waterfall relayed from TM-V71 Remote, following the amateur band of VFO A.",
        "Logging to Wavelog and QRZ.com, auto-log switchable in the LOG title; "
        "worked-before index from Wavelog.",
        "QSL card by e-mail: your picture with the QSO data drawn on it, sent through "
        "your own mail account — one QSO, several marked ones or automatically after "
        "every log; stations that want no QSL (QRZ.com) are skipped.",
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
    ("p", "Optional nightly reboot that spares a running station: deploy/nightly-"
          "reboot.sh (installed as /usr/local/bin/ft950-nightly-reboot, root crontab "
          "00 4 * * *) reboots the Pi only while the radio is off — it skips the night "
          "when the FT-950 answers over CAT or its Shelly relay is on, and reboots "
          "when the backend does not answer. Each decision goes to the journal "
          "(journalctl -t nightly-reboot)."),
    ("p", "Then open Settings ▸ General: callsign, locator, CAT port and baud "
          "rate. Station data, logbook credentials and memory names are stored in "
          "backend/runtime.json (mode 0600) and never sent back to the browser."),

    ("h1", "5  The page"),
    ("img", "manual/titlebar.png", 174),
    ("p", "The title bar shows the logo (uploaded under Settings, never part of "
          "the repository), your callsign (shown with a slashed zero), the "
          "nameplate, the CAT and USB AUDIO lamps, the UTC clock, the theme switch, "
          "Settings and — once a Shelly is set up — the power switch."),
    ("p", "Below follow the panels VFO, PTT · MEMORY and AUDIO, DIGI, RTTY, CW, HackRF "
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
          "0.35 s and falling back a little slower. "
          "AFU above MHz lights up — green field, dark letters, like BUSY — while "
          "VFO A is inside an amateur band of the "
          "frequency plan chosen in Settings ▸ Appearance (Germany, IARU Region 1, 2 / "
          "USA or 3); its tooltip names the band and its limits."),
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
          "groups RX (preamp, AGC, ATT), FILTER (width, shift, squelch SQL 0–255), "
          "LEVEL (RF gain in %), CLARIFIER and DSP; FILTER to DSP are equally wide, "
          "each value stands right beside its slider. All sliders look as in "
          "TM-V71 Remote: filled in the panel colour up to a rectangular fader knob, "
          "with a thin ring in that colour (transmit sliders red). The roofing filter is switched with R.FLT in the title bar."),
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
          "the CW skimmer); off, the waterfall stops and is cleared (the Pi then "
          "computes no waterfall either). AUTO picks FT8 or FT4 by the nearest "
          "standard frequency — on 80 m FT8 (3.573) and FT4 (3.575 MHz) are only "
          "2 kHz apart. Decoding runs every period with WSJT-X's jt9 — or a "
          "faster build chosen in Settings ▸ FT8 ▸ Decoder (see below); the WSJT-X plate in "
          "the title bar shows the one that runs (2.7, 2.7 fast, 3.3 MT). The passband is split "
          "over three cores. D1/D2/D3/AUTO in the footer pick the depth: D2 "
          "(default) puts the decodes on screen about 1.5–2 s after the period on a "
          "Pi 5, D3 finds a few more weak signals in about 3 s, AUTO follows the "
          "measured run time. Received messages list UTC and local time (the "
          "time on every row, dimmed after the first of a pass), a level column "
          "(five bars, lit from -20 / -15 / -10 / -3 / +5 dB), SNR, time offset, "
          "frequency, message, DXCC with worked-before colouring and the distance in "
          "km from your locator — from the station's locator (this message or an "
          "earlier one), else marked ~ and measured to the centre of its DXCC entity."),
    ("p", "Title bar: DECODE, CQ with its target, Auto Answer, Auto Hunt, the "
          "WSJT-X plate, the next transmission, Halt TRX and the TX lamp. Strip "
          "under the tables: mode (AUTO · FT8, FT8, FT4), the cycle bar (RX or TX "
          "and the seconds into the period), EVEN/ODD, DECODE with the run time "
          "and the number of decodes of the last pass, the NTP LED and the PSK "
          "Reporter LED, then RX with the receive level and TX with TUNE. Footer: "
          "dial frequency and power (framed), decode depth, OVL (codec overload), the TX frequency with "
          "Adopt Frequency, palette, contrast and brightness of the waterfall, "
          "and the VOACAP band conditions."),
    ("ul", [
        "Click a decode to put the RX window (green frame, if switched on in "
        "Settings ▸ FT8) there. With Adopt "
        "Frequency on (footer, next to the TX frequency) the TX box (red) moves "
        "too, and Auto Answer and Auto Hunt answer on the partner's frequency — "
        "the TX box follows while their QSO runs. A click in the waterfall sets "
        "TX by hand (the click is the middle of the box, so it sits over the "
        "signal clicked; the TX frequency itself stays its lowest tone, as in "
        "WSJT-X) and switches Adopt Frequency off, so the next station does "
        "not pull it away; Shift+click moves only the RX window. "
        "double-click answers the station — in Received and in My Messages — and "
        "selects its session in Sessions, so Session Messages shows that QSO. "
        "A double-click within the first ~1.7 s of your own TX period still "
        "transmits in that period (start at once, the partner sees a slightly "
        "positive DT); later it goes out in the next one.",
        "Palette Hologram (dark blue to cyan, as the globe) adds a callsign strip "
        "at the foot of the waterfall: every decoded station of the last period "
        "with a tick at its frequency, in up to four rows. The waterfall gets "
        "shorter by that strip, so the labels never cover the signals. Hovering "
        "a callsign shows country, SNR and the message; a double-click calls the "
        "station, as in Received. Settings ▸ FT8 ▸ Callsigns (all palettes) shows "
        "the strip with every palette.",
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
        "My Messages: on your TX lines the last column (DXCC/CNT) counts the "
        "attempts — 2/5 for a QSO message (five unanswered repeats end the "
        "sequence), 3/40 for CQ; the last attempt is amber.",
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
        "Hunt and the two periods: after a QSO, Hunt calls from the first period it "
        "hears — the one opposite to our TX — and so keeps to one parity for long "
        "runs. Settings ▸ FT8 ▸ \u201cPick only after hearing both periods\u201d "
        "(default off) makes it listen one period more (about 15 s per QSO) and "
        "pick the best station of both, even and odd.",
        "Hunt's own CQ: when Hunt hears no new station for 4 periods (full passes, "
        "about a minute in FT8), it calls CQ itself — on a free spot it measured over "
        "those periods: every decoded signal and any energy more than 6 dB over the "
        "noise count as occupied, and the CQ goes into the middle of the widest free "
        "gap between 600 and 2400 Hz (no gap: the quietest window). A caller is worked "
        "as usual; the next new station's CQ ends Hunt's CQ and is called (a caller to "
        "our CQ in the same period comes first). The button then reads Hunt · CQ. "
        "Hunt off stops its CQ (a QSO running ends first). While the CQ runs, Hunt "
        "hears only the other period. Settings ▸ FT8 ▸ \u201cCQ after quiet periods\u201d "
        "(default 4, 0 = Hunt only listens).",
        "Timing: the next message is decided about 0.7 s into the period and goes "
        "on the air at about +1 s (the latest start is +2 s). An early pass at "
        "13.4 s searches the whole band, split over three cores (~0.6 s), while "
        "the other station is still sending — callers, a partner who moved and "
        "an empty period are known before our next start. Right after the period "
        "a narrow partner pass (~0.4 s) checks the partner's frequency; the full "
        "pass (~1.5 s) fills the tables. Measurements: docs/FT8-DECODING.md §13.",
        "SKIP in the Sessions header (amber while a QSO runs) ends the QSO in "
        "progress at once: TX to that station stops, and the rest goes on — CQ "
        "calls again (or takes a waiting caller), Auto Hunt and Auto Answer go to "
        "the next station. A station Auto Hunt was calling goes on the no-reply "
        "list, so it is not picked again straight away; the session shows as "
        "skipped. HALT, by contrast, stops everything.",
        "The sequencer follows WSJT-X: call, report, R-report, RR73, 73; it can "
        "log automatically: Auto-log in the Sessions header (default on) logs every "
        "completed QSO to Wavelog, and Settings ▸ Log can send it to the QRZ.com "
        "logbook as well (its own switch, default off); with neither it is offered "
        "in the log form. A session counts as logged when every target accepted it. "
        "Answers that differ from the textbook: RR73 or RRR while we still repeat "
        "our report is answered with 73 (their RR73 says our report arrived), "
        "\u201cR <locator>\u201d (WSJT-X's contest-style acknowledgement) counts like an "
        "R-report and is answered with RR73 (RST received stays empty), and a bare "
        "73 completes the QSO only once the reports went across — sent too early, "
        "it is ignored, so nothing is logged or carded half-done. "
        "A new QSO the sequencer starts by itself (Auto Hunt, Auto Answer, a caller "
        "to our CQ) is selected in Sessions, so Session Messages follows it; a "
        "session clicked afterwards stays until the next one starts. While a QSO "
        "runs, Session Messages also lists the partner's messages to others (their "
        "CQ, their QSO with someone else) in grey — they change nothing in the "
        "sequence; so is the decode that started the session when it was not "
        "addressed to you. Our own lines appear as soon as the next message is "
        "decided, about a second before the radio keys: TX ▷ dimmed while "
        "pending, TX ▶ once on the air, TX ✕ struck through if it did not go out "
        "(too late for the period, or halted). "
        "A reply that comes in while our "
        "older message is still on the air is answered next period — the QSO is not "
        "closed before our own 73. While a QSO runs, a narrow decode around the "
        "partner (FT8 ±200 Hz, FT4 ±300 Hz — FT4 needs the wider margin) has their "
        "reply within ~0.1 s of the period end, so the next message usually starts "
        "0.5–1 s into our period; without a reply there it repeats at once. It can send RRR instead of RR73 "
        "(Settings ▸ FT8).",
        "Several callers to one CQ: the first goes to a QSO, the others wait in a "
        "queue and are answered one after the other when the QSO ends, instead of "
        "the next CQ. Order: new DXCC, DXCC new on this band, not worked on this "
        "band, then the strongest. A waiting station drops out after 2 minutes "
        "without a new call, or as soon as it is heard with someone else or "
        "calling CQ; one just worked does not queue again. Waiting stations are "
        "listed in italics at the end of Sessions (next, waiting 2, …; the tooltip "
        "shows their message and SNR); a click puts one at the front.",
        "A caller decoded while our CQ is already on the air: the CQ is cut and "
        "the answer goes out in the same period, as long as it can still start in "
        "time (FT8 up to 1.7 s into the period, FT4 1.2 s); later it goes out in "
        "the next period. A cut CQ does not count towards the CQ limit.",
        "Calls FT8 cannot carry in full (DL1ABC/QRP, W1AW/4, PA/DJ0SH — only /P "
        "and /R fit) go out as WSJT-X sends them, in short (hashed) form: "
        "<DL1ABC/QRP> DJ0SH JO62. The other station recognises its own call; "
        "your call, grid and reports stay in full.",
        "PSK Reporter (Settings ▸ FT8): every station decoded here is reported to "
        "pskreporter.info, with your call and locator as the receiver. The PSK "
        "Reporter LED in the strip is green after a good upload, red when the last "
        "one failed, dark until the first; its tooltip has the details. While the "
        "reporting is off the LED is hidden, its place kept. Below 1450 px window "
        "width the label shortens to PSK.",
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
    ("h2", "Globe"),
    ("img", "manual/globe.jpg", 174),
    ("p", "Globe, beside the title of Received Messages, opens a popup with the "
          "stations heard on a slowly turning, see-through hologram globe (land as "
          "an even dot grid that looks the same from any side, the poles included); your "
          "own station stands out in amber with call and locator. Stations of the "
          "last 30 minutes are points with their call (where they would overlap, "
          "the important ones win): magenta a new DXCC, green new on this band, red "
          "worked; a hollow ring sits at the centre of the DXCC entity when the "
          "station sent no locator. A call that appears for the first time draws a "
          "line out from you that fades after about 20 s; stations calling CQ now "
          "(last 45 s) have a dashed line, the QSO in progress an amber line with "
          "pulses running along it."),
    ("ul", [
        "The table beside it has your call as its title, the frequency with band "
        "and mode, a TX lamp (red and glowing while transmitting, with the message "
        "that goes out) and HALT TRX, as on the main page. Columns: UTC, call (with "
        "a CQ tag while it calls), locator, km, bearing, SNR, DXCC.",
        "A click on a row (or a point) turns the globe to the station and stops it "
        "there; RESUME, bottom left, turns it on again - also after turning it by "
        "hand with the mouse. The wheel zooms. A station clicked on the globe is "
        "marked in the table too (cyan stripe) and scrolled into view; the table "
        "keeps it there while it re-sorts, until you scroll it yourself.",
        "While transmitting, a small TX line at the top right of the globe shows "
        "what goes out (TX ▸ message, TUNE, VOICE or PTT). During a QSO it shows "
        "the partner's last message above it (RX ◂ message and SNR) and stays "
        "through the receive periods too, your last message then dimmed.",
        "✕ CLEAR in the table footer empties table and globe; only stations heard "
        "afterwards come back. GRAY LINE beside it (amber when on, remembered) "
        "shades the night side from the sun's position now — twilight to −12°, "
        "the day side faintly lit — with the day/night border as a dashed amber "
        "line: DX along it often goes well.",
        "A double-click on a row calls the station - through the main page, exactly "
        "as a double-click in Received Messages (RX window, Adopt Frequency, log "
        "form, then the call). Without the main page the popup does not call.",
        "Under the globe runs the FT8 spectrum across the full width: a waterfall "
        "over the decoder's passband (200-3000 Hz from Settings ▸ FT8, the DIGI "
        "waterfall's data) in the hologram colours with the current spectrum over "
        "it, the stations of the last period with their call at their audio "
        "frequency (only while the legend is open — folded, the strip stays "
        "clean), the QSO partner in amber and your TX frequency as a red line "
        "while a sequence runs. With DECODE off it is empty. Legend and FT8 "
        "spectrum fold with their header line (▾/▸); the browser remembers it.",
        "The popup follows the main page's light or dark theme (☀/☾), also while "
        "it is open.",
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

    ("h1", "10  RTTY skimmer"),
    ("img", "manual/rtty.png", 174),
    ("p", "The RTTY panel below DIGI decodes every RTTY signal in the passband at "
          "once — standard amateur RTTY: 45.45 baud, 170 Hz shift, Baudot with US "
          "figures, 1.5 stop bits. DECODE switches it on (it runs on the Pi only "
          "while on). Each signal gets a row: tone (the audio centre between mark "
          "and space), frequency on the air, SNR, quality (share of characters with "
          "a correct stop bit) and the running text; a click shows its whole text on "
          "the right and takes its callsign into CALL."),
    ("ul", [
        "How it works: every 0.5 s a spectrum of the last 2 s is searched for "
        "pairs of peaks 170 Hz apart with a dip between them (up to 8 signals, "
        "slow drift is followed). Per signal, mark and space go through a filter "
        "matched to the bit; their levels are compared after each is normalised "
        "to its own peak, so fading of one tone does not flip the bits. A UART "
        "finds start bit, five data bits and stop bit. A character only counts "
        "where one tone stands clearly above the other, so noise prints nothing, "
        "and a row is shown only once most of its frames are clean.",
        "Mark and space: on the air the mark is the higher frequency. In LSB / "
        "DATA-L that is the lower audio tone, in USB / DATA-U the higher one — the "
        "panel follows the radio's mode (MARK ↓ / ↑). REV swaps them, for a "
        "station sending reversed (receive and transmit).",
        "Transmit: AFSK through the DATA input, like FT8 — the radio in DATA-L "
        "(usual), DATA-U or USB, at the FT8 TX drive. The text goes into the send "
        "line, by hand or from the macros CQ, CALL, 599, TU, AGN, QRZ and RY "
        "({MY}, {LOC} and the CALL field are filled in); Enter or SEND transmits "
        "it with CR LF before and after, mark idle in front. TX is the centre "
        "frequency in Hz of audio (300–2600); a click on a signal sets it to that "
        "station; DEFAULT puts it back to 2210 Hz (mark 2125 / space 2295 Hz). "
        "STOP and Halt TRX stop at once; a watchdog ends the transmission "
        "at the latest 3 s after its length. While the FT8 sequencer or Auto Hunt "
        "runs, RTTY does not transmit.",
        "Own transmissions in Text: each appears as a red TX block in the received "
        "text of the station at the TX frequency, at the point it went out — the "
        "QSO reads in order and the block stays in place while the text scrolls. "
        "Sent keeps the list. While we transmit (and 0.6 s after) the RTTY, CW and "
        "JTTY receivers hear silence: the radio puts our own signal on its audio "
        "out, and it would come back as an echo of the TX text.",
        "The panel can be hidden and gets its own accent colour in Settings ▸ "
        "Appearance.",
    ]),

    ("h1", "11  CW skimmer"),
    ("img", "manual/cw.png", 174),
    ("p", "DECODE starts the skimmer. Every CW carrier in the passband gets its "
          "own decoder; the table shows pitch, RF frequency, speed, SNR and the "
          "running text. Click a row to follow it: its text appears under Text, "
          "ZERO BEAT tunes VFO A onto it."),
    ("p", "Sending uses the radio's own keyer: the text is written to keyer memory "
          "5 and played (KM/KY), in chunks of 50 characters. The radio must be in "
          "CW or CW-R with BK-IN on. Macros (CQ, CALL, 5NN, RR, 73, AGN, QRZ) fill "
          "the send line with every word once (CQ DE <call> K) — click twice to "
          "repeat; CALL is taken from the selected signal, a call typed in stays "
          "until another signal is clicked or the field is emptied. Own transmissions "
          "show as red TX blocks in the received text of the signal on the radio's "
          "pitch (the one we are zero-beat with), as in RTTY; while we send, the "
          "skimmer hears silence (no echo)."),
    ("ul", [
        "WPM is a slider (4–60) in the panel head; it sets the keyer speed of the "
        "radio (KS) as soon as you let go, and follows the radio every 10 s. Each "
        "time the service connects to the radio it starts at 20 WPM "
        "(FT950_CW_START_WPM in .env, 0 keeps the radio's speed). The "
        "pitch beside it is read from the radio (KP).",
        "Before each send the panel switches the radio's keyer on (KR1) and sets "
        "CW memory 5 to TEXT (menu 023), both checked by read-back. The text is "
        "played with KYA - on the FT-950, text written with KM plays with "
        "KY6…KYA (memory 1…5), not KY1…5. A message recorded with the paddle in "
        "memory 5 is therefore not available.",
        "MON plays your own CW in the browser: the radio's sidetone and monitor "
        "reach only its speaker, not the Pi. The page keys a tone at the radio's "
        "speed and pitch, paced like the radio's chunks, with soft edges (no "
        "clicks). It is made in the browser and never transmitted; STOP ends it "
        "with the radio, MON off at once.",
        "If something does not go out, GET /api/cw/diag shows (read only) the "
        "keyer, memory 5 type, both key jacks and the content of memory 5.",
    ]),
    ("p", "The CW assistant (the row above the send line) moves a QSO on with one "
          "key: NEXT, or Enter in an empty send line or in CALL. The button names the "
          "step it sends."),
    ("ul", [
        "Calling CQ: CQ → REPORT → TU + LOG, then CQ again. After your CQ, a call "
        "heard near your own pitch (where a caller zero-beats) comes into CALL, "
        "framed — it is only answered when you press NEXT. What the other station "
        "sends is read from its text and shown as RCVD: RST, serial number (cut "
        "numbers such as 1T2 = 102), name and QTH; it goes into the log.",
        "Answering a station: a double-click on a signal tunes onto it (zero beat), "
        "takes its call into CALL and sends ANSWER; then REPORT and 73 + LOG "
        "(contest: REPORT + LOG).",
        "QSO or CONTEST: QSO sends the full exchange with RST 599, NAME and QTH "
        "(stored in the browser); CONTEST the short one (CQ TEST, CALL 5NN, TU).",
        "SERIAL adds a serial number to the report on demand; the field shows the "
        "next one (editable), it counts up after every logged QSO.",
        "AUTO CQ repeats CQ with a listening PAUSE until a station answers, STOP or "
        "RESET — at most 30 calls. It runs in this browser: closing the page stops it.",
        "The log entry goes to Wavelog and QRZ.com as mode CW with RST sent and "
        "received, name, QTH, start and end time, with SERIAL also STX/SRX.",
    ]),

    ("h1", "12  JTTY"),
    ("img", "manual/jtty.png", 174),
    ("p", "JTTY is the keyboard mode of WSJT-X 3.2 and later, by Joe Taylor K1JT, "
          "Steve Franke K9AN, Rob Sherwood G4KLA and the WSJT-X team (GPL v3). It is "
          "worked like RTTY — both sides type and send whenever they like, there are "
          "no T/R periods — but with forward error correction: 4-GFSK at 31.25 baud, "
          "127 Hz wide, frames of 1.89 s, readable down to about −15 dB where 45.45-baud "
          "RTTY is long gone. Calls, reports and locators travel in compact form."),
    ("ul", [
        "DECODE starts jttyd on the Pi (decoder/jttyd, built against WSJT-X 3.3's "
        "decoder). It first sets up its tables on a test signal of its own (STARTING, "
        "about 25 s), then listens over the whole passband — about 12 % of one core.",
        "Every message is a row: UTC, audio frequency, SNR, text; one still arriving "
        "is shown in italics with … . A click follows that station: Text shows its "
        "conversation (every message on its frequency), CALL takes its call, TX its "
        "frequency. Our own transmissions join the list and the conversation as TX "
        "lines in red (\u201csending …\u201d while on the air); received ones are labelled "
        "RX. CALL is taken only from what the other station sent.",
        "Sending: the send line or the macros, WSJT-X's F-key forms with every call "
        "once (JTTY's FEC needs no repeats): CQ (CQ <me> CQ, 1 frame), CALL, MY, 599, "
        "TU CQ, 73, AGN, QRZ — 1 to 3 frames each. A call typed into CALL stays until "
        "another station is clicked. Enter or SEND transmits at once — no "
        "waiting for a period. The radio must be in DATA-U or USB; the drive is the FT8 "
        "TX drive; in DATA-L or LSB (mirrored tones, undecodable) JTTY refuses to "
        "transmit. STOP and Halt TRX end it; while the FT8 sequencer runs, JTTY does "
        "not transmit. DEFAULT puts TX on 1500 Hz, the middle of the calling range.",
        "BAND tunes VFO A to a JTTY dial frequency in DATA-U (WSJT-X 3.3's list, "
        "preliminary): 1.839, 3.572, 7.078, 10.140, 14.090, 18.104, 21.090, 24.920, "
        "28.090, 50.160 MHz. 20 m at 14.090 MHz is the suggested first meeting place.",
        "WSJT-X's decoder listens on the receive frequency and 1200–1800 Hz; jttyd "
        "sets its tolerance to the whole passband, so every JTTY signal is found — "
        "measured without loss of sensitivity.",
    ]),

    ("h1", "13  HackRF waterfall"),
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

    ("h1", "14  Log"),
    ("p", "The LOG panel logs to Wavelog (API key and station profile) and "
          "the QRZ.com logbook — each with its own button beside the callsign: LOG "
          "WAVELOG and LOG QRZ.COM (the form stays, so a QSO can go to both; a ✓ "
          "marks where it went). QRZ LOOKUP fills name, QTH and "
          "locator; NEW STATION / WORKED shows in the title bar. The worked-before index is downloaded from Wavelog and colours "
          "the FT8 list. Settings ▸ Log holds the accounts — the keys never leave the Pi."),
    ("p", "In the title bar, LEDs as in the page's own title bar: WAVELOG and QRZ "
          "green when set up. Three switches (filled = on): AUTO WAVELOG logs QSOs "
          "completed by the sequencer to Wavelog (as Auto-log in Sessions and "
          "Settings ▸ Log), AUTO QRZ to the QRZ.com logbook as well, AUTO QSL sends "
          "the QSL card by e-mail after every successful log (chapter 15). The LOG "
          "buttons are only outlined while the form is empty and filled once a "
          "callsign is in it — then there is something to log."),
    ("p", "Recent QSOs: a click marks a row, Ctrl/Cmd-click adds or removes one, "
          "Shift-click marks a range. QRZ Visit, Map Display and QSL Card (shows the "
          "card) work on the last clicked row, Send QSL on all marked ones. The list "
          "follows changes made on the Pi by itself (checked every 5 s): QSOs "
          "logged automatically and QSL cards sent by AUTO QSL appear at once, the "
          "latter with a notice. The QSL "
          "column shows ✓ sent, ✗ not sent, ⊘ skipped — hover for why."),

    ("h1", "15  QSL card by e-mail"),
    ("img", "manual/qsl-card.jpg", 150),
    ("p", "A QSL card is your own picture with the QSO data drawn on it on the Pi: "
          "your callsign as a label (in the nameplate font, 0 as Ø), the data block "
          "(to radio, date, UTC, band, mode, RST, QSL and below it OP, QTH, RIG, PWR, "
          "ANT side by side) and a note of its own. The mail is HTML with the card in "
          "it, the page's title plate in the header (steel title bar, both calls in "
          "amber), a link to this project and a plain-text part; it goes out through "
          "your own mail account. In the HTML part your own call is written with Ø as "
          "on the card; the plain-text part keeps the 0, so the call can be copied."),
    ("img", "manual/set-qsl.jpg", 120),
    ("ul", [
        "Settings ▸ QSL ▸ Card: + adds pictures (rotated and reduced to 2000 px on "
        "the Pi), a click picks one. The preview is drawn by the Pi exactly as the "
        "card will be; drag the callsign, the data block and the note into place. "
        "The note takes placeholders, e.g. TNX {CALL} 73; empty lines are left out.",
        "Colours: Callsign colour (the label on the card and both calls in the mail "
        "header, default amber), Data block colour (values; the labels and the "
        "divider take it fainter, default white) and Note colour (default "
        "turquoise) — each with a reset button; the preview follows at once. The "
        "other station's call is never wider than 10 characters (DJ0ABC/QRP): a "
        "longer one is drawn smaller, so the data block does not grow into the "
        "other blocks.",
        "Mail server: SMTP server, port and security, username and password (stays "
        "on the Pi, never sent back to the browser), sender. With a username the "
        "Pi logs in (587 STARTTLS or 465 SSL/TLS) — the mail then gets your "
        "domain's DKIM signature and passes DMARC. Without a username it is handed "
        "over without login, for a server that relays for the Pi by its address; "
        "its DKIM signer and SPF must know that address too. Test login only logs "
        "in, it sends nothing.",
        "E-mail: subject and text with placeholders {CALL} {NAME} {MY} {OP} {DATE} "
        "{UTC} {BAND} {MODE} {RST} {FREQ}. Title colour sets the mail's title bar "
        "(the gradient and edges follow from it; STEEL resets it). Preview mail "
        "opens the mail as the recipient sees it, with the form as it stands — "
        "sample QSO, nothing saved or sent. Send test card mails a card for a sample "
        "QSO to the test recipient.",
        "Sending: mark QSOs in Recent QSOs and click Send QSL. The address comes "
        "from QRZ.com (XML lookup, Settings ▸ Log). For one QSO the confirmation "
        "shows it, editable, with QRZ.com's QSL hints (eQSL / LoTW / paper and the "
        "operator's QSL note). For several, one confirmation; stations without an "
        "address, those that want no QSL and QSOs that already got a card are "
        "skipped and named.",
        "One card per station, band and mode and UTC day: AUTO QSL and several marked "
        "QSOs skip a station that already got its card today on that band and mode "
        "(QSL column ⊘); a single card sent by hand still goes, after a warning.",
        "No QSL wanted: a QSL note on QRZ.com such as NO QSL, NO EQSL, NO CARDS, "
        "LOTW ONLY, QRZ ONLY or OQRS ONLY, or eQSL: no. Such stations are skipped "
        "when several QSOs are marked and by AUTO QSL; for a single QSO the "
        "confirmation warns and leaves the choice to you.",
        "AUTO QSL (LOG title, or Settings ▸ QSL ▸ Automation) sends the card after "
        "every successful log, in the background on the Pi — no browser needed.",
        "What stays on the Pi only: the mail account with its password, the "
        "pictures and the title plate (backend/qsl_images/, runtime.json, "
        "backend/app/assets/ — all gitignored).",
    ]),

    ("h1", "16  Settings"),
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
        "RRR, PSK Reporter (switch, off by default, with the destination "
        "report.pskreporter.info:4739 (UDP) beside it; an optional antenna "
        "description; links to the PSK Reporter map — stations heard by you and "
        "stations that hear you, last hour; statistics since the service started: "
        "uploads, spots, different stations, waiting, last upload, spots per band. "
        "Uploads every 5 minutes, each station at most once an hour per band and "
        "mode, sooner only when its locator becomes known; unsure decodes and your "
        "own call are not reported), Auto Hunt (whom to call: not worked on this band, never worked, DXCC "
        "new on this band, DXCC never worked; minimum SNR; switch-off time, 0 = "
        "never; the no-reply list as a table — blocked time, call, country, grid, "
        "band, SNR, tries — with fixed column heads, × to take a call off, a field "
        "to add one by hand and Clear list; these edits apply at once), the "
        "waterfall (per browser, at once: the green RX window on/off — default off —, "
        "the callsign strip for all palettes "
        "and the default palette, picked by clicking its colour sample; the Palette "
        "choice under the waterfall then lasts only until the page is reloaded), and "
        "the colours of the message lists with preview and reset.",
        "Audio: capture device, codec level, FT8 TX drive, the FT-950 audio menus, "
        "voice TX processing, WebRTC buffers.",
        "Log: Wavelog and QRZ.com; automation: QRZ lookup before every log, "
        "auto-log to Wavelog (as Auto-log in Sessions) and, separately, auto-log to "
        "the QRZ.com logbook (needs its API key).",
        "QSL: card picture and layout, mail server, e-mail text, test card, "
        "automatic sending (chapter 15).",
        "Memories: the 32 memory keys with CTCSS, split and VFO B.",
        "Appearance (per browser): hide the DIGI, RTTY, JTTY, CW, HackRF and Log panels "
        "(VFO, PTT and Audio always stay; hiding the HackRF stops its sweep), an accent "
        "colour per panel with Reset, peak hold of the LCD meters (PO · SWR · ALC · "
        "ID · VDD) on/off, and the AFU lamp on/off with its frequency plan. "
        "Changes show at once and are kept with SAVE. SAVE AS DEFAULT stores this "
        "browser's accent colours on the Pi as the default for every browser — a "
        "browser's own colours still win.",
        "Pi Hardware: load, temperature, memory, disk and a 24 h graph; below it the "
        "WSJT-X decoder (jt9) on its own — the last pass (CPU seconds, run time, "
        "parts), its memory (the parallel parts added up, peak) and the CPU of the "
        "last minute as a share of the Pi, with its own 24 h graph of CPU share and "
        "memory. CPU time comes from the kernel when a jt9 run ends; the memory is "
        "jt9's own peak, read while it runs. "
        "Two rows count audio dropouts since the service started: Audio · sound card "
        "shows the codec's overruns (samples lost — the decoder, waterfall and browser "
        "all miss them), slow 20 ms chunks (more than 15 ms to process) with the longest "
        "one, and arecord restarts; Audio · to browser shows gaps (a browser's buffer "
        "ran dry: a short silence), drops (a browser fell behind and the oldest audio "
        "was discarded) and how many browsers are listening. All at 0 means clean audio.",
    ]),
    ("img", "manual/set-ft8.png", 120),
    ("img", "manual/set-appearance.png", 120),
    ("p", "Every tab ends with the same bar: CLOSE, plus SAVE on General, FT8, Log, QSL and Appearance. "
          "On Audio changes apply at once, on Memories each row has its own WRITE, "
          "Pi Hardware is read only. While Settings is open, the animations behind it "
          "pause (cycle bar, meters, spectrum, waterfalls) to save the browser's CPU; "
          "data and decoding go on, and the waterfalls draw what came in on close."),

    ("h1", "17  Remote power"),
    ("p", "A Shelly relay (Gen1 or Gen2/Plus/Pro, detected automatically) "
          "switches the radio's 13.8 V. The power switch in the title bar turns it "
          "on and off — before cutting the supply, a transmission and the sequencer "
          "are stopped. After switching off, power stays off for at least 3 s. While "
          "the radio is off a plate covers the page. Auto power-off switches the "
          "radio off when no browser has had the page open for the set time."),

    ("h1", "18  Troubleshooting"),
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

    ("h1", "19  Technical reference"),
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
        "cw.py: the CW skimmer; rtty.py: the RTTY skimmer and AFSK; jtty.py with "
        "decoder/jttyd: JTTY receive and transmit; dec_history.py: "
        "the decoder figures; hackrf_proxy.py; shelly.py; recorder.py.",
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
