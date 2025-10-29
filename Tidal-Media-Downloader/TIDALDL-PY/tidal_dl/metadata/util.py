import functools
from typing import Optional, Type, TypeVar
import logging

logger = logging.getLogger(__name__)


def safe_get(dictionary, *keys, default=None):
    return functools.reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys,
        dictionary,
    )


T = TypeVar("T")


def typed(thing, expected_type: Type[T]) -> T:
    # ADDED: Detailed debug logging to trace type assertions
    if not isinstance(thing, expected_type):
        logger.debug(
            f"[typed-assertion-failed] Value: '{thing}', "
            f"Type: {type(thing).__name__}, "
            f"Expected Type: {expected_type.__name__}"
        )
    assert isinstance(thing, expected_type)
    return thing


def get_quality_id(
    bit_depth: Optional[int],
    sampling_rate: Optional[int | float],
) -> int:
    """Get the universal quality id from bit depth and sampling rate.

    :param bit_depth:
    :type bit_depth: Optional[int]
    :param sampling_rate: In kHz
    :type sampling_rate: Optional[int]
    """
    # XXX: Should `0` quality be supported?
    if bit_depth is None or sampling_rate is None:  # is lossy
        return 1

    if bit_depth == 16:
        return 2

    if bit_depth == 24:
        if sampling_rate <= 96:
            return 3

        return 4

    raise Exception(f"Invalid {bit_depth = }")