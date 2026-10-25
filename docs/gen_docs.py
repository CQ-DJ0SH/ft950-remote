#!/usr/bin/env python3
"""Generate the FT-950 Remote manuals (English + German) into docs/.

Framework taken over from TM-V71 Remote. Run:  backend/.venv/bin/python docs/gen_docs.py
(needs fpdf2: backend/.venv/bin/pip install fpdf2)
"""
import os
import re
import time

from fpdf import FPDF

HERE = os.path.dirname(os.path.abspath(__file__))


def jpeg(path, max_w=1800, quality=88):
    """Screenshots as JPEG for the PDF (a third of the PNG size, no visible loss)."""
    import io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return buf
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONTI = FONT   # DejaVu ships no Oblique here; reuse the regular face for "I"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

ACCENT = (176, 98, 0)         # FT-950 amber, dark enough for print
DARK = (28, 39, 49)
GREY = (110, 122, 132)
CODEBG = (244, 246, 248)


def _app_version() -> str:
    """The version the running app reports, read from its one definition.

    The manual used to carry a copy of the number and the two drifted apart —
    the app said 3.4 while the PDFs still said 3.2. Parsed rather than
    imported: importing the config module would load the settings, and reading
    runtime.json has no business in a documentation build.
    """
    src = os.path.join(HERE, "..", "backend", "app", "config.py")
    with open(src, encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:                       # fail loudly: a wrong version is worse
        raise SystemExit(f"APP_VERSION not found in {src}")
    return m.group(1)


VERSION = _app_version()


RULE = (206, 216, 222)        # hairlines for header/footer
HALF = 87                     # half the type area (174 mm), for L/R cells


class Doc(FPDF):
    """A4 with a running head: product left, current chapter right.

    The chapter caption is what makes a long manual navigable — on any page it
    says which of the fourteen chapters you are in. Cover and contents carry no
    furniture (running_head stays False there)."""
    title_txt = ""            # document title, shown in the footer
    chapter = ""              # current chapter, shown in the running head
    running_head = False      # off for the cover
    opening = False           # this page opens a chapter -> no caption

    def header(self):
        if self.page_no() <= 1 or not self.running_head:
            return
        self.set_y(11)
        self.set_font("DV", "", 7.6)
        self.set_text_color(*GREY)
        self.cell(HALF, 4, "FT-950 REMOTE", align="L")
        # A page that opens a chapter carries no caption: the title stands right
        # below it in 22 pt, and repeating it is the classic redundancy.
        self.cell(HALF, 4, "" if self.opening else self.chapter,
                  align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(18, 16.4, 192, 16.4)
        self.set_y(22)

    def footer(self):
        if self.page_no() <= 1:      # the cover carries no furniture; the flag
            return                   # is already back on when this page closes
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(18, self.h - 15, 192, self.h - 15)
        self.set_y(-13)
        self.set_font("DV", "", 7.6)
        self.set_text_color(*GREY)
        self.cell(HALF, 5, f"{self.title_txt} · Version {VERSION}", align="L")
        self.cell(HALF, 5, f"{self.page_no()} / {{nb}}", align="R",
                  new_x="LMARGIN", new_y="TOP")


def new_pdf(title):
    pdf = Doc(orientation="P", unit="mm", format="A4")
    pdf.title_txt = title
    pdf.add_font("DV", "", FONT)
    pdf.add_font("DV", "B", FONTB)
    pdf.add_font("DV", "I", FONTI)
    pdf.add_font("MN", "", MONO)
    pdf.set_margins(18, 22, 18)          # top margin clears the running head
    pdf.set_auto_page_break(True, margin=20)
    pdf.alias_nb_pages()                 # "{nb}" in the footer = total pages
    pdf.set_title("FT-950 Remote — " + title)
    pdf.set_author("CQ-DJ0SH")
    pdf.set_creator("gen_docs.py (fpdf2)")
    return pdf


def cover(pdf, title, subtitle, lang):
    """Title page: an accent band, the wordmark, what the thing is, and when
    this copy was generated. No running head — a cover carries no furniture."""
    pdf.running_head = False
    pdf.add_page()
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 0, 210, 6, style="F")                 # band across the head
    pdf.ln(34)
    pdf.set_font("DV", "B", 32)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 17, "FT-950 Remote", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.5)
    pdf.line(78, pdf.get_y() + 1, 132, pdf.get_y() + 1)
    pdf.ln(7)
    pdf.set_font("DV", "", 15)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 9, title, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DV", "", 11)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 7, subtitle, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    img = os.path.join(HERE, "screenshots", "vfo.png")
    if os.path.exists(img):
        try:
            pdf.image(jpeg(img), x=33, w=144)
        except Exception:
            pass
    pdf.ln(7)
    pdf.set_font("DV", "I", 9.5)
    pdf.set_text_color(*GREY)
    note = ("Yaesu FT-950 web remote via the SCU-17 — CAT, WebRTC voice, FT8/FT4, "
            "CW skimmer — for a Raspberry Pi.") if lang == "en" else \
           ("Web-Fernsteuerung für den Yaesu FT-950 über das SCU-17 — CAT, "
            "WebRTC-Sprache, FT8/FT4, CW-Skimmer — für den Raspberry Pi.")
    pdf.multi_cell(0, 5, note, align="C")
    # foot of the cover: version, date, source
    pdf.set_y(-32)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.2)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("DV", "", 9)
    pdf.set_text_color(*DARK)
    stamp = ("Version %s · %s" % (VERSION, time.strftime("%d.%m.%Y"))) if lang == "de" \
            else ("Version %s · %s" % (VERSION, time.strftime("%Y-%m-%d")))
    pdf.cell(0, 5, stamp, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DV", "", 8)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 4, "github.com/CQ-DJ0SH/ft950-remote", align="C",
             new_x="LMARGIN", new_y="NEXT")


