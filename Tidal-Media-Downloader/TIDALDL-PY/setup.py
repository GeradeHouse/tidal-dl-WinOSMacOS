from setuptools import setup, find_packages

setup(
    name="tidal-dl",
    version="3.0.0",  # Change this as needed
    packages=find_packages(),
    install_requires=[
        "aigpy==2025.05.03.1",
        "requests==2.31.0",
        "colorama==0.4.4",
        "prettytable==3.1.1",
        "mutagen==1.45.1",
        "psutil==5.9.0",
        "pycryptodome==3.14.1",
        "lyricsgenius==3.0.1",
        "pydub==0.25.1",
        "PyQt6",
        "qt-material==2.12",
        "spotipy",
        "moviepy",
        "ffpyplayer",
        "aiofiles",
        "aiohttp",
        "beautifulsoup4",
        "pathvalidate"
    ],
    entry_points={
        "console_scripts": [
            "tidal-dl=tidal_dl.__init__:main",
        ],
    },
)
