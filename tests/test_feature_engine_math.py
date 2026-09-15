from apps.api.app.services.feature_engine import _form, _points


class MatchStub:
    def __init__(self, home_team_id, away_team_id, home_score, away_score):
        self.home_team_id = home_team_id
        self.away_team_id = away_team_id
        self.home_score = home_score
        self.away_score = away_score


def test_points_are_deterministic():
    assert _points(2, 1) == 3
    assert _points(1, 1) == 1
    assert _points(0, 2) == 0


def test_form_uses_team_perspective_home_and_away():
    matches = [
        MatchStub(1, 2, 2, 0),
        MatchStub(3, 1, 1, 1),
        MatchStub(1, 4, 0, 3),
    ]
    form = _form(matches, 1, 5)
    assert form.games == 3
    assert form.gf_per_game == 1.0
    assert form.ga_per_game == 4 / 3
    assert form.points_per_game == 4 / 3
    assert (form.wins, form.draws, form.losses) == (1, 1, 1)
    assert form.complete is False


def test_form_respects_window_limit():
    matches = [MatchStub(1, 2, 1, 0) for _ in range(6)]
    form = _form(matches, 1, 5)
    assert form.games == 5
    assert form.complete is True
    assert form.wins == 5


def test_empty_form_is_explicitly_unavailable():
    form = _form([], 1, 5)
    assert form.games == 0
    assert form.gf_per_game is None
    assert form.ga_per_game is None
    assert form.points_per_game is None
    assert form.complete is False