def toc(pdf, outline, lang):
    """Contents, rendered into the placeholder reserved after the cover.

    Chapters in dark, sections indented and grey, page numbers flush right with
    a dotted leader — the reader can find a chapter without paging through."""
    pdf.set_font("DV", "B", 17)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 11, "Inhalt" if lang == "de" else "Contents",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.4)
    y = pdf.get_y() + 1
    pdf.line(18, y, 192, y)
    pdf.ln(6)
    for e in outline:
        chapter = e.level == 0
        pdf.set_font("DV", "B" if chapter else "", 10.5 if chapter else 9.5)
        pdf.set_text_color(*(DARK if chapter else GREY))
        if chapter:
            pdf.ln(1.6)
        x0 = 18 if chapter else 24
        pdf.set_x(x0)
        label = e.name
        num = str(e.page_number)
        wl = pdf.get_string_width(label)
        wn = pdf.get_string_width(num)
        pdf.cell(wl + 1, 5.4, label)
        # dotted leader, drawn rather than typed: dots that always land on the
        # same baseline and never wrap
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        pdf.set_dash_pattern(dash=0.4, gap=1.1)
        ly = pdf.get_y() + 3.6
        pdf.line(x0 + wl + 2, ly, 192 - wn - 2, ly)
        pdf.set_dash_pattern()
        pdf.set_x(192 - wn - 1)
        pdf.cell(wn + 1, 5.4, num, align="R", new_x="LMARGIN", new_y="NEXT")


# ---------------------------------------------------------------------------
# Block diagrams. Drawn with fpdf primitives rather than embedded as an image:
# they stay sharp at any zoom, cost no file, and the labels are searchable text.
# ---------------------------------------------------------------------------
BOX_BG = (240, 244, 247)
BOX_ED = (152, 168, 178)
ACC_BG = (250, 238, 220)
LINE = (96, 112, 122)


def _box(pdf, x, y, w, h, title, sub=(), accent=False):
    pdf.set_line_width(0.3)
    pdf.set_draw_color(*(ACCENT if accent else BOX_ED))
    pdf.set_fill_color(*(ACC_BG if accent else BOX_BG))
    pdf.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=1.2)
    pdf.set_xy(x + 1, y + 1.3)
    pdf.set_font("DV", "B", 7)
    pdf.set_text_color(*(ACCENT if accent else DARK))
    pdf.multi_cell(w - 2, 3.1, title, align="C")
    if sub:
        pdf.set_x(x + 1)
        pdf.set_font("DV", "", 5.8)
        pdf.set_text_color(*GREY)
        pdf.multi_cell(w - 2, 2.6, "\n".join(sub), align="C")


