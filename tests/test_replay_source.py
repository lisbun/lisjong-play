import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lisjong.policy_contract import (
    AnkanAction,
    ChiAction,
    DaiminkanAction,
    DiscardAction,
    KakanAction,
    KyuushuKyuuhaiAction,
    PassAction,
    PonAction,
    RiichiAction,
    RonAction,
    Seat,
    TileCategory,
    TsumoAction,
)
from lisjong_arena.game_trace import GameTrace, GameTraceEvent
from lisjong_engine.public_state import PublicTile
from lisjong_engine.tile import TileCategory as EngineTileCategory
from lisjong_engine.tile import TileType as EngineTileType

from lisjong_play.formatting import format_tile, tile_sort_key
from lisjong_play.replay_source import (
    ReplayLoadError,
    _action_label,
    _tile_sort_key,
    build_timeline,
    load_replay_timeline,
    seat_name,
    tile_label,
)
from lisjong_play.tile_images import TILE_ASSET_FILENAMES
from tests import _replay_fixtures as fixtures
from tests._replay_fixtures import (
    FINAL_RANKS,
    FINAL_SCORES,
    GAME_MODE,
    SEED,
    save_fixture_record,
    tile,
)

_ALL_RECORD_TILES = tuple(
    tile(category, rank, is_red=is_red)
    for category in (TileCategory.MANZU, TileCategory.PINZU, TileCategory.SOUZU)
    for rank in range(1, 10)
    for is_red in ((False, True) if rank == 5 else (False,))
) + tuple(tile(TileCategory.HONOR, rank) for rank in range(1, 8))

_ENGINE_CATEGORIES = {
    TileCategory.MANZU: EngineTileCategory.MANZU,
    TileCategory.PINZU: EngineTileCategory.PINZU,
    TileCategory.SOUZU: EngineTileCategory.SOUZU,
    TileCategory.HONOR: EngineTileCategory.HONOR,
}


def _engine_tile(value):
    return PublicTile(
        tile_type=EngineTileType(
            category=_ENGINE_CATEGORIES[value.tile_type.category],
            rank=value.tile_type.rank,
        ),
        is_red=value.is_red,
    )


class ReplayLoaderBoundaryTest(unittest.TestCase):
    """recordの入口はArena strict loaderだけであり、失敗はfail closedになる。"""

    def test_default_loader_is_the_arena_supported_strict_loader(self) -> None:
        from lisjong_arena.durable_local_game_record import load_local_game_record

        from lisjong_play.replay_source import _default_loader

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            expected = load_local_game_record(path)
            self.assertEqual(
                expected.record_identity, _default_loader(path).record_identity
            )

    def test_valid_completed_record_opens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            saved = save_fixture_record(path)
            timeline = load_replay_timeline(path)

        self.assertEqual(saved.record_identity, timeline.record_identity)
        self.assertEqual(SEED, timeline.seed)
        self.assertEqual(GAME_MODE, timeline.game_mode)
        self.assertEqual(4, len(timeline.frames))

    def test_missing_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReplayLoadError):
                load_replay_timeline(Path(directory) / "absent")

    def test_unknown_schema_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = manifest["schema_version"] + 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(ReplayLoadError):
                load_replay_timeline(path)

    def test_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            result_path = path / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            document["seed"] = document["seed"] + 1
            result_path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(ReplayLoadError):
                load_replay_timeline(path)

    def test_truncated_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            (path / "decisions.json").unlink()

            with self.assertRaises(ReplayLoadError):
                load_replay_timeline(path)

    def test_loader_failure_is_not_presented_as_a_partial_replay(self) -> None:
        def failing_loader(_path):
            raise RuntimeError("unsupported record")

        with self.assertRaises(ReplayLoadError) as caught:
            load_replay_timeline("ignored", loader=failing_loader)
        self.assertIn("unsupported record", str(caught.exception))

    def test_record_files_are_not_mutated_by_opening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            before = {item.name: item.read_bytes() for item in sorted(path.iterdir())}

            load_replay_timeline(path)

            after = {item.name: item.read_bytes() for item in sorted(path.iterdir())}
        self.assertEqual(before, after)


class ReplayTimelineProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        path = Path(self._directory.name) / "record"
        self.record = save_fixture_record(path)
        self.timeline = load_replay_timeline(path)

    def test_round_boundaries_come_from_recorded_round_identity(self) -> None:
        self.assertEqual(
            ["東1局 0本場", "東2局 0本場"],
            [item.label for item in self.timeline.rounds],
        )
        self.assertEqual(
            (0, 1),
            (
                self.timeline.rounds[0].first_frame_index,
                self.timeline.rounds[0].last_frame_index,
            ),
        )
        self.assertEqual(
            (2, 3),
            (
                self.timeline.rounds[1].first_frame_index,
                self.timeline.rounds[1].last_frame_index,
            ),
        )

    def test_one_step_may_contain_a_round_boundary(self) -> None:
        """step 2はryukyoku / end_kyoku / 次局start_kyoku / dahaiを含む。"""
        third = self.timeline.frames[2]
        self.assertEqual(2, third.step_ordinal)
        self.assertEqual(1, third.round_index)
        self.assertEqual(0, self.timeline.frames[1].round_index)

    def test_frames_keep_recorded_decision_identity(self) -> None:
        self.assertEqual(
            [(0, 0), (1, 1), (2, 2), (3, 3)],
            [(frame.step_ordinal, frame.seat) for frame in self.timeline.frames],
        )

    def test_board_uses_recorded_public_state(self) -> None:
        board = self.timeline.frames[1].board
        self.assertEqual("東1局 0本場", board.round_label)
        self.assertEqual("供託 1本 / 残り山 69枚", board.center_detail)
        self.assertEqual(("5m",), board.dora_indicators)
        by_label = {seat.label: seat for seat in board.seats}
        self.assertEqual(26000, by_label["P1（東家）"].score)
        self.assertEqual("立直", by_label["P2（南家）"].riichi)
        self.assertEqual("宣言中", by_label["P3（西家）"].riichi)
        self.assertEqual(
            ("1m",), tuple(item.tile for item in by_label["P1（東家）"].river)
        )
        self.assertEqual("P3", by_label["P1（東家）"].river[0].called_by)

    def test_viewer_relative_position_follows_the_recorded_decision_seat(self) -> None:
        positions = []
        for frame in self.timeline.frames:
            own = [seat for seat in frame.board.seats if seat.position == "bottom"]
            positions.append(own[0].label)
        self.assertEqual(
            ["P1（東家）", "P2（南家）", "P3（南家）", "P4（西家）"], positions
        )

    def test_only_the_recorded_decision_seat_hand_is_presented(self) -> None:
        """record v1はdecision seat自身のconcealed handしか保証しない。"""
        board = self.timeline.frames[0].board
        self.assertEqual("7p", board.drawn_tile)
        self.assertNotIn("", board.hand_tiles)
        for seat in board.seats:
            self.assertFalse(hasattr(seat, "hand_tiles"))

    def test_river_does_not_claim_a_riichi_declaration_marker(self) -> None:
        """record v1のdiscardは宣言牌markerを持たないため推測しない。"""
        for frame in self.timeline.frames:
            for seat in frame.board.seats:
                for cell in seat.river:
                    self.assertFalse(cell.is_riichi_declaration)

    def test_meld_presentation_comes_from_the_recorded_meld(self) -> None:
        board = self.timeline.frames[2].board
        melds = [seat.melds for seat in board.seats if seat.melds]
        self.assertEqual(1, len(melds))
        meld = melds[0][0]
        self.assertEqual("ポン", meld.type_label)
        self.assertEqual(("2p", "2p", "2p"), meld.tiles)
        self.assertEqual("P4", meld.from_seat)
        self.assertEqual("2p", meld.called_tile)

    def test_round_result_uses_only_recorded_objective_values(self) -> None:
        first = self.timeline.rounds[0].result_text
        self.assertIn("--- 東1局 0本場 終了 ---", first)
        self.assertIn("結果: 流局 (exhaustive_draw)", first)
        self.assertIn("点数移動: P1 +1000 / P2 +1000 / P3 -1000 / P4 -1000", first)
        self.assertIn("局開始時点数: P1 25000 / P2 25000 / P3 25000 / P4 25000", first)

        second = self.timeline.rounds[1].result_text
        self.assertIn("結果: 和了 P2（ロン）", second)
        self.assertIn("放銃: P3", second)
        self.assertIn("点数移動: P1 +1000 / P2 +5000 / P3 -3000 / P4 -3000", second)

    def test_round_result_does_not_present_unrecorded_scoring_detail(self) -> None:
        for round_view in self.timeline.rounds:
            for absent in ("役", "符", "翻", "聴牌:"):
                self.assertNotIn(f"{absent} ", round_view.result_text)

    def test_match_result_uses_recorded_scores_and_ranks(self) -> None:
        text = self.timeline.final_result_text
        self.assertIn("=== 半荘結果 ===", text)
        for rank, seat in sorted((rank, seat) for seat, rank in enumerate(FINAL_RANKS)):
            name = ("P1", "P2", "P3", "P4")[seat]
            self.assertIn(f"{rank}位 {name}: {FINAL_SCORES[seat]}点", text)

    def test_metadata_identifies_the_record_without_redefining_it(self) -> None:
        text = self.timeline.metadata_text
        self.assertIn(self.record.record_identity, text)
        self.assertIn(f"seed: {SEED}", text)
        self.assertIn(f"game mode: {GAME_MODE}", text)
        self.assertIn("P1=fixture-policy-0", text)
        self.assertIn("durable local game record v1", text)


