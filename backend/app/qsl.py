"""QSL card by e-mail: a picture of the station with the QSO data drawn on it,
sent through the operator's own SMTP account (Settings ▸ QSL).

The card is drawn here on the Pi (Pillow), so automatic sending after a log
works without a browser. Layout: the own callsign as a label and one data
block - the QSO row (to radio, date, UTC, band, mode, RST, QSL) and below it
the station lines (OP, QTH, RIG, PWR, ANT) - and the note as a block of its
own. All three sit where the operator
dragged them in the preview, stored as fractions of the picture (x, y of the
top-left corner), so they keep their place on any picture size.

Pictures are uploaded in Settings, normalised once (EXIF rotation, RGB, at
most 2000 px wide) and kept in backend/qsl_images/ (gitignored).
"""
from __future__ import annotations

import html
import io
import logging
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import runtime

log = logging.getLogger("ft950.qsl")

IMAGES = Path(__file__).resolve().parents[1] / "qsl_images"
CARD_W = 1400                       # card width in px (height follows the picture)
BLOCK_ALPHA = 150                   # darkening of the blocks (0-255) over the blurred picture
UPLOAD_MAX = 20 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}\.jpg$")

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu"]
FONT_BOLD = "DejaVuSansMono-Bold.ttf"
FONT_REG = "DejaVuSansMono.ttf"
FONT_LABEL = "DejaVuSans.ttf"
# the own callsign as on the page's nameplate; local copy only (gitignored),
# DejaVu Sans Mono Bold when it is missing
FONT_CALL = Path(__file__).resolve().parents[2] / "frontend" / "fonts" / "Neuropol.woff"

DEFAULTS = {
    # mail account: entered in Settings ▸ QSL, stored only in runtime.json
    "smtp_host": "", "smtp_port": 587, "smtp_security": "starttls",
    "smtp_user": "", "smtp_password": "",
    "from_addr": "", "from_name": "", "bcc_self": False,
    "test_to": "",
    "image": "", "op": "", "qth": "", "rig": "Yaesu FT-950", "pwr": "", "ant": "",
    "qsl_text": "TNX", "note": "TNX QSO 73",
    "call_color": "#f2b13a",        # own callsign on the card and in the mail header (amber)
    "data_color": "#ffffff",        # data block values (labels: the same, fainter)
    "note_color": "#78dcc8",        # the note
    "header_color": "#79726a",      # the mail's title bar (warm steel; gradient and edges follow)
    "pos_call": [0.025, 0.04], "pos_data": [0.025, 0.60], "pos_note": [0.62, 0.86],
    "subject": "QSL card {MY} - {CALL} {DATE} {BAND} {MODE}",
    "body": ("Dear {NAME},\n\n"
             "thank you for our {MODE} QSO on {BAND} on {DATE} at {UTC} UTC.\n"
             "Here is my QSL card to confirm it.\n\n"
             "Vy 73,\n{OP} {MY}"),
    "auto": False,
}
SECRET = "smtp_password"


class QslError(Exception):
    pass


# the operator's QSL note on QRZ.com saying no card is wanted (or only
# another way): NO QSL, NO EQSL, NO CARDS, QSL: NO, LOTW ONLY, ONLY LOTW,
# QRZ (LOGBOOK) ONLY, OQRS/CLUBLOG ONLY, NO E-MAIL
_DECLINE = re.compile(
    r"\bNO\s*(?:E[-\s]?)?QSLS?\b|\bNO\s+(?:E[-\s]?)?CARDS?\b|\bNO\s+E[-\s]?MAILS?\b|\bQSLS?\s*[:=]?\s*NO\b"
    r"|\b(?:LOTW|QRZ(?:\s+LOG(?:BOOK)?)?|OQRS|CLUBLOG|CLUB\s*LOG)\s+ONLY\b"
    r"|\bONLY\s+(?:VIA\s+)?(?:LOTW|QRZ|OQRS|CLUBLOG|CLUB\s*LOG)\b")