def _frame(pdf, x, y, w, h, label):
    """Dashed enclosure with a caption — one physical machine."""
    pdf.set_dash_pattern(dash=1.2, gap=1.2)
    pdf.set_draw_color(*BOX_ED)
    pdf.set_line_width(0.25)
    pdf.rect(x, y, w, h, style="D", round_corners=True, corner_radius=2)
    pdf.set_dash_pattern()
    pdf.set_xy(x + 2, y + 1)
    pdf.set_font("DV", "B", 6.4)
    pdf.set_text_color(*GREY)
    pdf.cell(w - 4, 3.4, label)


def _arrow(pdf, x1, y1, x2, y2, label="", both=False, above=True):
    import math
    pdf.set_draw_color(*LINE)
    pdf.set_fill_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(x1, y1, x2, y2)
    a = math.atan2(y2 - y1, x2 - x1)
    for ax, ay, ang in (((x2, y2, a),) if not both
                        else ((x2, y2, a), (x1, y1, a + math.pi))):
        pdf.polygon([(ax, ay),
                     (ax - 1.9 * math.cos(ang) + 1.0 * math.sin(ang),
                      ay - 1.9 * math.sin(ang) - 1.0 * math.cos(ang)),
                     (ax - 1.9 * math.cos(ang) - 1.0 * math.sin(ang),
                      ay - 1.9 * math.sin(ang) + 1.0 * math.cos(ang))], style="F")
    if label:
        pdf.set_font("DV", "", 5.6)
        pdf.set_text_color(*GREY)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        pdf.set_xy(mx - 14, my - (3.2 if above else -0.6))
        pdf.cell(28, 2.6, label, align="C")


def _chain(pdf, y, x0, items, w, gap, h, accent=(), labels=()):
    """A left-to-right row of boxes joined by arrows. Returns their x spans."""
    xs = []
    x = x0
    for i, (title, sub) in enumerate(items):
        _box(pdf, x, y, w, h, title, sub, accent=(i in accent))
        xs.append((x, x + w))
        x += w + gap
    for i in range(len(items) - 1):
        lbl = labels[i] if i < len(labels) else ""
        _arrow(pdf, xs[i][1] + 0.5, y + h / 2, xs[i + 1][0] - 0.5, y + h / 2, lbl)
    return xs


def _diagram(pdf, height, fn):
    """Reserve the space, keep it on one page, then draw."""
    if pdf.get_y() + height > pdf.h - 18:
        pdf.add_page()
    pdf.set_auto_page_break(False)
    y0 = pdf.get_y() + 2
    fn(pdf, y0)
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_y(y0 + height)
    pdf.set_text_color(*DARK)


def _cap(pdf, x, y, w, text):
    pdf.set_xy(x, y)
    pdf.set_font("DV", "I", 6.6)
    pdf.set_text_color(*GREY)
    pdf.multi_cell(w, 3, text, align="C")




def draw_system(pdf, lang):
    de = lang == "de"
    def fn(pdf, y):
        _box(pdf, 18, y + 22, 32, 20, "Browser" if not de else "Browser",
             ("PC / Tablet / Phone", "HTTPS · PWA") if not de else ("PC / Tablet / Handy", "HTTPS · PWA"))
        _frame(pdf, 60, y, 82, 64, "Raspberry Pi (.139)  ·  FT-950 Remote")
        _box(pdf, 64, y + 7, 74, 12, "FastAPI backend (uvicorn, HTTPS :8444)",
             ("REST · WebSocket · WebRTC/Opus",), accent=True)
        _box(pdf, 64, y + 23, 35, 16, "CAT driver", ("ft950.py · 38400 Bd", "status poll 0.1 s"))
        _box(pdf, 103, y + 23, 35, 16, "Audio", ("arecord / aplay 48 kHz", "voice · recorder"))
        _box(pdf, 64, y + 43, 35, 16, "jt9 (WSJT-X)", ("FT8/FT4, 3 cores", "sequencer"))
        _box(pdf, 103, y + 43, 35, 16, "CW skimmer", ("STFT · per-signal", "Morse timing"))
        _box(pdf, 152, y + 14, 40, 14, "Yaesu SCU-17", ("CP2105 CAT · PCM2903B",))
        _box(pdf, 152, y + 36, 40, 14, "Yaesu FT-950", ("CAT · DATA jack",), accent=True)
        _arrow(pdf, 50.5, y + 32, 63.5, y + 13, "HTTPS/WSS", both=True)
        _arrow(pdf, 138.5, y + 21, 151.5, y + 21, "USB", both=True)
        _arrow(pdf, 172, y + 28.5, 172, y + 35.5, "", both=True)
        _box(pdf, 18, y + 52, 32, 12, ".155: TM-V71 Remote", ("HackRF One",))
        _arrow(pdf, 50.5, y + 58, 63.5, y + 51, "/ws/hackrf", above=False)
        _box(pdf, 152, y + 54, 40, 10, "Shelly relay", ("13.8 V of the radio" if not de else "13,8 V des Funkgeräts",))
        _arrow(pdf, 142.5, y + 57, 151.5, y + 59, "HTTP")
    _diagram(pdf, 70, fn)


