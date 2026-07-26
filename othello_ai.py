"""
Othello AI Engine — Positional & Mobility Heuristic Move Solver.
"""

from othello_game import SIZE, valid_moves, apply_move, counts

# Positional weight grid for 8x8 Othello
WEIGHTS = [
    [100, -20,  10,   5,   5,  10, -20, 100],
    [-20, -50,  -2,  -2,  -2,  -2, -50, -20],
    [ 10,  -2,   5,   1,   1,   5,  -2,  10],
    [  5,  -2,   1,   1,   1,   1,  -2,   5],
    [  5,  -2,   1,   1,   1,   1,  -2,   5],
    [ 10,  -2,   5,   1,   1,   5,  -2,  10],
    [-20, -50,  -2,  -2,  -2,  -2, -50, -20],
    [100, -20,  10,   5,   5,  10, -20, 100],
]


def evaluate_board(board: list[list[str | None]], color: str) -> float:
    """Evaluate board position score from the perspective of `color`."""
    opp = 'w' if color == 'b' else 'b'
    score = 0.0
    
    # 1. Positional values
    for r in range(SIZE):
        for c in range(SIZE):
            cell = board[r][c]
            if cell == color:
                score += WEIGHTS[r][c]
            elif cell == opp:
                score -= WEIGHTS[r][c]
                
    # 2. Corner dynamics adjustments
    corners = [(0, 0), (0, 7), (7, 0), (7, 7)]
    for cr, cc in corners:
        if board[cr][cc] == color:
            score += 50
        elif board[cr][cc] == opp:
            score -= 50

    # 3. Piece count & mobility
    my_count, opp_count = counts(board) if color == 'b' else counts(board)[::-1]
    my_moves = len(valid_moves(board, color))
    opp_moves = len(valid_moves(board, opp))
    
    score += (my_moves - opp_moves) * 5
    score += (my_count - opp_count) * 2

    return score


def get_best_move(board: list[list[str | None]], color: str) -> tuple[int, int] | None:
    """Return the best move (row, col) for `color` on `board`."""
    moves = valid_moves(board, color)
    if not moves:
        return None

    best_score = float('-inf')
    best_move = moves[0]

    for r, c in moves:
        # Create a deep copy to simulate
        sim_board = [row[:] for row in board]
        apply_move(sim_board, r, c, color)
        val = evaluate_board(sim_board, color)
        if val > best_score:
            best_score = val
            best_move = (r, c)

    return best_move
