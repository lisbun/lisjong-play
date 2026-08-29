"""Engine public decision boundaryを直接使うHuman selector。"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from lisjong_engine.action_descriptor import (
    ActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
    is_action_descriptor,
)
from lisjong_engine.observation import ObservationDecisionKind, SeatObservation
from lisjong_engine.public_state import PublicTile

from lisjong_play.formatting import format_tile, tile_sort_key
from lisjong_play.renderer import (
    render_action_menu,
    render_board,
    render_discard_menu,
    render_reaction_board,
)

_REACTION_KINDS = frozenset(
    {
        ObservationDecisionKind.DISCARD_REACTION,
        ObservationDecisionKind.KAKAN_REACTION,
        ObservationDecisionKind.ANKAN_REACTION,
    }
)


class HumanSelectionError(RuntimeError):
    """Human selector inputへ曖昧または不整合なpublic option集合が渡された場合。"""


@dataclass(frozen=True)
class _DiscardMenuChoices:
    hand_tiles: tuple[PublicTile, ...]
    hand_numbers: tuple[int | None, ...]
    actions_by_number: tuple[DiscardActionDescriptor, ...]
    tsumogiri: DiscardActionDescriptor | None


class HumanActionSelector:
    """engine public ActionDescriptorからHumanが1件を選ぶ同期selector。"""

    def __init__(
        self,
        input_reader: Callable[[str], str],
        output_writer: Callable[[str], None],
    ) -> None:
        if not callable(input_reader):
            raise TypeError("input_reader must be callable")
        if not callable(output_writer):
            raise TypeError("output_writer must be callable")
        self._input_reader = input_reader
        self._output_writer = output_writer

    def __call__(
        self,
        observation: SeatObservation,
        options: Iterable[ActionDescriptor],
    ) -> ActionDescriptor:
        if not isinstance(observation, SeatObservation):
            raise TypeError("observation must be a SeatObservation")
        values = self._normalize_options(options)

        is_reaction = observation.decision_kind in _REACTION_KINDS
        is_pure_discard = (
            observation.decision_kind is ObservationDecisionKind.TURN
            and all(isinstance(value, DiscardActionDescriptor) for value in values)
        )

        if is_reaction:
            self._output_writer(render_reaction_board(observation))
            pass_action = self._find_pass(values)
            if pass_action is not None:
                return self._choose_with_pass(values, pass_action)
            return self._choose_plain(values)

        if is_pure_discard:
            self._output_writer(render_board(observation, include_hand=False))
            discard_values = tuple(
                value for value in values if isinstance(value, DiscardActionDescriptor)
            )
            return self._choose_pure_discard(observation, discard_values)

        self._output_writer(render_board(observation))
        tsumogiri = self._find_tsumogiri(values)
        if tsumogiri is not None:
            self._validate_tsumogiri(observation, tsumogiri)
            return self._choose_with_tsumogiri(values, tsumogiri)
        return self._choose_plain(values)

    @staticmethod
    def _normalize_options(
        options: Iterable[ActionDescriptor],
    ) -> tuple[ActionDescriptor, ...]:
        try:
            values = tuple(options)
        except TypeError:
            raise TypeError("options must be iterable") from None
        if not values:
            raise ValueError("options must not be empty")
        if any(not is_action_descriptor(value) for value in values):
            raise TypeError("options must contain only current ActionDescriptor values")
        return values

    @staticmethod
    def _find_pass(
        options: tuple[ActionDescriptor, ...],
    ) -> PassActionDescriptor | None:
        passes = tuple(
            option for option in options if isinstance(option, PassActionDescriptor)
        )
        if len(passes) > 1:
            raise HumanSelectionError(
                "reaction decision must not expose multiple pass options"
            )
        return passes[0] if passes else None

    @staticmethod
    def _find_tsumogiri(
        options: tuple[ActionDescriptor, ...],
    ) -> DiscardActionDescriptor | None:
        tsumogiri = tuple(
            option
            for option in options
            if isinstance(option, DiscardActionDescriptor) and option.is_tsumogiri
        )
        if len(tsumogiri) > 1:
            raise HumanSelectionError(
                "decision must not expose multiple tsumogiri options"
            )
        return tsumogiri[0] if tsumogiri else None

    @staticmethod
    def _validate_tsumogiri(
        observation: SeatObservation,
        tsumogiri: DiscardActionDescriptor,
    ) -> None:
        if observation.drawn_tile != tsumogiri.tile:
            raise HumanSelectionError(
                "tsumogiri descriptor must match SeatObservation.drawn_tile"
            )

    def _choose_pure_discard(
        self,
        observation: SeatObservation,
        options: tuple[DiscardActionDescriptor, ...],
    ) -> DiscardActionDescriptor:
        menu = self._build_discard_menu(observation, options)
        self._output_writer(
            render_discard_menu(
                menu.hand_tiles,
                menu.hand_numbers,
                tsumogiri_tile=(menu.tsumogiri.tile if menu.tsumogiri else None),
            )
        )

        if menu.tsumogiri is not None:
            prompt = (
                "番号を入力してください"
                f"（Enter=ツモ切り {format_tile(menu.tsumogiri.tile)}）: "
            )
        else:
            prompt = "番号を入力してください: "

        while True:
            raw = self._input_reader(prompt)
            stripped = raw.strip()
            if not stripped:
                if menu.tsumogiri is not None:
                    return menu.tsumogiri
                self._output_writer("入力が空です。番号を入力してください。")
                continue

            if not menu.actions_by_number:
                self._output_writer("この場面ではEnterのみ有効です。")
                continue

            number = self._parse_number(stripped, len(menu.actions_by_number))
            if number is None:
                continue
            return menu.actions_by_number[number - 1]

    def _build_discard_menu(
        self,
        observation: SeatObservation,
        options: tuple[DiscardActionDescriptor, ...],
    ) -> _DiscardMenuChoices:
        tsumogiri = self._find_tsumogiri(tuple(options))
        if tsumogiri is not None:
            self._validate_tsumogiri(observation, tsumogiri)

        numbered = tuple(
            sorted(
                (option for option in options if not option.is_tsumogiri),
                key=lambda option: tile_sort_key(option.tile),
            )
        )
        for index, option in enumerate(numbered):
            if any(option.tile == previous.tile for previous in numbered[:index]):
                raise HumanSelectionError(
                    "pure discard decision must not expose duplicate non-tsumogiri tiles"
                )

        hand_tiles = list(sorted(observation.hand_tiles, key=tile_sort_key))
        if tsumogiri is not None:
            for index in range(len(hand_tiles) - 1, -1, -1):
                if hand_tiles[index] == tsumogiri.tile:
                    hand_tiles.pop(index)
                    break
            else:  # pragma: no cover - SeatObservation contract already guards this
                raise HumanSelectionError(
                    "tsumogiri descriptor tile must be present in hand_tiles"
                )

        hand_numbers = []
        for hand_tile in hand_tiles:
            matching_numbers = tuple(
                number
                for number, option in enumerate(numbered, start=1)
                if option.tile == hand_tile
            )
            if len(matching_numbers) > 1:  # pragma: no cover - duplicate guard above
                raise HumanSelectionError(
                    "hand tile must not map to multiple discard menu numbers"
                )
            hand_numbers.append(matching_numbers[0] if matching_numbers else None)

        return _DiscardMenuChoices(
            hand_tiles=tuple(hand_tiles),
            hand_numbers=tuple(hand_numbers),
            actions_by_number=numbered,
            tsumogiri=tsumogiri,
        )

    def _choose_with_pass(
        self,
        options: tuple[ActionDescriptor, ...],
        pass_action: PassActionDescriptor,
    ) -> ActionDescriptor:
        other_options = tuple(option for option in options if option is not pass_action)
        if not other_options:
            self._output_writer(
                "選択できる操作はパスのみです。Enterで続行してください。"
            )
            while True:
                if not self._input_reader("Enterで続行: ").strip():
                    return pass_action
                self._output_writer("この場面ではEnterのみ有効です。")

        self._output_writer(
            render_action_menu(
                other_options,
                header="操作を選んでください（Enter=パス）:",
            )
        )
        while True:
            raw = self._input_reader("Enter=パス、番号=行動: ")
            stripped = raw.strip()
            if not stripped:
                return pass_action
            number = self._parse_number(stripped, len(other_options))
            if number is not None:
                return other_options[number - 1]

    def _choose_with_tsumogiri(
        self,
        options: tuple[ActionDescriptor, ...],
        tsumogiri: DiscardActionDescriptor,
    ) -> ActionDescriptor:
        other_options = tuple(option for option in options if option is not tsumogiri)
        if not other_options:
            self._output_writer(
                "選択できる操作はツモ切りのみです。Enterで続行してください。"
            )
            while True:
                if not self._input_reader("Enterで続行: ").strip():
                    return tsumogiri
                self._output_writer("この場面ではEnterのみ有効です。")

        self._output_writer(render_action_menu(other_options))
        while True:
            raw = self._input_reader("Enter=ツモ切り、番号=その他の行動: ")
            stripped = raw.strip()
            if not stripped:
                return tsumogiri
            number = self._parse_number(stripped, len(other_options))
            if number is not None:
                return other_options[number - 1]

    def _choose_plain(self, options: tuple[ActionDescriptor, ...]) -> ActionDescriptor:
        self._output_writer(render_action_menu(options))
        while True:
            raw = self._input_reader("番号を入力してください: ")
            stripped = raw.strip()
            if not stripped:
                self._output_writer("入力が空です。番号を入力してください。")
                continue
            number = self._parse_number(stripped, len(options))
            if number is not None:
                return options[number - 1]

    def _parse_number(self, stripped: str, option_count: int) -> int | None:
        try:
            number = int(stripped)
        except ValueError:
            self._output_writer("数字で入力してください。")
            return None
        if number <= 0 or number > option_count:
            self._output_writer(f"1から{option_count}の範囲で入力してください。")
            return None
        return number
