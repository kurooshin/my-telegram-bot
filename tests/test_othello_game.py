"""Tests for othello_game.py pure logic (valid_moves, apply_move, counts, new_board)."""

from othello_game import new_board, valid_moves, apply_move, counts, SIZE


def test_new_board():
    board = new_board()
    assert len(board) == SIZE
    assert len(board[0]) == SIZE
    # Starting position
    assert board[3][3] == 'w'
    assert board[4][4] == 'w'
    assert board[3][4] == 'b'
    assert board[4][3] == 'b'
    # All others are None
    assert board[0][0] is None
    assert board[7][7] is None


def test_initial_counts():
    board = new_board()
    b, w = counts(board)
    assert b == 2
    assert w == 2


def test_valid_moves_black_first():
    board = new_board()
    moves = valid_moves(board, 'b')
    assert sorted(moves) == [(2, 3), (3, 2), (4, 5), (5, 4)]


def test_valid_moves_white_initial():
    board = new_board()
    moves = valid_moves(board, 'w')
    assert sorted(moves) == [(2, 4), (3, 5), (4, 2), (5, 3)]


def test_apply_move_flip():
    board = new_board()
    apply_move(board, 2, 3, 'b')
    # The piece at (3, 3) should be flipped from 'w' to 'b'
    assert board[2][3] == 'b'
    assert board[3][3] == 'b'
    b, w = counts(board)
    assert b == 4
    assert w == 1


def test_valid_moves_after_move():
    board = new_board()
    apply_move(board, 2, 3, 'b')
    moves = valid_moves(board, 'w')
    # White should have valid moves after black's move
    assert len(moves) > 0


def test_no_valid_moves_on_full_board():
    board = [['b'] * SIZE for _ in range(SIZE)]
    moves = valid_moves(board, 'w')
    assert moves == []


def test_apply_move_no_flip_corner():
    """Placing in an empty corner with no adjacent opponent pieces makes no flip."""
    board = [[None] * SIZE for _ in range(SIZE)]
    board[0][1] = 'w'
    board[1][0] = 'w'
    board[0][0] = 'b'
    moves = valid_moves(board, 'b')
    assert (0, 0) not in moves  # Already occupied
    apply_move(board, 7, 7, 'b')
    assert board[7][7] == 'b'
    b, w = counts(board)
    assert b == 2
    assert w == 2


def test_out_of_bounds_move_not_valid():
    board = new_board()
    moves = valid_moves(board, 'b')
    for r, c in moves:
        assert 0 <= r < SIZE
        assert 0 <= c < SIZE


import pytest
import asyncio
from unittest.mock import AsyncMock, patch
import othello_game
import ratelimit
import database


@pytest.mark.asyncio
async def test_create_and_make_move():
    with patch("database.save_othello_game", new_callable=AsyncMock):
        gid = await othello_game.create_game("p1", "Player 1", "p2", "Player 2")
        assert gid in othello_game.games
        
        state = othello_game.get_state(gid)
        assert state["turn"] == "b"
        assert state["black_score"] == 2
        assert state["white_score"] == 2
        assert not state["game_over"]

        # Player 1 (black) makes a valid move at (2, 3)
        new_state = await othello_game.make_move(gid, "p1", 2, 3)
        assert new_state is not None
        assert new_state["turn"] == "w"
        assert new_state["black_score"] == 4
        assert new_state["white_score"] == 1

        # Invalid move by player 1 when it's player 2's turn
        invalid_state = await othello_game.make_move(gid, "p1", 2, 4)
        assert invalid_state is None


def test_chat_messages():
    gid = "test_chat_gid"
    othello_game.chat_messages.pop(gid, None)
    
    othello_game.add_chat_message(gid, "p1", "Player 1", "Hello world!")
    msgs = othello_game.get_chat_messages(gid)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "Hello world!"
    assert msgs[0]["name"] == "Player 1"


