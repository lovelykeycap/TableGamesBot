from datetime import datetime, timezone

from app.bot.renderers import RoomRenderer, home_markup
from app.domain.games.spy import ROLE_SPY
from app.services.views import MemberView, RoomView, SpyGameView, ThemeOption


def test_lobby_has_copy_buttons_but_does_not_render_a_clickable_join_link() -> None:
    view = RoomView(
        room_id=1,
        status="lobby",
        game_type="spy",
        join_code="ABC1234",
        join_token="safe-token",
        host_user_id=100,
        is_join_open=True,
        settings={
            "spy_count_mode": "random",
            "spy_count": 1,
            "spies_know_each_other": False,
            "theme_ids": [1],
        },
        revision=1,
        actor_user_id=100,
        actor_member_id=1,
        actor_status="active",
        actor_role=None,
        members=(
            MemberView(
                id=1,
                user_id=100,
                display_name="Хост",
                username=None,
                status="active",
                role=None,
                joined_at=datetime.now(timezone.utc),
            ),
        ),
        themes=(ThemeOption(id=1, name="Учёба", is_selected=True, has_active_locations=True),),
        game=None,
        vote=None,
        known_spy_names=(),
    )

    text, markup = RoomRenderer("testbot").render(view)
    buttons = [button for row in markup.inline_keyboard for button in row]
    copy_buttons = {button.text: button.copy_text.text for button in buttons if button.copy_text}

    assert "https://" not in text
    assert "Код комнаты: <code>ABC1234</code>\n\nИгроки (1/40):" in text
    assert "• Хост\n\nШпионы:" in text
    assert copy_buttons == {
        "📋 Скопировать код": "ABC1234",
        "🔗 Скопировать ссылку": "https://t.me/testbot?start=join_safe-token",
    }
    assert "🕵️" not in text
    assert "✅ 🎲 Случайное число" in {button.text for button in buttons}
    assert "🔢 Точное число" in {button.text for button in buttons}


def test_closed_room_has_a_return_to_games_button() -> None:
    view = RoomView(
        room_id=1,
        status="cancelled",
        game_type="spy",
        join_code="ABC1234",
        join_token="safe-token",
        host_user_id=100,
        is_join_open=False,
        settings={},
        revision=1,
        actor_user_id=100,
        actor_member_id=1,
        actor_status="left",
        actor_role=None,
        members=(),
        themes=(),
        game=None,
        vote=None,
        known_spy_names=(),
    )

    text, markup = RoomRenderer("testbot").render(view)

    assert text == "<b>Комната закрыта.</b>"
    assert [
        (button.text, button.callback_data) for row in markup.inline_keyboard for button in row
    ] == [("🎮 К играм", "h:home")]
    assert "🎮 Активные комнаты" in {
        button.text for row in home_markup().inline_keyboard for button in row
    }


def test_finished_game_lists_only_spies_and_returns_to_games() -> None:
    members = tuple(
        MemberView(
            id=index,
            user_id=index,
            display_name=f"Игрок {index}",
            username=None,
            status="active",
            role=ROLE_SPY if index <= 2 else "civilian",
            joined_at=datetime.now(timezone.utc),
        )
        for index in range(1, 23)
    )
    view = RoomView(
        room_id=1,
        status="finished",
        game_type="spy",
        join_code="ABC1234",
        join_token="safe-token",
        host_user_id=1,
        is_join_open=False,
        settings={},
        revision=1,
        actor_user_id=1,
        actor_member_id=1,
        actor_status="active",
        actor_role=ROLE_SPY,
        members=members,
        themes=(),
        game=SpyGameView(
            theme_name="Учёба",
            location_name="Школа",
            spy_count=2,
            spies_know_each_other=True,
            result="spies_win_reveal",
        ),
        vote=None,
        known_spy_names=(),
    )

    text, markup = RoomRenderer("testbot").render(view)

    assert "<b>■ Шпион — партия завершена</b>" in text
    assert "Роли:" not in text
    assert "Мирный" not in text
    assert "• Игрок 1" in text
    assert "• Игрок 2" in text
    assert "Игрок 3" not in text
    assert [
        (button.text, button.callback_data) for row in markup.inline_keyboard for button in row
    ] == [("🎮 К играм", "h:home")]


def test_player_list_is_vertical_and_limits_output_to_twenty() -> None:
    members = tuple(
        MemberView(
            id=index,
            user_id=index,
            display_name=f"Игрок {index}",
            username=None,
            status="active",
            role=None,
            joined_at=datetime.now(timezone.utc),
        )
        for index in range(1, 23)
    )

    lines = RoomRenderer._member_lines(members)

    assert lines[0] == "• Игрок 1"
    assert lines[19] == "• Игрок 20"
    assert lines[-1] == "… и ещё 2"
    assert len(lines) == 21