def qsl_hint(info: dict) -> dict:
    """What QRZ.com says about QSLs: a short line for the operator and, if the
    station does not want a card by e-mail, the reason (else "")."""
    yn = {"1": "yes", "0": "no", "Y": "yes", "N": "no"}
    parts = [f"{k} {yn[str(info.get(f)).strip().upper()]}" for k, f in
             (("eQSL", "eqsl"), ("LoTW", "lotw"), ("paper", "mqsl"))
             if str(info.get(f) or "").strip().upper() in yn]
    note = " ".join((info.get("qslmgr") or "").split())
    if note:
        parts.append(f"\u201c{note}\u201d")
    reason = ""
    if note and _DECLINE.search(note.upper()):
        reason = f"QSL note: {note}"
    elif str(info.get("eqsl") or "").strip().upper() in ("0", "N"):
        reason = "eQSL: no"
    return {"hint": " · ".join(parts), "declined": reason}


# -- config ------------------------------------------------------------------
def load_cfg() -> dict:
    saved = runtime.load().get("qsl") or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def public_cfg() -> dict:
    """For the browser: everything but the password (only whether it is set)."""
    c = load_cfg()
    pw = bool(c.pop(SECRET))
    return {**c, "password_set": pw, "images": list_images()}


def save_cfg(update: dict) -> dict:
    cur = load_cfg()
    for k, v in update.items():
        if k not in DEFAULTS or v is None:
            continue
        if k == SECRET and v == "":
            continue                        # empty field keeps the saved password
        cur[k] = v
    runtime.save({"qsl": cur})
    return public_cfg()


# -- pictures ------------------------------------------------------------------
def list_images() -> list[str]:
    if not IMAGES.is_dir():
        return []
    return sorted(p.name for p in IMAGES.glob("*.jpg"))


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "picture").stem)[:60].strip("._") or "picture"
    return f"{stem}.jpg"


def image_path(name: str) -> Path:
    if not _NAME.match(name or ""):
        raise QslError("invalid picture name")
    p = IMAGES / name
    if not p.is_file():
        raise QslError(f"picture {name} not found")
    return p


def add_image(name: str, data: bytes) -> str:
    """Store an uploaded picture: EXIF-rotated, RGB, at most 2000 px wide, JPEG."""
    if len(data) > UPLOAD_MAX:
        raise QslError("picture larger than 20 MB")
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decoder error
        raise QslError(f"not a readable picture: {exc}") from exc
    if im.width > 2000:
        im = im.resize((2000, round(im.height * 2000 / im.width)), Image.LANCZOS)
    IMAGES.mkdir(exist_ok=True)
    out = _safe_name(name)
    im.save(IMAGES / out, "JPEG", quality=90)
    return out


def delete_image(name: str) -> None:
    image_path(name).unlink()


def thumbnail(name: str, width: int = 240) -> bytes:
    im = Image.open(image_path(name))
    im.thumbnail((width, width * 2))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


# -- the card ------------------------------------------------------------------
def _font(name, size: int) -> ImageFont.FreeTypeFont:
    if isinstance(name, Path):
        if name.is_file():
            return ImageFont.truetype(str(name), size)
        name = FONT_BOLD
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def qso_fields(q: dict) -> dict:
    """Card and mail values of one QSO (a recent-log entry or a test QSO)."""
    date, t = q.get("qso_date") or "", q.get("time_on") or ""
    if not date and q.get("ts"):
        ts = datetime.fromisoformat(q["ts"]).astimezone(timezone.utc)
        date, t = ts.strftime("%Y%m%d"), ts.strftime("%H%M%S")
    return {
        "CALL": (q.get("call") or "").upper(),
        "DATE": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date,
        "UTC": f"{t[:2]}:{t[2:4]}" if len(t) >= 4 else t,
        "BAND": q.get("band") or "", "MODE": (q.get("mode") or "").upper(),
        "FREQ": q.get("freq_mhz") or "",
        "RST": q.get("rst_sent") or "", "NAME": (q.get("name") or "").strip(),
    }


