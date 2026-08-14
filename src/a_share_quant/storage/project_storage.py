"""Project-local storage boundary policy."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

_MANAGED_DIRECTORIES = {
    "TEMP": Path(".runtime/tmp"),
    "TMP": Path(".runtime/tmp"),
    "PIP_CACHE_DIR": Path(".runtime/cache/pip"),
    "JOBLIB_TEMP_FOLDER": Path(".runtime/cache/joblib"),
    "XDG_CACHE_HOME": Path(".runtime/cache/xdg"),
    "MPLCONFIGDIR": Path(".runtime/cache/matplotlib"),
}
_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL", "CLOCK$", "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³")
    + tuple(f"COM{index}" for index in range(1, 10))
    + tuple(f"LPT{index}" for index in range(1, 10))
)


class StorageBoundaryError(ValueError):
    """Raised when a path crosses the project-local storage boundary."""


class ProjectStoragePolicy:
    """Authorize storage paths relative to a repository root."""

    def __init__(
        self,
        repo_root: str | os.PathLike[str],
        required_drive: str | None = "D:",
    ) -> None:
        try:
            resolved_root = Path(repo_root).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise StorageBoundaryError("项目目录必须已存在且可严格解析") from exc
        if not resolved_root.is_dir():
            raise StorageBoundaryError("项目目录必须是已存在的目录")
        if required_drive is not None and (
            resolved_root.drive.casefold() != required_drive.casefold()
        ):
            drive_label = required_drive.rstrip(":")
            raise StorageBoundaryError(f"生产项目目录必须位于{drive_label}盘")
        self.repo_root = resolved_root

    def authorize(self, value: str | os.PathLike[str]) -> Path:
        """Return a normalized in-project path without creating the final target.

        Callers that perform I/O later must call :meth:`revalidate` immediately
        before that I/O because an authorized path does not hold directory handles.
        """

        supplied = Path(value)
        candidate = supplied if supplied.is_absolute() else self.repo_root / supplied
        normalized = _lexically_normalized_absolute_path(candidate)
        if not _is_within(normalized, self.repo_root):
            raise StorageBoundaryError("存储路径必须位于项目目录内")
        _validate_windows_path_components(normalized.relative_to(self.repo_root))
        self._validate_no_reparse_components(normalized)
        return normalized

    def revalidate(self, value: str | os.PathLike[str]) -> Path:
        """Freshly recheck an authorized path immediately before caller-owned I/O.

        The returned path is still not an atomic open handle; callers must not cache
        this result or treat it as eliminating the final check-to-use interval.
        """

        return self.authorize(value)

    def child_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        """Return a child-only environment with project-local temporary storage."""

        child = dict(base)
        prepared: dict[Path, Path] = {}
        for relative in dict.fromkeys(_MANAGED_DIRECTORIES.values()):
            directory = self.authorize(relative)
            self._prepare_directory(directory)
            prepared[relative] = directory
        for key, relative in _MANAGED_DIRECTORIES.items():
            child[key] = str(prepared[relative])
        return child

    def _prepare_directory(self, directory: Path) -> None:
        self.authorize(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageBoundaryError("无法在项目目录内创建受控目录") from exc
        self.revalidate(directory / ".storage-boundary-check")
        if not directory.is_dir():
            raise StorageBoundaryError("项目目录内的受控路径必须是目录")

    def _validate_no_reparse_components(self, candidate: Path) -> None:
        relative = candidate.relative_to(self.repo_root)
        current = self.repo_root
        _reject_reparse_component(current)
        for part in relative.parts:
            current /= part
            _reject_reparse_component(current)


def _lexically_normalized_absolute_path(path: Path) -> Path:
    raw_path = os.fspath(path)
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(os.getcwd(), raw_path)
    return Path(os.path.normpath(raw_path))


def _is_within(candidate: Path, repo_root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(candidate), os.fspath(repo_root)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(repo_root))


def _validate_windows_path_components(relative: Path) -> None:
    for component in PureWindowsPath(os.fspath(relative)).parts:
        if ":" in component:
            raise StorageBoundaryError("项目目录路径不能包含 Windows ADS 冒号")
        if component.endswith((".", " ")):
            raise StorageBoundaryError("项目目录路径组件不能以点或空格结尾")
        normalized_base = component.rstrip(" .").split(".", maxsplit=1)[0].rstrip(" .")
        if normalized_base.upper() in _WINDOWS_RESERVED_DEVICE_NAMES:
            raise StorageBoundaryError("项目目录路径不能使用 Windows 保留设备名")


def _reject_reparse_component(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StorageBoundaryError("无法安全检查项目目录路径") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        raise StorageBoundaryError(
            "项目目录内路径不能包含符号链接、junction 或 reparse point"
        )
