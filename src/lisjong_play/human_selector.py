"""Engine public decision boundaryを直接使うHuman selector。"""

from collections.abc import Callable, Iterable

from lisjong_engine.action_descriptor import (
    ActionDescriptor,
    DiscardActionDescriptor,
    PassActionDescriptor,
    is_action_descriptor,
)
from lisjong_engine.observation import ObservationDecisionKind, SeatObservation

from lisjong_play.formatting import format_tile
from lisjong_play.renderer import render_action_menu, render_board

_REACTION_KINDS = frozenset(
    {
        ObservationDecisionKind.DISCARD_REACTION,
        ObservationDecisionKind.KAKAN_REACTION,
        ObservationDecisionKind.ANKAN_REACTION,
    }
)


class HumanSelectionError(RuntimeError):
    """Human selector inputへ曖昧または不整合なpublic option集合が渡された場合。"""


class HumanActionSelector:
    """1-based menuからengine `ActionDescriptor`を1件選ぶ同期selector。"""

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

        self._output_writer(render_board(observation))
        self._output_writer(render_action_menu(values))

        default_action, default_label = self._default_action(observation, values)
        prompt = "番号を入力してください"
        if default_label is not None:
            prompt += f"（Enter={default_label}）"
        prompt += ": "

        while True:
            raw = self._input_reader(prompt)
            stripped = raw.strip()
            if not stripped:
                if default_action is not None:
                    return default_action
                self._output_writer("入力が空です。番号を入力してください。")
                continue
            try:
                number = int(stripped)
            except ValueError:
                self._output_writer("数字で入力してください。")
                continue
            if number <= 0 or number > len(values):
                self._output_writer(f"1から{len(values)}の範囲で入力してください。")
                continue
            return values[number - 1]

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
    def _default_action(
        observation: SeatObservation,
        options: tuple[ActionDescriptor, ...],
    ) -> tuple[ActionDescriptor | None, str | None]:
        if observation.decision_kind in _REACTION_KINDS:
            passes = tuple(option for option in options if isinstance(option, PassActionDescriptor))
            if len(passes) > 1:
                raise HumanSelectionError("reaction decision must not expose multiple pass options")
            if passes:
                return passes[0], "パス"

        tsumogiri = tuple(
            option
            for option in options
            if isinstance(option, DiscardActionDescriptor) and option.is_tsumogiri
        )
        if len(tsumogiri) > 1:
            raise HumanSelectionError("decision must not expose multiple tsumogiri options")
        if tsumogiri:
            return tsumogiri[0], f"ツモ切り {format_tile(tsumogiri[0].tile)}"
        return None, None