def draw_audio(pdf, lang):
    de = lang == "de"
    def fn(pdf, y):
        pdf.set_font("DV", "B", 7.5)
        pdf.set_text_color(*ACCENT)
        pdf.set_xy(18, y); pdf.cell(0, 4, "RECEIVE" if not de else "EMPFANG")
        xs = _chain(pdf, y + 5, 18, [
            ("FT-950", ("DATA OUT",)), ("SCU-17 codec", ("48 kHz, 16 bit",)),
            ("Capture", ("UTC-anchored ring",))], 34, 10, 14, accent=(0,))
        bx = 140
        for i, (t, sub) in enumerate([("Browser", ("WebRTC/Opus, RX gain",) if not de else ("WebRTC/Opus, RX-Gain",)),
                                      ("jt9 · waterfall", ("DIGI RX level",) if not de else ("DIGI-RX-Pegel",)),
                                      ("CW skimmer", ("all carriers",) if not de else ("alle Träger",)),
                                      ("Recorder", ("WAV 16 kHz",))]):
            yy = y + i * 11
            _box(pdf, bx, yy, 52, 9.5, t, sub)
            _arrow(pdf, xs[-1][1] + 0.5, y + 12, bx - 0.5, yy + 4.7)
        y2 = y + 50
        pdf.set_font("DV", "B", 7.5); pdf.set_text_color(*ACCENT)
        pdf.set_xy(18, y2); pdf.cell(0, 4, "TRANSMIT" if not de else "SENDEN")
        _chain(pdf, y2 + 5, 18, [
            ("Browser mic", ("muted until PTT",) if not de else ("stumm bis PTT",)),
            ("Gain / AGC", ("pre-roll",)), ("Compressor", ("optional",)),
            ("Pre-emphasis", ("+ 3 kHz LP",)), ("Limiter", ("−1 dBFS",)),
            ("SCU-17 → FT-950", ("DATA IN",))], 25, 4.8, 14, accent=(5,))
        _cap(pdf, 18, y2 + 21, 174,
             "FT8/FT4 and TUNE: tones generated on the Pi (ft8code/ft4code) go straight to the codec; "
             "CW is keyed by the radio itself (keyer memory)." if not de else
             "FT8/FT4 und TUNE: Töne vom Pi (ft8code/ft4code) gehen direkt in den Codec; "
             "CW tastet das Funkgerät selbst (Keyer-Speicher).")
    _diagram(pdf, 80, fn)


DIAGRAMS = {"system": draw_system, "audio": draw_audio}


