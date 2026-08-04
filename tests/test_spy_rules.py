from app.domain.games.spy import resolve_vote


def test_yes_majority_closes_a_vote_early() -> None:
    resolution = resolve_vote(eligible_count=5, yes_count=3, cast_count=3)

    assert resolution.is_resolved is True
    assert resolution.accepted is True


def test_tie_keeps_candidate_after_everyone_voted() -> None:
    resolution = resolve_vote(eligible_count=4, yes_count=2, cast_count=4)

    assert resolution.is_resolved is True
    assert resolution.accepted is False


def test_vote_waits_for_missing_player_without_yes_majority() -> None:
    resolution = resolve_vote(eligible_count=4, yes_count=1, cast_count=3)

    assert resolution.is_resolved is False
    assert resolution.accepted is None
