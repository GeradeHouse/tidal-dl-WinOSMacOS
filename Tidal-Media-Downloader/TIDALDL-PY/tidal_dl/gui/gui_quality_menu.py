# tidal_dl/gui/gui_quality_menu.py

from tidal_dl.enums import AudioQuality


DOWNLOAD_QUALITY_MENU_ITEMS = (
    ("AAC (Low)", AudioQuality.LOW),
    ("MP3 (Constant Bitrate)", AudioQuality.MP3),
    ("FLAC (CD Standard)", AudioQuality.LOSSLESS),
    ("FLAC (Max HiRes)", AudioQuality.HI_RES_LOSSLESS),
    ("Highest Available", AudioQuality.HIGHEST),
)
