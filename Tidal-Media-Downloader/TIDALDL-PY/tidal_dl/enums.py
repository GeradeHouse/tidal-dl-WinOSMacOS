from enum import Enum


class AudioQuality(Enum):
    LOW = "LOW"  # TIDAL Low: compressed AAC
    HIGH = "HIGH"  # TIDAL High: FLAC 16-bit / 44.1 kHz when entitlement/profile allows it
    LOSSLESS = "LOSSLESS"  # Legacy API value for FLAC CD Standard
    HI_RES_LOSSLESS = "HI_RES_LOSSLESS"  # TIDAL Max: HiRes FLAC when available
    HIGHEST = "HIGHEST"  # Maps to Highest Available
    MP3 = "MP3"


class Type(Enum):
    Album = 0
    Track = 1
    Playlist = 3
    Artist = 4
    Mix = 5
    Null = 6
