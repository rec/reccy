from itertools import islice
from random import Random

import pytest
from pydantic import ValidationError

from reccy.randomizer import Randomizer


@pytest.mark.parametrize('n', [1, 2, 7])
@pytest.mark.parametrize('interval', [None, 0])
def test_interval_defaults_to_half_the_range(n: int, interval: int | None) -> None:
    value = Randomizer(n=n, dont_repeat_interval=interval)
    assert value.dont_repeat_interval == (n // 2 if interval is None else interval)
    assert isinstance(value.dont_repeat_interval, int)


@pytest.mark.parametrize('n', [0, -1, 1.5, True])
def test_range_requires_a_positive_integer(n: object) -> None:
    with pytest.raises(ValidationError):
        Randomizer(n=n)


@pytest.mark.parametrize('interval', [-1, 5, 6, 1.5, True])
def test_interval_requires_an_integer_less_than_n(interval: object) -> None:
    with pytest.raises(ValidationError):
        Randomizer(n=5, dont_repeat_interval=interval)


@pytest.mark.parametrize('use_all', [False, True])
@pytest.mark.parametrize('n', [1, 2, 5, 10])
def test_draws_obey_the_gap_and_cycle_contract(n: int, use_all: bool) -> None:
    for interval in range(n):
        value = Randomizer(
            n=n,
            dont_repeat_interval=interval,
            use_all_before_repeating=use_all,
            random=Random(19),
        )
        draws = list(islice(value.iter(), n * 20))
        for index, number in enumerate(draws):
            assert 0 <= number < n
            assert number not in draws[max(0, index - interval) : index]
        if use_all:
            for start in range(0, len(draws), n):
                assert sorted(draws[start : start + n]) == list(range(n))


@pytest.mark.parametrize('use_all', [False, True])
def test_maximum_gap_repeats_the_initial_order_forever(use_all: bool) -> None:
    value = Randomizer(
        n=8,
        dont_repeat_interval=7,
        use_all_before_repeating=use_all,
        random=Random(42),
    )
    iterator = value.iter()
    first = list(islice(iterator, 8))
    assert sorted(first) == list(range(8))
    assert list(islice(iterator, 80)) == first * 10


def test_zero_gap_allows_adjacent_repeats_without_cycles() -> None:
    value = Randomizer(
        n=2, dont_repeat_interval=0, use_all_before_repeating=False, random=Random(0)
    )
    iterator = value.iter()
    assert next(iterator) == next(iterator)


def test_gap_one_allows_repeating_after_one_other_number() -> None:
    iterator = Randomizer(n=2, dont_repeat_interval=1, random=Random(0)).iter()
    first = next(iterator)
    assert next(iterator) != first
    assert next(iterator) == first


def test_supplied_seed_reproduces_the_sequence() -> None:
    left = Randomizer(n=7, random=Random(42))
    right = Randomizer(n=7, random=Random(42))
    assert list(islice(left.iter(), 30)) == list(islice(right.iter(), 30))


def test_random_defaults_to_none() -> None:
    assert Randomizer(n=2).random is None


def test_omitted_interval_is_resolved_during_validation() -> None:
    assert Randomizer(n=7).dont_repeat_interval == 3


def test_without_cycles_or_gap_draws_are_independent() -> None:
    value = Randomizer(
        n=5, dont_repeat_interval=0, use_all_before_repeating=False, random=Random(42)
    )
    iterator = value.iter()
    expected = Random(42)
    for _ in range(100):
        assert next(iterator) == expected.randrange(5)


def test_field_iteration_remains_available() -> None:
    value = Randomizer(n=5)
    assert dict(value)['n'] == 5


def test_configuration_remains_frozen() -> None:
    value = Randomizer(n=5)
    with pytest.raises(ValidationError, match='frozen'):
        value.n = 6


@pytest.mark.parametrize('use_all', [False, True])
def test_interleaved_iterators_keep_separate_histories_and_cycles(
    use_all: bool,
) -> None:
    value = Randomizer(n=7, dont_repeat_interval=6, use_all_before_repeating=use_all)
    left = value.iter(Random(42))
    right = value.iter(Random(42))
    first = list(islice(left, 7))
    for index in range(70):
        assert next(right) == first[index % 7]
        assert next(left) == first[index % 7]


def test_iterator_random_overrides_model_random() -> None:
    model_random = Random(19)
    initial_state = model_random.getstate()
    value = Randomizer(n=5, random=model_random)
    expected = Randomizer(n=5, random=Random(42)).iter()
    iterator = value.iter(Random(42))
    for _ in range(30):
        assert next(iterator) == next(expected)
    assert model_random.getstate() == initial_state


@pytest.mark.parametrize('use_all', [False, True])
def test_interleaved_iterators_sharing_model_random_keep_separate_state(
    use_all: bool,
) -> None:
    value = Randomizer(
        n=7,
        dont_repeat_interval=6,
        use_all_before_repeating=use_all,
        random=Random(42),
    )
    left = value.iter()
    right = value.iter()
    left_draws: list[int] = []
    right_draws: list[int] = []
    for index in range(63):
        left_draws.append(next(left))
        if index % 3 == 0:
            right_draws.extend(islice(right, 3))
    for draws in [left_draws, right_draws]:
        for index, number in enumerate(draws):
            assert number not in draws[max(0, index - 6) : index]
        for start in range(0, len(draws), 7):
            assert sorted(draws[start : start + 7]) == list(range(7))