def _layout(cfg: dict, q: dict, my_call: str, w: int):
    """Sizes and texts of both blocks for a card ``w`` px wide."""
    s = w / 1400
    f = qso_fields(q)
    rows = [[("TO RADIO", f["CALL"]), ("DATE", f["DATE"]), ("UTC", f["UTC"]),
             ("BAND", f["BAND"]), ("MODE", f["MODE"]), ("RST", f["RST"]),
             ("QSL", cfg.get("qsl_text") or "")]]
    lines = [(k, cfg.get(k.lower()) or "") for k in ("OP", "QTH", "RIG", "PWR", "ANT")]
    lines = [(k, v) for k, v in lines if v]
    fl, fv, fs = _font(FONT_LABEL, round(17 * s)), _font(FONT_BOLD, round(30 * s)), _font(FONT_REG, round(27 * s))
    pad, gap = round(22 * s), round(30 * s)
    ncol = max(len(r) for r in rows)
    # the other station's call: at most as wide as 10 characters (DJ0ABC/QRP);
    # a longer one (VP2V/DJ0ABC/QRP) is drawn smaller, so the block does not
    # grow into the callsign label or the note
    call_max = fv.getlength("W" * 10)
    call = rows[0][0][1]
    fcall = fv
    if fv.getlength(call) > call_max:
        fcall = _font(FONT_BOLD, max(8, int(fv.size * call_max / fv.getlength(call))))
    col_w = [max((max(fl.getlength(r[i][0]), (fcall if (r is rows[0] and i == 0) else fv).getlength(r[i][1]))
                  for r in rows if i < len(r)), default=0) for i in range(ncol)]
    row_w = sum(col_w) + gap * (ncol - 1)
    row_h, row_gap = round(62 * s), round(14 * s)
    # station lines side by side: as many columns as fit under the QSO row,
    # at least two (the block grows for them if it must)
    sgap, lab_pad = round(40 * s), round(12 * s)

    def grid(n):
        g = [lines[i:i + n] for i in range(0, len(lines), n)]
        lw = [max((fl.getlength(r[i][0]) for r in g if i < len(r)), default=0) + lab_pad for i in range(n)]
        vw = [max((fs.getlength(r[i][1]) for r in g if i < len(r)), default=0) for i in range(n)]
        return g, lw, vw, sum(lw) + sum(vw) + sgap * (n - 1)
    n = min(3, max(1, len(lines)))
    while n > 2 and grid(n)[3] > row_w:
        n -= 1
    sgrid, lab_ws, val_ws, lines_w = grid(n) if lines else ([], [], [], 0)
    # column starts at the 1/n marks of the width, shifted only as far as
    # needed so no column overlaps the next or runs past the right edge
    col_x = []
    if lines:
        total = max(row_w, lines_w)
        cw = [lw + vw for lw, vw in zip(lab_ws, val_ws)]
        mgap = round(24 * s)
        col_x = [i * total / n for i in range(n)]
        col_x[-1] = min(col_x[-1], total - cw[-1])
        for i in range(n - 2, -1, -1):
            col_x[i] = min(col_x[i], col_x[i + 1] - mgap - cw[i])
        col_x[0] = 0
        for i in range(1, n):
            col_x[i] = max(col_x[i], col_x[i - 1] + cw[i - 1] + mgap)
        lines_w = max(total, col_x[-1] + cw[-1])
    bw = round(max(row_w, lines_w) + 2 * pad)
    lh = round(38 * s)
    bh = round(pad + len(rows) * row_h + (len(rows) - 1) * row_gap
               + (round(18 * s) + lh * len(sgrid) if lines else 0) + pad)
    # the note: its own block (placeholders as in the mail, e.g. TNX {CALL} 73)
    note = _fill((cfg.get("note") or "").strip(), f, cfg, my_call)
    fn = _font(FONT_REG, round(34 * s))
    n_pad = round(18 * s)
    nl, nt, nr, nb = fn.getbbox(note or " ", anchor="ls")
    note_size = (round(nr - nl + 2 * n_pad), round(nb - nt + 2 * n_pad * 0.8)) if note else (0, 0)
    fc = _font(FONT_CALL, round(78 * s))
    ch_pad = round(22 * s)
    label = _call_label(my_call, fc)
    l, t, r, b = fc.getbbox(label or " ", anchor="ls")
    cw = round(r - l + 2 * ch_pad)
    chh = round(b - t + 2 * ch_pad * 0.8)
    return dict(s=s, rows=rows, col_w=col_w, gap=gap, fcall=fcall, row_h=row_h, row_gap=row_gap, lines=lines, note=note,
                fn=fn, n_pad=n_pad, note_box=(nl, nt, nr, nb), note_size=note_size,
                sgrid=sgrid, lab_ws=lab_ws, val_ws=val_ws, sgap=sgap, col_x=col_x,
                pad=pad, lh=lh, fl=fl, fv=fv, fs=fs, fc=fc, data=(bw, bh), call=(cw, chh),
                ch_pad=ch_pad, call_box=(l, t, r, b), label=label)


