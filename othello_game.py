import secrets
import logging

SIZE = 8

waiting_queue = []
games = {}

def new_board():
    b = [[None]*SIZE for _ in range(SIZE)]
    b[3][3] = b[4][4] = 'w'
    b[3][4] = b[4][3] = 'b'
    return b

def valid_moves(board, color):
    opp = 'w' if color == 'b' else 'b'
    moves = []
    for r in range(SIZE):
        for c in range(SIZE):
            if board[r][c]:
                continue
            for dr, dc in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
                nr, nc = r+dr, c+dc
                found = False
                while 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == opp:
                    nr += dr; nc += dc; found = True
                if found and 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == color:
                    moves.append((r, c))
                    break
    return moves

def apply_move(board, r, c, color):
    opp = 'w' if color == 'b' else 'b'
    board[r][c] = color
    for dr, dc in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
        nr, nc = r+dr, c+dc
        to_flip = []
        while 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == opp:
            to_flip.append((nr, nc))
            nr += dr; nc += dc
        if to_flip and 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == color:
            for fr, fc in to_flip:
                board[fr][fc] = color

def counts(board):
    b = sum(row.count('b') for row in board)
    w = sum(row.count('w') for row in board)
    return b, w

def create_game(black_id, black_name, white_id, white_name):
    gid = secrets.token_hex(8)
    games[gid] = {
        'board': new_board(),
        'turn': 'b',
        'black': {'id': black_id, 'name': black_name},
        'white': {'id': white_id, 'name': white_name},
        'game_over': False,
        'winner': None,
        'last_move': None,
    }
    return gid

def get_state(gid):
    g = games.get(gid)
    if not g:
        return None
    b, w = counts(g['board'])
    valid = valid_moves(g['board'], g['turn'])
    if not valid:
        opp = 'w' if g['turn'] == 'b' else 'b'
        opp_valid = valid_moves(g['board'], opp)
        if not opp_valid:
            g['game_over'] = True
            g['winner'] = 'draw' if b == w else ('black' if b > w else 'white')
        else:
            g['turn'] = opp
            valid = valid_moves(g['board'], g['turn'])
    return {
        'board': g['board'],
        'turn': g['turn'],
        'black': g['black'],
        'white': g['white'],
        'black_score': b,
        'white_score': w,
        'game_over': g['game_over'],
        'winner': g['winner'],
        'last_move': g['last_move'],
        'valid_moves': [list(m) for m in valid],
    }

def make_move(gid, user_id, r, c):
    g = games.get(gid)
    if not g or g['game_over']:
        return None
    color = None
    if g['black']['id'] == user_id:
        color = 'b'
    elif g['white']['id'] == user_id:
        color = 'w'
    if not color or g['turn'] != color:
        return None
    board = g['board']
    valid = valid_moves(board, color)
    if (r, c) not in valid:
        return None
    apply_move(board, r, c, color)
    g['last_move'] = (r, c)
    g['turn'] = 'w' if color == 'b' else 'b'
    return get_state(gid)