class ReplayRecordConsistencyTest(unittest.TestCase):
    """recordが内部的に一致しない場合は、partial replayにせずfail closedする。"""

    class _Provenance:
        lisjong_arena_revision = "a"
        lisjong_revision = "b"
        lisjong_engine_revision = "c"

    def _record(self, inspection):
        class _Record:
            record_identity = "d" * 64
            policy_identities = ("p0", "p1", "p2", "p3")
            provenance = ReplayRecordConsistencyTest._Provenance()

        record = _Record()
        record.inspection = inspection
        return record

    def _with_replaced_event(self, index: int, payload: dict):
        """event数を変えずに1 eventだけ差し替えたinspectionを返す。"""
        inspection = fixtures.inspection()
        events = list(inspection.game_trace.events)
        events[index] = GameTraceEvent(
            sequence=index, event=json.dumps(payload, sort_keys=True)
        )
        trace = GameTrace(
            seed=inspection.game_trace.seed,
            game_mode=inspection.game_trace.game_mode,
            events=tuple(events),
        )
        return replace(inspection, game_trace=trace)

    def test_unsupported_objective_event_type_fails_closed(self) -> None:
        inspection = self._with_replaced_event(0, {"type": "unknown_future_event"})
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("unsupported objective event type", str(caught.exception))

    def test_round_without_a_recorded_result_fails_closed(self) -> None:
        inspection = self._with_replaced_event(
            3, {"type": "dahai", "actor": 0, "pai": "2m", "tsumogiri": False}
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("no recorded result event", str(caught.exception))

    def test_result_without_a_recorded_round_start_fails_closed(self) -> None:
        inspection = self._with_replaced_event(
            1, {"type": "ryukyoku", "reason": "abortive", "deltas": [0, 0, 0, 0]}
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("before any recorded round start", str(caught.exception))

    def test_round_count_mismatch_fails_closed(self) -> None:
        """decision側の局数とrecorded round結果の数が合わない場合。"""
        inspection = self._with_replaced_event(
            5, {"type": "dahai", "actor": 1, "pai": "3m", "tsumogiri": False}
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("do not correspond", str(caught.exception))

    def test_round_dealer_mismatch_fails_closed(self) -> None:
        """局名が同じでも、recorded親がdecision側と違えばfail closedする。"""
        inspection = self._with_replaced_event(
            5,
            {
                "type": "start_kyoku",
                "bakaze": "E",
                "kyoku": 2,
                "honba": 0,
                "oya": 3,
                "kyotaku": 0,
                "dora_marker": "3p",
                "scores": [26000, 26000, 24000, 24000],
            },
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("does not match its decisions", str(caught.exception))

    def test_round_identity_mismatch_fails_closed(self) -> None:
        inspection = self._with_replaced_event(
            5,
            {
                "type": "start_kyoku",
                "bakaze": "S",
                "kyoku": 4,
                "honba": 3,
                "oya": 1,
                "kyotaku": 0,
                "dora_marker": "3p",
                "scores": [26000, 26000, 24000, 24000],
            },
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(inspection))
        self.assertIn("does not match its decisions", str(caught.exception))


class ReplayRawEventTypeStrictnessTest(unittest.TestCase):
    """objective GameTrace fieldを暗黙のint()変換で受理しないこと。

    `GameTraceEvent`が保証するのはeventがvalid JSON objectであるところまでで、
    個々のMJAI fieldの型はGameTrace contractでは固定されていない。bool / float /
    string integerをintへcoerceせず、`ReplayLoadError`でfail closedする。
    """

    class _Provenance:
        lisjong_arena_revision = "a"
        lisjong_revision = "b"
        lisjong_engine_revision = "c"

    def _record(self, inspection):
        class _Record:
            record_identity = "d" * 64
            policy_identities = ("p0", "p1", "p2", "p3")
            provenance = ReplayRawEventTypeStrictnessTest._Provenance()

        record = _Record()
        record.inspection = inspection
        return record

    def _rejects(self, index: int, payload: dict) -> str:
        inspection = fixtures.inspection()
        events = list(inspection.game_trace.events)
        events[index] = GameTraceEvent(
            sequence=index, event=json.dumps(payload, sort_keys=True)
        )
        trace = GameTrace(
            seed=inspection.game_trace.seed,
            game_mode=inspection.game_trace.game_mode,
            events=tuple(events),
        )
        with self.assertRaises(ReplayLoadError) as caught:
            build_timeline(self._record(replace(inspection, game_trace=trace)))
        return str(caught.exception)

    def _start_kyoku(self, **overrides) -> dict:
        payload = {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "oya": 0,
            "kyotaku": 0,
            "dora_marker": "5m",
            "scores": [25000, 25000, 25000, 25000],
        }
        payload.update(overrides)
        return payload

    def _hora(self, **overrides) -> dict:
        payload = {
            "type": "hora",
            "actor": 1,
            "target": 2,
            "deltas": [1000, 5000, -3000, -3000],
        }
        payload.update(overrides)
        return payload

    def test_string_integer_oya_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(oya="0"))
        self.assertIn("'oya'", message)
        self.assertIn("JSON integer", message)

    def test_float_kyoku_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(kyoku=1.0))
        self.assertIn("'kyoku'", message)
        self.assertIn("JSON integer", message)

    def test_fractional_float_kyoku_is_rejected(self) -> None:
        self.assertIn("JSON integer", self._rejects(1, self._start_kyoku(kyoku=1.9)))

    def test_bool_honba_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(honba=True))
        self.assertIn("'honba'", message)
        self.assertIn("JSON integer", message)

    def test_bool_in_scores_is_rejected(self) -> None:
        message = self._rejects(
            1, self._start_kyoku(scores=[True, 25000, 25000, 25000])
        )
        self.assertIn("'scores'[0]", message)
        self.assertIn("JSON integer", message)

    def test_float_in_scores_is_rejected(self) -> None:
        message = self._rejects(
            1, self._start_kyoku(scores=[25000.0, 25000, 25000, 25000])
        )
        self.assertIn("'scores'[0]", message)
        self.assertIn("JSON integer", message)

    def test_string_integer_in_deltas_is_rejected(self) -> None:
        message = self._rejects(7, self._hora(deltas=["1000", 5000, -3000, -3000]))
        self.assertIn("'deltas'[0]", message)
        self.assertIn("JSON integer", message)

    def test_bool_hora_actor_is_rejected(self) -> None:
        message = self._rejects(7, self._hora(actor=True))
        self.assertIn("'actor'", message)
        self.assertIn("JSON integer", message)

    def test_string_integer_hora_target_is_rejected(self) -> None:
        message = self._rejects(7, self._hora(target="2"))
        self.assertIn("'target'", message)
        self.assertIn("JSON integer", message)

    def test_out_of_range_seat_is_rejected(self) -> None:
        self.assertIn("seat in 0..3", self._rejects(7, self._hora(actor=4)))

    def test_non_array_scores_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(scores=25000))
        self.assertIn("'scores'", message)
        self.assertIn("JSON array", message)

    def test_wrong_length_scores_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(scores=[25000, 25000, 25000]))
        self.assertIn("four values", message)

    def test_non_string_bakaze_is_rejected(self) -> None:
        message = self._rejects(1, self._start_kyoku(bakaze=0))
        self.assertIn("'bakaze'", message)
        self.assertIn("JSON string", message)

    def test_non_string_ryukyoku_reason_is_rejected(self) -> None:
        message = self._rejects(
            3, {"type": "ryukyoku", "reason": 0, "deltas": [0, 0, 0, 0]}
        )
        self.assertIn("'reason'", message)
        self.assertIn("JSON string", message)

    def test_non_string_event_type_is_rejected(self) -> None:
        message = self._rejects(0, {"type": 1})
        self.assertIn("unsupported objective event type", message)

    def test_well_formed_integers_are_still_accepted(self) -> None:
        """strict化がvalid recordのnavigation semanticsを変えないこと。"""
        inspection = fixtures.inspection()
        timeline = build_timeline(self._record(inspection))
        self.assertEqual(4, len(timeline.frames))
        self.assertEqual(
            ["東1局 0本場", "東2局 0本場"], [item.label for item in timeline.rounds]
        )