def test_rate_limiter():
    key = "test_rate_user"
    ratelimit._limits.pop(key, None)
    
    # 5 allowed calls
    for _ in range(5):
        assert ratelimit.check_rate_limit(key, max_calls=5, window=10.0) is True
    
    # 6th call should be rate limited
    assert ratelimit.check_rate_limit(key, max_calls=5, window=10.0) is False


def test_normalize_persian():
    raw = "عليك"
    normalized = database.normalize_persian(raw)
    assert "ي" not in normalized
    assert "ك" not in normalized


def test_slot_lobby():
    chat_id = 999111
    othello_game.lobbies.pop(chat_id, None)

    # Join Black
    ok, err = othello_game.lobby_join_slot(chat_id, "u1", "User 1", "black")
    assert ok is True
    lobby = othello_game.lobbies[chat_id]
    assert lobby["black"]["id"] == "u1"

    # Join White
    ok, err = othello_game.lobby_join_slot(chat_id, "u2", "User 2", "white")
    assert ok is True
    assert lobby["white"]["id"] == "u2"


@pytest.mark.asyncio
async def test_quick_matchmaking():
    othello_game.quick_queue.clear()

    with patch("database.save_othello_game", new_callable=AsyncMock):
        # User 1 joins queue -> no match yet
        gid1, opp1 = await othello_game.join_quick_match("q1", "Queuer 1", 100)
        assert gid1 is None

        # User 2 joins queue -> instantly matched!
        gid2, opp2 = await othello_game.join_quick_match("q2", "Queuer 2", 200)
        assert gid2 is not None
        assert opp2["id"] == "q1"


def test_ai_solver():
    import othello_ai
    board = othello_game.new_board()
    best_m = othello_ai.get_best_move(board, 'b')
    assert best_m in [(2, 3), (3, 2), (4, 5), (5, 4)]




# ---- resign ----
import asyncio

import othello_game


def _make_game(monkeypatch, black=("111", "Ali"), white=("222", "Sara")):
    saved = []

    async def fake_save(*args, **kwargs):
        saved.append(args)

    monkeypatch.setattr(othello_game.database, "save_othello_game", fake_save)
    # No event loop timers in unit tests
    monkeypatch.setattr(othello_game, "_schedule_game_timeout", lambda gid: None)

    async def go():
        return await othello_game.create_game(black[0], black[1], white[0], white[1])

    gid = asyncio.run(go())
    return gid, saved


def test_resign_gives_win_to_opponent(monkeypatch):
    gid, saved = _make_game(monkeypatch)
    state = asyncio.run(othello_game.resign(gid, "111"))   # black resigns
    assert state["game_over"] is True
    assert state["winner"] == "white"
    assert state["end_reason"] == "resign"
    assert saved[-1][7] is True and saved[-1][8] == "white"   # persisted
    othello_game.games.pop(gid, None)


def test_white_resign_gives_win_to_black(monkeypatch):
    gid, _ = _make_game(monkeypatch)
    state = asyncio.run(othello_game.resign(gid, "222"))
    assert state["winner"] == "black"
    othello_game.games.pop(gid, None)


def test_resign_rejects_non_player_and_finished_game(monkeypatch):
    gid, _ = _make_game(monkeypatch)
    assert asyncio.run(othello_game.resign(gid, "333")) is None      # stranger
    assert asyncio.run(othello_game.resign("nope", "111")) is None   # unknown game
    assert asyncio.run(othello_game.resign(gid, "111")) is not None
    assert asyncio.run(othello_game.resign(gid, "222")) is None      # already over
    othello_game.games.pop(gid, None)


def test_no_moves_after_resign(monkeypatch):
    gid, _ = _make_game(monkeypatch)
    asyncio.run(othello_game.resign(gid, "222"))
    assert asyncio.run(othello_game.make_move(gid, "111", 2, 3)) is None
    othello_game.games.pop(gid, None)
