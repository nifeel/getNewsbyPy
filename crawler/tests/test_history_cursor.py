from app.services.history_api import advance_history_cursor, history_resume_start_id


def test_advance_history_cursor_uses_smaller_batch_min() -> None:
    assert advance_history_cursor(9740602, 9740578) == 9740578


def test_advance_history_cursor_steps_back_when_min_equals_cursor() -> None:
    assert advance_history_cursor(9740379, 9740379) == 9740378


def test_advance_history_cursor_steps_back_when_min_is_ahead() -> None:
    assert advance_history_cursor(9740379, 9740402) == 9740378


def test_advance_history_cursor_stops_at_one() -> None:
    assert advance_history_cursor(1, 1) is None
    assert advance_history_cursor(1, 20) is None


def test_history_resume_start_id_skips_when_target_reached() -> None:
    assert history_resume_start_id(
        target_reached=True,
        checkpoints=[9740379, 9740602],
        latest_news_id=9744183,
    ) is None


def test_history_resume_start_id_uses_oldest_checkpoint() -> None:
    assert history_resume_start_id(
        target_reached=False,
        checkpoints=[9744014, 9740379, 9740602],
        latest_news_id=9744183,
    ) == 9740379


def test_history_resume_start_id_falls_back_to_latest() -> None:
    assert history_resume_start_id(
        target_reached=False,
        checkpoints=[],
        latest_news_id=9744183,
    ) == 9744183