def _rgb(color, default=(242, 177, 58)) -> tuple[int, int, int]:
    """#rrggbb -> (r, g, b); anything else: the default amber."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (color or "").strip())
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else default


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _shade(rgb, f: float) -> str:
    """f > 0: towards white by f, f < 0: towards black by -f."""
    return _hex([c + (255 - c) * f if f > 0 else c * (1 + f) for c in rgb])


def _call_label(call: str, font) -> str:
    """Neuropol draws 0 like O: a slashed zero as on the page's nameplate
    (DJ0SH -> DJØSH), if the font has the glyph."""
    call = (call or "").upper()
    try:
        has = font.getmask("Ø").getbbox() is not None and font.getlength("Ø") != font.getlength("\uffff")
    except Exception:  # noqa: BLE001
        has = False
    return call.replace("0", "Ø") if has else call


def _place(pos, size, w, h):
    """Top-left in px from a stored fraction, kept inside the picture."""
    x = round(float(pos[0]) * w) if pos else 0
    y = round(float(pos[1]) * h) if pos else 0
    return max(0, min(x, w - size[0])), max(0, min(y, h - size[1]))


def render(cfg: dict, q: dict, my_call: str, image: Optional[str] = None,
           width: int = CARD_W) -> tuple[bytes, dict]:
    """The card as JPEG, and both blocks as fractions (for the drag boxes)."""
    name = image if image is not None else cfg.get("image")
    if name:
        bg = Image.open(image_path(name)).convert("RGB")
    else:                                   # no picture yet: a plain dark card
        bg = Image.new("RGB", (1400, 900), (24, 32, 40))
    h = round(bg.height * width / bg.width)
    card = bg.resize((width, h), Image.LANCZOS).convert("RGBA")
    L = _layout(cfg, q, my_call.upper(), width)
    r = round(14 * L["s"])
    cx, cy = _place(cfg.get("pos_call"), L["call"], width, h)
    x, y = _place(cfg.get("pos_data"), L["data"], width, h)
    nx, ny = _place(cfg.get("pos_note"), L["note_size"], width, h)
    blocks = [((cx, cy), L["call"]), ((x, y), L["data"])] + ([((nx, ny), L["note_size"])] if L["note"] else [])
    # frosted glass: the picture blurred behind the blocks, so its details
    # do not compete with the text
    for (bx, by), (bw_, bh_) in blocks:
        reg = card.crop((bx, by, bx + bw_, by + bh_)).filter(ImageFilter.GaussianBlur(round(14 * L["s"])))
        mask = Image.new("L", (bw_, bh_), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw_ - 1, bh_ - 1), r, fill=255)
        card.paste(reg, (bx, by), mask)
    over = Image.new("RGBA", card.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    # callsign label
    d.rounded_rectangle((cx, cy, cx + L["call"][0], cy + L["call"][1]), r, fill=(10, 14, 18, BLOCK_ALPHA))
    l, t, _r, b = L["call_box"]                 # the glyphs' own box: centred whatever the font
    d.text((cx + L["ch_pad"] - l, cy + (L["call"][1] - (b - t)) / 2 - t), L["label"],
           font=L["fc"], fill=(*_rgb(cfg.get("call_color")), 255), anchor="ls")
    # data block
    bw, bh = L["data"]
    d.rounded_rectangle((x, y, x + bw, y + bh), r, fill=(10, 14, 18, BLOCK_ALPHA))
    px, py = x + L["pad"], y + L["pad"]
    dc = _rgb(cfg.get("data_color"), (255, 255, 255))
    dim, white = (*dc, 165), (*dc, 255)          # labels: the value colour, fainter
    for row in L["rows"]:
        px = x + L["pad"]
        for (lab, val), cw_ in zip(row, L["col_w"]):
            d.text((px, py), lab, font=L["fl"], fill=dim)
            f = L["fcall"] if (row is L["rows"][0] and lab == "TO RADIO") else L["fv"]
            d.text((px, py + round(24 * L["s"]) + (L["fv"].size - f.size) * 0.8), val, font=f, fill=white)
            px += cw_ + L["gap"]
        py += L["row_h"] + L["row_gap"]
    ly = py - L["row_gap"]
    if L["lines"]:
        ly += round(18 * L["s"])
        d.line((x + L["pad"], ly - round(9 * L["s"]), x + bw - L["pad"], ly - round(9 * L["s"])),
               fill=(*dc, 60), width=max(1, round(L["s"])))
        for row in L["sgrid"]:
            for (k, v), lw, cx0 in zip(row, L["lab_ws"], L["col_x"]):
                px = x + L["pad"] + cx0
                d.text((px, ly + L["lh"] / 2), k, font=L["fl"], fill=dim, anchor="lm")
                d.text((px + lw, ly + L["lh"] / 2), v, font=L["fs"], fill=white, anchor="lm")
            ly += L["lh"]
    # note block
    if L["note"]:
        nw, nh = L["note_size"]
        d.rounded_rectangle((nx, ny, nx + nw, ny + nh), r, fill=(10, 14, 18, BLOCK_ALPHA))
        l, t, _r, b = L["note_box"]
        d.text((nx + L["n_pad"] - l, ny + (nh - (b - t)) / 2 - t), L["note"], font=L["fn"],
               fill=(*_rgb(cfg.get("note_color"), (120, 220, 200)), 255), anchor="ls")
    out = Image.alpha_composite(card, over).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=88)
    boxes = {"call": [cx / width, cy / h, L["call"][0] / width, L["call"][1] / h],
             "data": [x / width, y / h, bw / width, bh / h],
             "note": [nx / width, ny / h, L["note_size"][0] / width, L["note_size"][1] / h] if L["note"] else None,
             "w": width, "h": h}
    return buf.getvalue(), boxes


# -- the mail ------------------------------------------------------------------
TITLE = "FT-950 REMOTE"
REPO = "https://github.com/CQ-DJ0SH/ft950-remote"
# the page's title plate (screws, Neuropol lettering), captured from the page
# at 2x with a transparent edge (/root/ft950-uitools/plateshot.py); local
# like the font itself - without it the lettering alone is drawn
PLATE = Path(__file__).resolve().parent / "assets" / "mail_plate.png"


def header_png() -> tuple[bytes, int, int]:
    """The mail header's picture and its display size: the title plate, or
    the name in Neuropol when the plate file is missing."""
    if PLATE.is_file():
        data = PLATE.read_bytes()
        w, h = Image.open(io.BytesIO(data)).size
        return data, round(w / 2), round(h / 2)  # 2x picture, shown at its page size
    return title_png()


def title_png(text: str = TITLE, height: int = 22) -> tuple[bytes, int, int]:
    """The mail header's name in Neuropol as a picture (mail programs do not
    load the font), drawn at twice the size for sharp screens. Returns the
    PNG and its display size."""
    f = _font(FONT_CALL, height * 2)
    l, t, r, b = f.getbbox(text, anchor="ls")
    im = Image.new("RGBA", (int(r - l) + 4, int(b - t) + 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((2 - l, 2 - t), text, font=f, fill=(150, 225, 235, 255), anchor="ls")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), round(im.width / 2), round(im.height / 2)
def _fill(tpl: str, f: dict, cfg: dict, my_call: str) -> str:
    vals = {**f, "MY": my_call.upper(), "OP": cfg.get("op") or "",
            "NAME": f["NAME"].split()[0].title() if f["NAME"] else f["CALL"]}
    return re.sub(r"\{([A-Z]+)\}", lambda m: str(vals.get(m.group(1), m.group(0))), tpl)


def build_mail(cfg: dict, q: dict, my_call: str, to: str, card: bytes) -> EmailMessage:
    f = qso_fields(q)
    subject = _fill(cfg.get("subject") or DEFAULTS["subject"], f, cfg, my_call)
    text = _fill(cfg.get("body") or DEFAULTS["body"], f, cfg, my_call)
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = formataddr((cfg.get("from_name") or my_call.upper(), cfg["from_addr"]))
    m["To"] = to
    if cfg.get("bcc_self"):
        m["Bcc"] = cfg["from_addr"]
    m["Date"] = formatdate(localtime=True)       # RFC 5322: exactly one Date (DMARC checks it)
    m["Message-ID"] = make_msgid(domain=cfg["from_addr"].split("@")[-1])
    m.set_content(text + f"\n\n(QSL card attached: qsl-{my_call.upper()}-to-{f['CALL']}.jpg)\n"
                  f"\n--\nSent with FT-950 Remote: {REPO}\n")
    cid = make_msgid(domain="qsl.card")
    tcid = make_msgid(domain="qsl.card")
    tpng, tw, th = header_png()
    # the own call with a slashed zero as on the card (HTML only - the
    # plain-text part keeps 0, so the call can be copied as it is)
    me, me_o = html.escape(my_call.upper()), html.escape(my_call.upper().replace("0", "Ø"))
    paras = "".join(f"<p style=\"margin:0 0 14px\">"
                    f"{re.sub(rf'\b{re.escape(me)}\b', me_o, html.escape(p)).replace(chr(10), '<br>')}</p>"
                    for p in text.split("\n\n"))
    # header: the own call written as on the card's label (slashed zero), both
    # calls in the amber of the radio's lamps
    amber = "#%02x%02x%02x" % _rgb(cfg.get("call_color"))
    hdr_me = me_o
    # title bar: one colour (Settings ▸ QSL), lighter at the top, darker below
    hc = _rgb(cfg.get("header_color"), (121, 114, 106))
    hb, h_top, h_bot = _hex(hc), _shade(hc, 0.25), _shade(hc, -0.17)
    h_edge_t, h_edge_b = _shade(hc, 0.45), _shade(hc, -0.43)
    m.add_alternative(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#ece8e3;font-family:Arial,Helvetica,sans-serif;color:#231f1b">
<div style="max-width:640px;margin:0 auto;background:#f7f5f2">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{hb}"
    style="background:{hb};background-image:linear-gradient(180deg,{h_top} 0%,{hb} 45%,{h_bot} 100%);
    border-top:1px solid {h_edge_t};border-bottom:1px solid {h_edge_b}">
    <tr><td align="center" style="padding:20px 16px 8px"><a href="{REPO}" target="_blank" style="text-decoration:none">
      <img src="cid:{tcid[1:-1]}" alt="{TITLE}" width="{tw}"
      style="display:block;margin:0 auto;border:0;width:100%;max-width:{tw}px;height:auto"></a></td></tr>
    <tr><td align="center" style="padding:0 24px 16px;color:#ffffff;font-size:18px;font-weight:bold;letter-spacing:.06em;
      text-shadow:0 1px 2px rgba(0,0,0,.45)">
      QSL <span style="color:{amber}">{hdr_me}</span> &rarr; <span style="color:{amber}">{html.escape(f['CALL'])}</span></td></tr>
  </table>
  <div style="padding:22px 24px;font-size:15px;line-height:1.55">{paras}
    <img src="cid:{cid[1:-1]}" alt="QSL card {html.escape(my_call.upper())}" width="592"
      style="display:block;width:100%;max-width:592px;height:auto;margin:18px 0 4px;border-radius:8px;border:1px solid #ddd5ca">
  </div>
  <div style="padding:12px 24px;border-top:1px solid #e6e0d8;font-size:11px;color:#9a9188;text-align:center">
    Sent with <a href="{REPO}" target="_blank" style="color:#7d6a52;font-weight:bold;text-decoration:none">FT-950 Remote</a>
    &middot; <a href="{REPO}" target="_blank" style="color:#9a7a4a;text-decoration:none">github.com/CQ-DJ0SH/ft950-remote</a></div>
</div></body></html>""", subtype="html")
    m.get_payload()[1].add_related(tpng, maintype="image", subtype="png", cid=tcid,
                                   filename="ft950-remote.png", disposition="inline")
    m.get_payload()[1].add_related(card, maintype="image", subtype="jpeg", cid=cid,
                                   filename=f"qsl-{my_call.upper()}-to-{f['CALL']}.jpg", disposition="inline")
    return m


