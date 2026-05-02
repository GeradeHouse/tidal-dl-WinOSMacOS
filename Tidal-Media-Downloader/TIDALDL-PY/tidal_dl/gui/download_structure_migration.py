#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
One-time/manual migration for legacy download folders into audio-type folders.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from tidal_dl.paths import get_user_download_path
from tidal_dl.settings import SETTINGS

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

AUDIO_EXTENSIONS_TO_FOLDER = {
    ".flac": "flac",
    ".mp3": "mp3",
    ".m4a": "m4a",
    ".mp4": "mp4",
}
LYRICS_EXTENSIONS_TO_FOLDER = {
    ".lrc": "lyrics",
}
MANAGED_TOP_LEVEL_FOLDERS = set(AUDIO_EXTENSIONS_TO_FOLDER.values()) | set(
    LYRICS_EXTENSIONS_TO_FOLDER.values()
)
AUDIO_DESTINATION_PRIORITY = ("flac", "m4a", "mp3", "mp4")


@dataclass
class DownloadMigrationPlanItem:
    source_path: str
    destination_path: str
    destination_folder: str


@dataclass
class DownloadMigrationResult:
    root_path: str
    moved: int
    skipped: int
    failed: int
    destination_counts: Dict[str, int]
    failures: List[str]


class DownloadStructureMigrationWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

    def _download_root(self) -> str:
        return os.path.abspath(get_user_download_path(SETTINGS.downloadPath))

    def _is_already_inside_managed_folder(self, root_path: str, file_path: str) -> bool:
        rel_path = os.path.relpath(file_path, root_path)
        first_part = rel_path.split(os.sep, 1)[0].lower()
        return first_part in MANAGED_TOP_LEVEL_FOLDERS

    def _relative_stem_key(self, root_path: str, file_path: str) -> str:
        relative_path = os.path.relpath(file_path, root_path)
        relative_stem, _ext = os.path.splitext(relative_path)
        return os.path.normcase(os.path.normpath(relative_stem))

    def _existing_audio_folder_for_lyrics(
        self,
        root_path: str,
        lyrics_relative_stem: str,
    ) -> Optional[str]:
        for audio_folder in AUDIO_DESTINATION_PRIORITY:
            for audio_ext in AUDIO_EXTENSIONS_TO_FOLDER:
                candidate_path = os.path.join(
                    root_path,
                    audio_folder,
                    f"{lyrics_relative_stem}{audio_ext}",
                )
                if os.path.exists(candidate_path):
                    return audio_folder
        return None

    def _lyrics_destination_folder(
        self,
        root_path: str,
        lyrics_source_path: str,
        planned_audio_folders_by_relative_stem: Dict[str, str],
    ) -> str:
        relative_path = os.path.relpath(lyrics_source_path, root_path)
        relative_stem, _ext = os.path.splitext(relative_path)
        normalized_stem = os.path.normcase(os.path.normpath(relative_stem))

        planned_audio_folder = planned_audio_folders_by_relative_stem.get(
            normalized_stem
        )
        if planned_audio_folder:
            return planned_audio_folder

        existing_audio_folder = self._existing_audio_folder_for_lyrics(
            root_path,
            relative_stem,
        )
        if existing_audio_folder:
            return existing_audio_folder

        return LYRICS_EXTENSIONS_TO_FOLDER[".lrc"]

    def build_plan(self) -> List[DownloadMigrationPlanItem]:
        root_path = self._download_root()
        if not os.path.isdir(root_path):
            return []

        plan: List[DownloadMigrationPlanItem] = []
        legacy_lyrics_paths: List[str] = []
        planned_audio_folders_by_relative_stem: Dict[str, str] = {}

        for current_root, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname.lower() not in MANAGED_TOP_LEVEL_FOLDERS
            ]

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                source_path = os.path.join(current_root, filename)

                if self._is_already_inside_managed_folder(root_path, source_path):
                    continue

                audio_folder = AUDIO_EXTENSIONS_TO_FOLDER.get(ext)
                if audio_folder:
                    relative_path = os.path.relpath(source_path, root_path)
                    destination_path = os.path.join(root_path, audio_folder, relative_path)
                    if os.path.normcase(os.path.abspath(source_path)) == os.path.normcase(
                        os.path.abspath(destination_path)
                    ):
                        continue

                    planned_audio_folders_by_relative_stem[
                        self._relative_stem_key(root_path, source_path)
                    ] = audio_folder
                    plan.append(
                        DownloadMigrationPlanItem(
                            source_path=source_path,
                            destination_path=destination_path,
                            destination_folder=audio_folder,
                        )
                    )
                    continue

                if ext in LYRICS_EXTENSIONS_TO_FOLDER:
                    legacy_lyrics_paths.append(source_path)

        for lyrics_source_path in legacy_lyrics_paths:
            relative_path = os.path.relpath(lyrics_source_path, root_path)
            destination_folder = self._lyrics_destination_folder(
                root_path,
                lyrics_source_path,
                planned_audio_folders_by_relative_stem,
            )
            destination_path = os.path.join(root_path, destination_folder, relative_path)

            if os.path.normcase(os.path.abspath(lyrics_source_path)) == os.path.normcase(
                os.path.abspath(destination_path)
            ):
                continue

            plan.append(
                DownloadMigrationPlanItem(
                    source_path=lyrics_source_path,
                    destination_path=destination_path,
                    destination_folder=destination_folder,
                )
            )

        return plan

    def run(self) -> None:
        root_path = self._download_root()
        plan = self.build_plan()
        destination_counts: Dict[str, int] = {}
        failures: List[str] = []
        moved = 0
        skipped = 0
        failed = 0
        total = len(plan)

        for index, item in enumerate(plan, start=1):
            self.progress.emit(index, total, item.source_path)

            try:
                if not os.path.exists(item.source_path):
                    skipped += 1
                    continue

                if os.path.exists(item.destination_path):
                    skipped += 1
                    failures.append(
                        f"Skipped existing destination: {item.destination_path}"
                    )
                    continue

                os.makedirs(os.path.dirname(item.destination_path), exist_ok=True)
                shutil.move(item.source_path, item.destination_path)
                moved += 1
                destination_counts[item.destination_folder] = (
                    destination_counts.get(item.destination_folder, 0) + 1
                )
            except Exception as exc:
                failed += 1
                failures.append(f"{item.source_path} -> {item.destination_path}: {exc}")
                logger.warning(
                    "Failed to migrate download file %s",
                    item.source_path,
                    exc_info=True,
                )

        self._remove_empty_legacy_dirs(root_path)

        self.finished.emit(
            DownloadMigrationResult(
                root_path=root_path,
                moved=moved,
                skipped=skipped,
                failed=failed,
                destination_counts=destination_counts,
                failures=failures[:20],
            )
        )

    def _remove_empty_legacy_dirs(self, root_path: str) -> None:
        for current_root, _dirnames, _filenames in os.walk(root_path, topdown=False):
            if os.path.abspath(current_root) == os.path.abspath(root_path):
                continue
            rel_path = os.path.relpath(current_root, root_path)
            first_part = rel_path.split(os.sep, 1)[0].lower()
            if first_part in MANAGED_TOP_LEVEL_FOLDERS:
                continue
            try:
                if not os.listdir(current_root):
                    os.rmdir(current_root)
            except Exception:
                logger.debug("Failed to remove empty legacy directory %s", current_root)
