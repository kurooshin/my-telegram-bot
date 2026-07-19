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
