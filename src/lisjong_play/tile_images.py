"""canonical tile label -> vendored FluffyStuff tile画像assetのregistry。

`lisjong_play.formatting.format_tile` が生成するcanonical tile label
（例: "1m" / "5pr" / "東"）だけをkeyとしてasset解決を行う。upstream filenameを
game semanticsとして扱わないため、mappingは本moduleだけに閉じる。
"""

from collections.abc import Callable, Mapping
from importlib import resources
from typing import Any

SOURCE_REPOSITORY = "https://github.com/FluffyStuff/riichi-mahjong-tiles"
SOURCE_REVISION = "26e127ba2117f45cdce5ea0225748cc0cfad3169"
SOURCE_PATH = "Export/Regular/"

_ASSET_PACKAGE = "lisjong_play"
_ASSET_SUBDIRECTORY = ("assets", "tiles")
_THIRD_PARTY_NOTICE_FILENAME = "THIRD_PARTY_NOTICE.md"

_SUIT_ASSET_PREFIXES: Mapping[str, str] = {"m": "Man", "p": "Pin", "s": "Sou"}
_HONOR_ASSET_NAMES: Mapping[str, str] = {
    "東": "Ton",
    "南": "Nan",
    "西": "Shaa",
    "北": "Pei",
    "白": "Haku",
    "發": "Hatsu",
    "中": "Chun",
}


def _build_tile_asset_filenames() -> dict[str, str]:
    filenames: dict[str, str] = {}
    for suffix, prefix in _SUIT_ASSET_PREFIXES.items():
        for rank in range(1, 10):
            filenames[f"{rank}{suffix}"] = f"{prefix}{rank}.png"
        filenames[f"5{suffix}r"] = f"{prefix}5-Dora.png"
    for label, name in _HONOR_ASSET_NAMES.items():
        filenames[label] = f"{name}.png"
    return filenames


TILE_ASSET_FILENAMES: Mapping[str, str] = _build_tile_asset_filenames()


class TileImageAssetError(LookupError):
    """canonical tile labelに対応する牌画像を一意に解決できない場合。

    別牌へのsilent fallbackを避けるため、未知のlabelやpackageから欠落した
    assetは必ずこの例外でfail closeする。
    """


def _asset_directory() -> Any:
    traversable = resources.files(_ASSET_PACKAGE)
    for part in _ASSET_SUBDIRECTORY:
        traversable = traversable / part
    return traversable


def tile_asset_traversable(tile_label: str) -> Any:
    """canonical tile labelから読み取り専用のpackage assetを解決する。

    process working directoryに依存しないimportlib.resourcesだけを使う。
    """
    try:
        filename = TILE_ASSET_FILENAMES[tile_label]
    except KeyError:
        raise TileImageAssetError(f"unknown tile label: {tile_label!r}") from None
    traversable = _asset_directory() / filename
    if not traversable.is_file():
        raise TileImageAssetError(f"missing tile asset file: {filename}")
    return traversable


def third_party_notice_text() -> str:
    """vendored assetのprovenance記録を読み込む。"""
    traversable = _asset_directory() / _THIRD_PARTY_NOTICE_FILENAME
    return traversable.read_text(encoding="utf-8")


class TileImageRegistry:
    """canonical tile label -> 生成済みimage objectをapplication lifetime中保持する。

    Tkinterの`PhotoImage`はPython側でreferenceを保持しないと表示が消えるため、
    同一labelには常に同じcache済みimage objectを返し、registry自身が
    application lifetime中生存することでreferenceを保つ。
    """

    def __init__(self, image_factory: Callable[[str], Any]) -> None:
        if not callable(image_factory):
            raise TypeError("image_factory must be callable")
        self._image_factory = image_factory
        self._cache: dict[str, Any] = {}

    def get(self, tile_label: str) -> Any:
        cached = self._cache.get(tile_label)
        if cached is not None:
            return cached
        traversable = tile_asset_traversable(tile_label)
        with resources.as_file(traversable) as path:
            image = self._image_factory(str(path))
        self._cache[tile_label] = image
        return image
