#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
Configuration classes for Tidal Media Downloader.
"""

from typing import Literal


class RymConfig:
    """Configuration for Rate Your Music (RYM) metadata service."""

    def __init__(self, enabled: bool = False, genre_mode: Literal["replace", "append"] = "replace"):
        """
        Initialize RYM configuration.
        
        Args:
            enabled: Whether RYM metadata enrichment is enabled
            genre_mode: How to handle genres - "replace" existing or "append" to existing
        """
        self.enabled = enabled
        self.genre_mode = genre_mode

    def __repr__(self):
        return f"RymConfig(enabled={self.enabled}, genre_mode='{self.genre_mode}')"