from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("streamrip")

# Set up GUI logging with DEBUG level for this module (metadata operations)
from ..gui.gui_logging import setup_gui_logger
setup_gui_logger("streamrip", logging.DEBUG)


@dataclass(slots=True)
class LabelMetadata:
    name: str
    ids: list[str]

    def album_ids(self):
        return self.ids

    @classmethod
    def from_resp(cls, resp: dict, source: str) -> LabelMetadata:
        logger.debug(resp)
        if source == "tidal":
            return cls(resp["name"], [a["id"] for a in resp["albums"]])
        else:
            raise NotImplementedError