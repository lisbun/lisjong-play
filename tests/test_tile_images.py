import os
import unittest

from lisjong_play.tile_images import (
    SOURCE_REVISION,
    TILE_ASSET_FILENAMES,
    TileImageAssetError,
    TileImageRegistry,
    third_party_notice_text,
    tile_asset_traversable,
)

_NORMAL_TILE_LABELS = tuple(
    f"{rank}{suffix}" for suffix in ("m", "p", "s") for rank in range(1, 10)
)
_RED_FIVE_LABELS = ("5mr", "5pr", "5sr")
_HONOR_LABELS = ("東", "南", "西", "北", "白", "發", "中")
_ALL_CANONICAL_LABELS = _NORMAL_TILE_LABELS + _RED_FIVE_LABELS + _HONOR_LABELS


class TileAssetMappingTest(unittest.TestCase):
    def test_all_34_normal_tiles_map_to_an_existing_asset(self) -> None:
        for label in _NORMAL_TILE_LABELS:
            with self.subTest(label=label):
                traversable = tile_asset_traversable(label)
                self.assertTrue(traversable.is_file())

    def test_red_fives_map_to_distinct_assets_from_their_normal_counterpart(
        self,
    ) -> None:
        for red_label, normal_label in (("5mr", "5m"), ("5pr", "5p"), ("5sr", "5s")):
            with self.subTest(label=red_label):
                self.assertNotEqual(
                    TILE_ASSET_FILENAMES[red_label], TILE_ASSET_FILENAMES[normal_label]
                )
                self.assertTrue(tile_asset_traversable(red_label).is_file())

    def test_all_honor_tiles_map_to_an_existing_asset(self) -> None:
        for label in _HONOR_LABELS:
            with self.subTest(label=label):
                self.assertTrue(tile_asset_traversable(label).is_file())

    def test_required_tile_set_is_exactly_34_normal_plus_3_red_fives(self) -> None:
        self.assertEqual(37, len(_ALL_CANONICAL_LABELS))
        self.assertEqual(set(_ALL_CANONICAL_LABELS), set(TILE_ASSET_FILENAMES))

    def test_unknown_tile_label_fails_closed_without_a_fallback_image(self) -> None:
        with self.assertRaises(TileImageAssetError):
            tile_asset_traversable("9z")


class TileAssetResourceResolutionTest(unittest.TestCase):
    def test_resolution_does_not_depend_on_the_process_working_directory(self) -> None:
        original_cwd = os.getcwd()
        os.chdir(os.path.expanduser("~"))
        try:
            traversable = tile_asset_traversable("5pr")
            self.assertTrue(traversable.is_file())
        finally:
            os.chdir(original_cwd)


class ThirdPartyNoticeTest(unittest.TestCase):
    def test_notice_records_the_pinned_source_revision(self) -> None:
        text = third_party_notice_text()
        self.assertIn(SOURCE_REVISION, text)
        self.assertIn("FluffyStuff/riichi-mahjong-tiles", text)
        self.assertIn("CC0", text)


class TileImageRegistryTest(unittest.TestCase):
    def test_get_builds_an_image_from_the_resolved_asset_path(self) -> None:
        calls: list[str] = []
        registry = TileImageRegistry(lambda path: calls.append(path) or path)

        image = registry.get("1m")

        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].endswith("Man1.png"))
        self.assertEqual(calls[0], image)

    def test_repeated_lookups_reuse_the_same_cached_image(self) -> None:
        registry = TileImageRegistry(lambda path: object())

        first = registry.get("東")
        second = registry.get("東")

        self.assertIs(first, second)

    def test_unknown_label_raises_without_calling_the_image_factory(self) -> None:
        factory_calls: list[str] = []
        registry = TileImageRegistry(lambda path: factory_calls.append(path))

        with self.assertRaises(TileImageAssetError):
            registry.get("9z")

        self.assertEqual([], factory_calls)

    def test_image_factory_must_be_callable(self) -> None:
        with self.assertRaises(TypeError):
            TileImageRegistry(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
