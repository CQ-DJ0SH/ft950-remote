from app.shelly import ShellySwitch


class _Fake(ShellySwitch):
    def __init__(self, gen):
        super().__init__()
        self.configure("192.0.2.10", 1)
        self.state, self.calls, self._gen = False, [], gen

    def _get(self, path):
        self.calls.append(path)
        if path == "/shelly":
            return {"gen": self._gen} if self._gen >= 2 else {"type": "SHSW-1"}
        if "turn=on" in path or "on=true" in path:
            self.state = True
        if "turn=off" in path or "on=false" in path:
            self.state = False
        return {"output": self.state} if self._gen >= 2 else {"ison": self.state}


def test_gen1_relay_api():
    s = _Fake(1)
    assert s.set(True) is True and "/relay/1?turn=on" in s.calls
    assert s.set(False) is False and s.off_remaining() > 2.5     # min off time starts


def test_gen2_rpc_api():
    s = _Fake(2)
    assert s.set(True) is True and "/rpc/Switch.Set?id=1&on=true" in s.calls
    assert s.status()["on"] is True and s.status()["gen"] == 2


def test_unconfigured():
    st = ShellySwitch().status()
    assert st["available"] is False and st["configured"] is False
