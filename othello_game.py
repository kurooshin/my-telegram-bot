import secrets
import json
import database

SIZE = 8

lobbies = {}
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

def lobby_text(chat_id):
    lobby = lobbies.get(chat_id)
    if not lobby or not lobby['players']:
        return "⚫ **Othello Lobby**\n\nNo players yet."
    body = "\n".join(f"{i+1}. {p['name']}" for i, p in enumerate(lobby['players']))
    return f"⚫ **Othello Lobby**\n\nPlayers ({len(lobby['players'])}/2):\n{body}"

def lobby_buttons():
    return [
        [{"text": "✅ Join", "callback_data": "oth_join"},
         {"text": "❌ Leave", "callback_data": "oth_leave"}]
    ]

def get_or_create_lobby(chat_id):
    if chat_id not in lobbies:
        lobbies[chat_id] = {'players': []}
    return lobbies[chat_id]

def lobby_add(chat_id, user_id, user_name):
    lobby = get_or_create_lobby(chat_id)
    if len(lobby['players']) >= 2:
        return False, "Lobby is full."
    if any(p['id'] == user_id for p in lobby['players']):
        return False, "You're already in the lobby."
    lobby['players'].append({'id': user_id, 'name': user_name})
    return True, None

def lobby_remove(chat_id, user_id):
    lobby = lobbies.get(chat_id)
    if not lobby:
        return False, "No lobby."
    before = len(lobby['players'])
    lobby['players'] = [p for p in lobby['players'] if p['id'] != user_id]
    if len(lobby['players']) == before:
        return False, "You're not in the lobby."
    return True, None

async def check_match(chat_id):
    lobby = lobbies.get(chat_id)
    if not lobby or len(lobby['players']) < 2:
        return None
    p1, p2 = lobby['players'][0], lobby['players'][1]
    gid = await create_game(p1['id'], p1['name'], p2['id'], p2['name'])
    del lobbies[chat_id]
    return gid

async def create_game(black_id, black_name, white_id, white_name):
    gid = secrets.token_hex(8)
    board = new_board()
    g = {
        'board': board,
        'turn': 'b',
        'black': {'id': black_id, 'name': black_name},
        'white': {'id': white_id, 'name': white_name},
        'game_over': False,
        'winner': None,
        'last_move': None,
    }
    games[gid] = g
    await database.save_othello_game(gid, board, 'b', black_id, black_name, white_id, white_name)
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

async def make_move(gid, user_id, r, c):
    g = games.get(gid)
    if not g or g['game_over']:
        return None
    color = None
    if g['black']['id'] == user_id or user_id == 'black':
        color = 'b'
    elif g['white']['id'] == user_id or user_id == 'white':
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

    b, w = counts(board)
    if not valid_moves(board, g['turn']):
        opp = 'w' if g['turn'] == 'b' else 'b'
        if not valid_moves(board, opp):
            g['game_over'] = True
            g['winner'] = 'draw' if b == w else ('black' if b > w else 'white')

    await database.save_othello_game(
        gid, board, g['turn'],
        g['black']['id'], g['black']['name'],
        g['white']['id'], g['white']['name'],
        g['game_over'], g['winner'], g['last_move']
    )
    return get_state(gid)

async def restore_games():
    rows = await database.load_othello_games()
    for row in rows:
        gid = row['game_id']
        games[gid] = {
            'board': row['board'],
            'turn': row['turn'],
            'black': {'id': row['black_id'], 'name': row['black_name']},
            'white': {'id': row['white_id'], 'name': row['white_name']},
            'game_over': row['game_over'],
            'winner': row['winner'],
            'last_move': row['last_move'],
        }

async def restore_lobbies():
    rows = await database.load_othello_lobbies()
    for row in rows:
        lobbies[row['chat_id']] = {'players': row['players']}
