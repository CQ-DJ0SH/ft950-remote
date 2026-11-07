"""QSL card: settings (temp runtime file), pictures, drawing, the mail - with a
fake SMTP server, nothing is sent."""
import io
import json

import pytest
from PIL import Image

from app import qsl, runtime

Q = {"call": "M6FPW", "qso_date": "20261106", "time_on": "083100", "band": "20m",
     "mode": "FT8", "rst_sent": "-01", "name": "Steven Bale"}


ACCOUNT = {"smtp_host": "smtp.example.org", "smtp_user": "me@example.org",
           "from_addr": "me@example.org", "test_to": "me@example.org"}


@pytest.fixture(autouse=True)
def tmp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(qsl, "IMAGES", tmp_path / "img")


def _jpeg(w=1200, h=800, color=(90, 120, 150)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def test_password_never_leaves_and_empty_keeps_it():
    qsl.save_cfg({"smtp_password": "geheim", "op": "Oliver"})
    pub = qsl.public_cfg()
    assert "smtp_password" not in pub and pub["password_set"] and pub["op"] == "Oliver"
    qsl.save_cfg({"smtp_password": "", "op": "Olli"})          # empty field: keep
    assert qsl.load_cfg()["smtp_password"] == "geheim"
    assert json.loads(runtime.FILE.read_text())["qsl"]["op"] == "Olli"


def test_no_account_data_in_the_defaults():
    c = qsl.load_cfg()
    assert c["smtp_host"] == c["smtp_user"] == c["smtp_password"] == c["from_addr"] == c["test_to"] == ""


def test_picture_upload_is_normalised_and_safe():
    name = qsl.add_image("../../etc/My Shack!.jpeg", _jpeg(3000, 2000))
    assert name == "My_Shack.jpg" and qsl.list_images() == [name]
    assert Image.open(qsl.image_path(name)).width == 2000
    with pytest.raises(qsl.QslError):
        qsl.image_path("../runtime.json")
    with pytest.raises(qsl.QslError):
        qsl.add_image("x.jpg", b"not a picture")
    qsl.delete_image(name)
    assert qsl.list_images() == []


def test_card_blocks_follow_the_stored_position_and_stay_inside():
    name = qsl.add_image("bg.jpg", _jpeg())
    cfg = {**qsl.DEFAULTS, "image": name, "op": "Oliver", "pos_data": [0.2, 0.1], "pos_call": [0.99, 0.99]}
    jpg, b = qsl.render(cfg, Q, "DJ0SH", width=1400)
    im = Image.open(io.BytesIO(jpg))
    assert im.size == (1400, round(800 * 1400 / 1200))
    assert b["data"][:2] == [pytest.approx(0.2, abs=0.002), pytest.approx(0.1, abs=0.002)]
    x, y, w, h = b["call"]                         # pushed back inside the picture
    assert x + w <= 1.0001 and y + h <= 1.0001


def test_card_without_picture():
    jpg, _ = qsl.render(qsl.DEFAULTS, Q, "DJ0SH")
    assert Image.open(io.BytesIO(jpg)).format == "JPEG"


def test_mail_html_with_inline_card_and_text():
    cfg = {**qsl.DEFAULTS, **ACCOUNT, "op": "Oliver", "bcc_self": True}
    m = qsl.build_mail(cfg, Q, "DJ0SH", "m6fpw@example.org", b"\xff\xd8jpeg")
    assert m["To"] == "m6fpw@example.org" and m["Bcc"] == "me@example.org"
    assert len(m.get_all("Date")) == 1 and len(m.get_all("Message-ID")) == 1
    assert m["Subject"] == "QSL card DJ0SH - M6FPW 2026-11-06 20m FT8"
    plain = m.get_body(("plain",)).get_content()
    assert plain.startswith("Dear Steven,") and "08:31 UTC" in plain and "Oliver DJ0SH" in plain
    html = m.get_body(("html",)).get_content()
    img = [p for p in m.walk() if p.get_content_type() == "image/jpeg"][0]
    png = [p for p in m.walk() if p.get_content_type() == "image/png"][0]   # FT-950 REMOTE in Neuropol
    assert f'cid:{png["Content-ID"][1:-1]}' in html and 'alt="FT-950 REMOTE"' in html
    assert 'href="https://github.com/CQ-DJ0SH/ft950-remote"' in html
    assert "https://github.com/CQ-DJ0SH/ft950-remote" in plain
    assert f'cid:{img["Content-ID"][1:-1]}' in html
    assert img.get_filename() == "qsl-DJ0SH-to-M6FPW.jpg"


def test_smtp_starttls_login_send(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): calls.append(("quit",))
        def starttls(self, context=None): calls.append(("starttls",))
        def ehlo_or_helo_if_needed(self): pass
        def has_extn(self, x): return x == "auth"
        def close(self): pass
        def login(self, u, p): calls.append(("login", u, p))
        def send_message(self, m): calls.append(("send", m["To"]))

    monkeypatch.setattr(qsl.smtplib, "SMTP", FakeSMTP)
    cfg = {**qsl.DEFAULTS, **ACCOUNT, "smtp_password": "pw"}
    qsl.smtp_send(cfg, qsl.build_mail(cfg, Q, "DJ0SH", "a@b.de", b"x"))
    assert calls == [("connect", "smtp.example.org", 587), ("starttls",),
                     ("login", "me@example.org", "pw"), ("send", "a@b.de"), ("quit",)]


def test_smtp_error_becomes_qsl_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(qsl.smtplib, "SMTP", boom)
    with pytest.raises(qsl.QslError, match="connection refused"):
        qsl.smtp_send({**qsl.DEFAULTS, **ACCOUNT, "smtp_password": "pw"}, qsl.build_mail({**qsl.DEFAULTS, **ACCOUNT}, Q, "DJ0SH", "a@b.de", b"x"))


def test_fields_from_a_recent_entry_timestamp():
    f = qsl.qso_fields({"call": "k1abc", "ts": "2026-11-07T12:34:56+00:00", "band": "40m", "mode": "cw"})
    assert (f["CALL"], f["DATE"], f["UTC"], f["MODE"]) == ("K1ABC", "2026-11-07", "12:34", "CW")


def test_without_username_no_login(monkeypatch):
    calls = []

    class Relay:
        def __init__(self, *a, **k): pass
        def starttls(self, context=None): calls.append("starttls")
        def ehlo_or_helo_if_needed(self): pass
        def has_extn(self, x): return False
        def login(self, *a): pytest.fail("no login without a username")
        def send_message(self, m): calls.append("send")
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(qsl.smtplib, "SMTP", Relay)
    cfg = {**qsl.DEFAULTS, **ACCOUNT, "smtp_user": "", "smtp_port": 25}
    qsl.smtp_send(cfg, qsl.build_mail(cfg, Q, "DJ0SH", "a@b.de", b"x"))
    assert calls == ["starttls", "send"]
    with pytest.raises(qsl.QslError, match="password missing"):
        qsl.smtp_send({**qsl.DEFAULTS, **ACCOUNT, "smtp_password": ""}, qsl.build_mail(cfg, Q, "DJ0SH", "a@b.de", b"x"))


def test_port_without_auth_is_refused(monkeypatch):
    class NoAuth:
        def __init__(self, *a, **k): pass
        def starttls(self, context=None): pass
        def ehlo_or_helo_if_needed(self): pass
        def has_extn(self, x): return False
        def close(self): pass
        def login(self, *a): pytest.fail("no login offered")
    monkeypatch.setattr(qsl.smtplib, "SMTP", NoAuth)
    r = qsl.smtp_test({**qsl.DEFAULTS, **ACCOUNT, "smtp_port": 25, "smtp_password": "pw"})
    assert not r["ok"] and "submission port" in r["message"]


def test_note_is_its_own_block_with_placeholders():
    cfg = {**qsl.DEFAULTS, "note": "TNX {CALL} 73", "pos_note": [0.1, 0.2]}
    _, b = qsl.render(cfg, Q, "DJ0SH", width=1400)
    assert b["note"][:2] == [pytest.approx(0.1, abs=0.002), pytest.approx(0.2, abs=0.002)]
    _, b2 = qsl.render(cfg, Q, "DJ0SH", width=1400)
    assert b2["note"][2] > 0.1                               # "TNX M6FPW 73" is wide
    _, b3 = qsl.render({**cfg, "note": ""}, Q, "DJ0SH")
    assert b3["note"] is None


@pytest.mark.parametrize("note,declined", [
    ("NO QSL", True), ("no eqsl please", True), ("No cards, LoTW only", True), ("QSL: NO", True),
    ("LOTW ONLY", True), ("QRZ logbook only", True), ("only via LoTW", True), ("OQRS only", True),
    ("NO E-MAIL QSL", True), ("via EB7DX", False), ("Direct or bureau", False), ("NO BURO", False),
    ("", False), ("QSL via LoTW and eQSL", False)])
def test_qsl_note_decline(note, declined):
    assert bool(qsl.qsl_hint({"qslmgr": note})["declined"]) is declined


def test_qsl_hint_flags():
    h = qsl.qsl_hint({"eqsl": "1", "lotw": "1", "mqsl": "0", "qslmgr": "via EB7DX"})
    assert h == {"hint": "eQSL yes · LoTW yes · paper no · “via EB7DX”", "declined": ""}
    assert qsl.qsl_hint({"eqsl": "0"})["declined"] == "eQSL: no"
