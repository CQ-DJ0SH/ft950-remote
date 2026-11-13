"""Settings ▸ FT8 ▸ COLOURS: what was saved comes back with the settings."""
import asyncio

from app import main


def test_digi_colors_come_back_with_the_settings(monkeypatch):
    monkeypatch.setitem(main._rt, "digi_colors", {"rx_cq": "#ff8800"})
    s = asyncio.run(main.get_settings())
    assert s["digi_colors"] == {"rx_cq": "#ff8800"}
