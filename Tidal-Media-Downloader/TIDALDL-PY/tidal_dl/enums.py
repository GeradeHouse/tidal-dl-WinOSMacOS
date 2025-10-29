from enum import Enum


class AudioQuality(Enum):
    LOW = "LOW"  # Maps to M4A High Efficiency
    HIGH = "HIGH"  # Maps to M4A Full Bandwidth
    LOSSLESS = "LOSSLESS"  # Maps to FLAC CD Standard
    HI_RES_LOSSLESS = "HI_RES_LOSSLESS"  # Maps to FLAC Hi-Res
    HIGHEST = "HIGHEST"  # Maps to Highest Available
    MP3 = "MP3"


class Type(Enum):
    Album = 0
    Track = 1
    Playlist = 3
    Artist = 4
    Mix = 5
    Null = 6
