"""Runtime configuration, overridable via environment variables (FT950_*)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "1.3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FT950_", env_file=".env")

    # Serial link to the radio: the SCU-17's CP2105 "Enhanced" port (if00).
    # The by-id path survives re-plugging; ttyUSB0/1 can swap.
    serial_port: str = ("/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_"
                        "to_UART_Bridge_Controller_01CD8BBB-if00-port0")
    serial_baud: int = 38400         # radio menu 026 = 3
    serial_timeout: float = 0.5

    # HTTPS server (LAN only). 8444, so it can run beside TM-V71 Remote (8443).
    host: str = "0.0.0.0"
    port: int = 8444

    # Two-tier polling (see FT950.read_fast / read_slow): ~25 ms per fast
    # pass, ~140 ms per slow pass, both measured on the radio.
    fast_interval: float = 0.1       # frequency, mode, S-meter, busy, TX
    slow_interval: float = 1.0       # filters, AGC, preamp, power …
    reconnect_interval: float = 2.0  # back-off while the radio is off / gone

    # Audio: the SCU-17's PCM2903B codec, addressed by card name (the card
    # index can change between boots). RX level is set on the radio only
    # (menu 051) — the codec has no capture control at all.
    audio_device: str = "hw:CARD=CODEC,DEV=0"

    # FT8/FT4 decoding (jt9). work_dir holds the 12 kHz slices (tmpfs).
    digi_work_dir: str = "/run/ft950"
    # jt9's own data (FFTW wisdom). Persistent: in /run it was lost on every
    # restart and the first decode re-planned the FFTs (12 s instead of 9 s).
    digi_data_dir: str = "/var/lib/ft950/jt9"
    digi_keep_slices: int = 20       # per mode, for A/B checks against WSJT-X
    my_call: str = ""                # enables jt9 a-priori decoding (-c)
    my_grid: str = ""                # (-G)


settings = Settings()
