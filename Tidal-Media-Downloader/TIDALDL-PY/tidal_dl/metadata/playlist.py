import logging
from dataclasses import dataclass

from .album import AlbumMetadata
from .track import TrackMetadata
from .util import typed

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class PlaylistMetadata:
    name: str
    tracks: list[TrackMetadata] | list[str]

    @classmethod
    def from_tidal(cls, resp: dict):
        name = typed(resp["title"], str)
        tracks = [str(track["id"]) for track in resp["tracks"]]
        return cls(name, tracks)

    def ids(self) -> list[str]:
        if len(self.tracks) == 0:
            return []
        if isinstance(self.tracks[0], str):
            return self.tracks  # type: ignore

        return [track.info.id for track in self.tracks]  # type: ignore

    @classmethod
    def from_resp(cls, resp: dict, source: str):
        if source == "tidal":
            return cls.from_tidal(resp)
        else:
            raise NotImplementedError(source)