def _connect(cfg: dict, timeout: float) -> smtplib.SMTP:
    """Connected, encrypted and (with a username) logged in - or QslError.
    Without a username the mail goes in unauthenticated: only for a server
    that relays for the Pi by its address (postfix mynetworks). Its DKIM
    signer and SPF must then also know that address, or DMARC for the own
    domain rejects the mail."""
    host, port = cfg.get("smtp_host"), int(cfg.get("smtp_port") or 587)
    if not host or not cfg.get("from_addr"):
        raise QslError("mail server or sender missing (Settings ▸ QSL)")
    user = cfg.get("smtp_user") or ""
    if user and not cfg.get("smtp_password"):
        raise QslError("password missing for the SMTP login (Settings ▸ QSL)")
    ctx = ssl.create_default_context()
    s = (smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) if cfg.get("smtp_security") == "ssl"
         else smtplib.SMTP(host, port, timeout=timeout))
    try:
        if cfg.get("smtp_security") == "starttls":
            s.starttls(context=ctx)
        s.ehlo_or_helo_if_needed()
        if user:
            if not s.has_extn("auth"):
                raise QslError(f"{host}:{port} offers no login — use the submission port "
                               "(587 STARTTLS or 465 SSL/TLS), or leave the username empty "
                               "if the server relays for the Pi by its address")
            s.login(user, cfg["smtp_password"])
    except BaseException:
        s.close()
        raise
    return s


