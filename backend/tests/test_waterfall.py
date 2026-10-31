"""Waterfall frames only while DECODE is on."""
import asyncio

import numpy as np

from app.waterfall import Waterfall


class _Cap:
    gain_db = 0.0
    def subscribe(self, f): self.f = f


class _Mgr:
    count = 1
    def __init__(self): self.sent = 0
    async def broadcast(self, m): self.sent += 1


def test_no_frames_while_decode_is_off():
    async def run():
        on, cap, mgr = {"v": True}, _Cap(), _Mgr()
        Waterfall(cap, mgr, asyncio.get_running_loop(), active=lambda: on["v"])
        for _ in range(50):
            cap.f(np.zeros(960, dtype=np.int16), 0.0)
        await asyncio.sleep(0.05)
        with_on = mgr.sent
        on["v"] = False
        for _ in range(50):
            cap.f(np.zeros(960, dtype=np.int16), 0.0)
        await asyncio.sleep(0.05)
        return with_on, mgr.sent - with_on
    assert asyncio.run(run()) == (10, 0)