class ReplayTypedContractBoundaryTest(unittest.TestCase):
    """typed contractとして復元済みのvalueは、raw JSONとして再検証しない。"""

    def test_typed_seat_values_are_required_to_be_seats(self) -> None:
        for value in (0, True, "0", 1.0, None):
            with self.subTest(value=value):
                with self.assertRaises(ReplayLoadError):
                    seat_name(value)

    def test_restored_seat_enum_is_accepted(self) -> None:
        self.assertEqual("P1", seat_name(Seat.SEAT_0))
        self.assertEqual("P4", seat_name(Seat.SEAT_3))

    def test_final_result_uses_the_typed_local_game_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record"
            save_fixture_record(path)
            timeline = load_replay_timeline(path)
        self.assertIn("1位 P2: 31000点", timeline.final_result_text)


class ReplayLabelVocabularyTest(unittest.TestCase):
    """Replay labelはlive GUIと同じcanonical tile語彙を使う。"""

    def test_every_record_tile_matches_the_live_formatter(self) -> None:
        for value in _ALL_RECORD_TILES:
            with self.subTest(tile=value):
                self.assertEqual(format_tile(_engine_tile(value)), tile_label(value))

    def test_every_record_tile_resolves_to_a_bundled_image(self) -> None:
        for value in _ALL_RECORD_TILES:
            self.assertIn(tile_label(value), TILE_ASSET_FILENAMES)

    def test_display_order_matches_the_live_formatter(self) -> None:
        by_replay = [
            tile_label(item) for item in sorted(_ALL_RECORD_TILES, key=_tile_sort_key)
        ]
        by_live = [
            format_tile(item)
            for item in sorted(
                (_engine_tile(value) for value in _ALL_RECORD_TILES), key=tile_sort_key
            )
        ]
        self.assertEqual(by_live, by_replay)