def smtp_send(cfg: dict, msg: EmailMessage) -> None:
    try:
        with _connect(cfg, 20) as s:
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise QslError(f"mail not sent: {exc}") from exc


def smtp_test(cfg: dict) -> dict:
    """Connect and log in only - no mail is sent."""
    host, port = cfg.get("smtp_host"), int(cfg.get("smtp_port") or 587)
    t = time.monotonic()
    try:
        with _connect(cfg, 15):
            pass
        how = "logged in" if cfg.get("smtp_user") else "connected (no login: relay by address)"
        return {"ok": True, "message": f"{how} at {host}:{port} ({(time.monotonic() - t) * 1000:.0f} ms)"}
    except QslError as exc:
        return {"ok": False, "message": str(exc)}
    except (smtplib.SMTPException, OSError) as exc:
        return {"ok": False, "message": str(exc) or exc.__class__.__name__}


TEST_QSO = {"call": "DL1ABC", "qso_date": None, "time_on": None, "band": "20m",
            "mode": "FT8", "rst_sent": "-07", "name": "Test Station", "freq_mhz": "14.074000"}


def test_qso() -> dict:
    now = datetime.now(timezone.utc)
    return {**TEST_QSO, "qso_date": now.strftime("%Y%m%d"), "time_on": now.strftime("%H%M%S")}