def render(pdf, blocks):
    for kind, *rest in blocks:
        if kind == "h1":
            # both are set BEFORE the page break: header() runs while the new
            # page is created, so it would otherwise print the previous chapter
            pdf.chapter = rest[0]
            pdf.opening = True
            # a chapter starts on its own page — unless the current one is still
            # untouched, which is the case for the page the ToC placeholder left
            if pdf.get_y() > pdf.t_margin + 0.5:
                pdf.add_page()
            pdf.opening = False
            pdf.start_section(rest[0], 0)     # PDF outline + table of contents
            # the chapter number is set in the accent colour and the title in
            # dark, so the eye finds the number when leafing through
            num, _, name = rest[0].partition("  ")
            pdf.set_font("DV", "B", 22)
            pdf.set_text_color(*ACCENT)
            w = pdf.get_string_width(num + " ")
            pdf.cell(w, 11, num + " ")
            pdf.set_text_color(*DARK)
            # left-aligned, and continuation lines hang under the title rather
            # than under the number
            pdf.set_left_margin(18 + w)
            pdf.multi_cell(0, 11, name, align="L")
            pdf.set_left_margin(18)
            pdf.set_draw_color(*ACCENT)
            pdf.set_line_width(0.5)
            y = pdf.get_y() + 1.5
            pdf.line(18, y, 192, y)
            pdf.ln(6)
        elif kind == "h2":
            pdf.ln(3)
            # Keep the heading with what follows. Without this a section title
            # can land at the very bottom of a page — and worse, its accent tick
            # is drawn before the text flows, so the tick stayed behind on the
            # previous page while the heading moved to the next one.
            if pdf.get_y() > pdf.h - pdf.b_margin - 24:
                pdf.add_page()
            pdf.start_section(rest[0], 1)
            # a short accent tick marks the section without shouting
            y = pdf.get_y()
            pdf.set_fill_color(*ACCENT)
            pdf.rect(18, y + 1.4, 1.6, 4.6, style="F")
            pdf.set_x(22)
            pdf.set_font("DV", "B", 12.5)
            pdf.set_text_color(*DARK)
            pdf.multi_cell(0, 7, rest[0], align="L")
            pdf.ln(1.5)
        elif kind == "h3":
            # a sub-heading inside a section: no tick, no outline entry — it
            # marks a turn in the argument, not a place to navigate to
            pdf.ln(2)
            if pdf.get_y() > pdf.h - pdf.b_margin - 20:
                pdf.add_page()
            pdf.set_font("DV", "B", 10.8)
            pdf.set_text_color(*ACCENT)
            pdf.multi_cell(0, 6, rest[0], align="L")
            pdf.ln(0.8)
        elif kind == "p":
            pdf.set_font("DV", "", 10.5)
            pdf.set_text_color(*DARK)
            pdf.multi_cell(0, 5.6, rest[0])
            pdf.ln(1.5)
        elif kind == "ul":
            pdf.set_font("DV", "", 10.5)
            pdf.set_text_color(*DARK)
            for item in rest[0]:
                x = pdf.get_x()
                pdf.set_x(22)
                pdf.set_text_color(*ACCENT)
                pdf.cell(4, 5.4, "•")
                pdf.set_text_color(*DARK)
                pdf.multi_cell(0, 5.4, item)
                pdf.set_x(x)
            pdf.ln(1.5)
        elif kind == "code":
            pdf.set_font("MN", "", 8.7)
            pdf.set_fill_color(*CODEBG)
            pdf.set_text_color(40, 50, 58)
            pdf.multi_cell(0, 4.6, rest[0], fill=True, border=0)
            pdf.ln(2)
        elif kind == "img":
            path = os.path.join(HERE, rest[0])
            if os.path.exists(path):
                try:
                    pdf.image(jpeg(path), w=rest[1] if len(rest) > 1 else 150)
                    pdf.ln(2)
                except Exception:
                    pass
        elif kind == "diagram":
            DIAGRAMS[rest[0]](pdf, rest[1])
        elif kind == "space":
            pdf.ln(rest[0] if rest else 3)


def build(path, title, subtitle, lang, blocks):
    pdf = new_pdf(title)
    cover(pdf, title, subtitle, lang)
    pdf.running_head = True
    pdf.chapter = ""
    pdf.add_page()
    pdf.insert_toc_placeholder(lambda p, outline: toc(p, outline, lang), pages=1)
    render(pdf, blocks)
    pdf.output(path)
    print("wrote", path)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    from manual_en import EN
    from manual_de import DE
    build(os.path.join(HERE, "Manual-EN.pdf"), "User & Technical Manual",
          "Yaesu FT-950 web remote", "en", EN)
    build(os.path.join(HERE, "Handbuch-DE.pdf"), "Benutzer- & Technikhandbuch",
          "Yaesu FT-950 Web-Fernsteuerung", "de", DE)