class ReplayActionLabelTest(unittest.TestCase):
    """recorded actionの表示は全variantを網羅し、未知variantはfail closedする。"""

    def test_every_recorded_action_variant_has_a_label(self) -> None:
        manzu_1 = tile(TileCategory.MANZU, 1)
        manzu_2 = tile(TileCategory.MANZU, 2)
        manzu_3 = tile(TileCategory.MANZU, 3)
        cases = (
            (
                DiscardAction(actor=Seat.SEAT_0, tile=manzu_1, tsumogiri=False),
                "打牌 1m",
            ),
            (
                DiscardAction(actor=Seat.SEAT_0, tile=manzu_1, tsumogiri=True),
                "打牌 1m（ツモ切り）",
            ),
            (RiichiAction(actor=Seat.SEAT_0), "立直宣言"),
            (
                ChiAction(
                    actor=Seat.SEAT_0,
                    target=Seat.SEAT_3,
                    called_tile=manzu_1,
                    consumed_tiles=(manzu_2, manzu_3),
                ),
                "チー 1m / 使用 2m 3m / from P4",
            ),
            (
                PonAction(
                    actor=Seat.SEAT_0,
                    target=Seat.SEAT_2,
                    called_tile=manzu_1,
                    consumed_tiles=(manzu_1, manzu_1),
                ),
                "ポン 1m / 使用 1m 1m / from P3",
            ),
            (
                DaiminkanAction(
                    actor=Seat.SEAT_0,
                    target=Seat.SEAT_1,
                    called_tile=manzu_1,
                    consumed_tiles=(manzu_1, manzu_1, manzu_1),
                ),
                "大明槓 1m / 使用 1m 1m 1m / from P2",
            ),
            (
                AnkanAction(
                    actor=Seat.SEAT_0, tiles=(manzu_1, manzu_1, manzu_1, manzu_1)
                ),
                "暗槓 1m 1m 1m 1m",
            ),
            (
                KakanAction(
                    actor=Seat.SEAT_0,
                    added_tile=manzu_1,
                    from_seat=Seat.SEAT_1,
                    called_tile=manzu_1,
                ),
                "加槓 1m",
            ),
            (
                RonAction(actor=Seat.SEAT_0, target=Seat.SEAT_1, winning_tile=manzu_1),
                "ロン 1m / from P2",
            ),
            (TsumoAction(actor=Seat.SEAT_0, winning_tile=manzu_1), "ツモ 1m"),
            (PassAction(actor=Seat.SEAT_0), "パス"),
            (KyuushuKyuuhaiAction(actor=Seat.SEAT_0), "九種九牌"),
        )
        for action, expected in cases:
            with self.subTest(action=type(action).__name__):
                self.assertEqual(expected, _action_label(action))

    def test_unknown_action_variant_fails_closed(self) -> None:
        class UnknownFutureAction:
            actor = Seat.SEAT_0

        with self.assertRaises(ReplayLoadError):
            _action_label(UnknownFutureAction())


if __name__ == "__main__":
    unittest.main()
