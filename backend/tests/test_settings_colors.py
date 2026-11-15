"""Settings ▸ FT8 ▸ COLOURS: what was saved comes back with the settings."""
import asyncio

from app import main


def test_digi_colors_come_back_with_the_settings(monkeypatch):
    monkeypatch.setitem(main._rt, "digi_colors", {"rx_cq": "#ff8800"})
    s = asyncio.run(main.get_settings())
    assert s["digi_colors"] == {"rx_cq": "#ff8800"}


def test_ui_prefs_kept_on_the_pi(monkeypatch, tmp_path):
    """View settings (palette, callsigns …) survive a browser cache clear:
    merged into runtime.json - here a throw-away file, never the live one."""
    from app import runtime
    from app.models import UiPrefsReq
    monkeypatch.setattr(runtime, "FILE", tmp_path / "runtime.json")
    asyncio.run(main.ui_prefs_save(UiPrefsReq(prefs={"cmapDefault": "Hologram", "evil": 1})))
    asyncio.run(main.ui_prefs_save(UiPrefsReq(prefs={"wfCallsAll": True})))
    got = asyncio.run(main.ui_prefs())["prefs"]
    assert got == {"cmapDefault": "Hologram", "wfCallsAll": True}         # merged, unknown key dropped
