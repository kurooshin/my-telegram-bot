"""
Othello game logic — board operations, lobby management, and chat buffer.

CONSTRAINT: All state (games, lobbies, chat_messages) is kept in in-process
Python dicts. This works for a single server process only. If the bot is scaled
to multiple workers/dynos, state becomes inconsistent. To scale horizontally,
migrate to Redis or Postgres-backed state (e.g., store active games in the
existing othello_games table and load on every request, or use Redis pub/sub).
"""
import secrets
import time
import database

SIZE = 8

lobbies: dict[int, dict] = {}
games: dict[str, dict] = {}
chat_messages: dict[str, list[dict]] = {}
MAX_CHAT = 100


def new_board() -> list[list[str | None]]:
    """Return a new 8x8 board with the standard Othello starting position."""
    b = [[None] * SIZE for _ in range(SIZE)]
    b[3][3] = b[4][4] = 'w'
    b[3][4] = b[4][3] = 'b'
    return b


def valid_moves(board: list[list[str | None]], color: str) -> list[tuple[int, int]]:
    """Return list of (row, col) tuples where `color` ('b'/'w') can legally place a piece."""
    opp = 'w' if color == 'b' else 'b'
    moves = []
    for r in range(SIZE):
        for c in range(SIZE):
            if board[r][c]:
                continue
            for dr, dc in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
                nr, nc = r + dr, c + dc
                found = False
                while 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == opp:
                    nr += dr
                    nc += dc
                    found = True
                if found and 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == color:
                    moves.append((r, c))
                    break
    return moves


def apply_move(board: list[list[str | None]], r: int, c: int, color: str) -> None:
    """Place `color` at (r,c) and flip opponent pieces. Mutates board in-place."""
    opp = 'w' if color == 'b' else 'b'
    board[r][c] = color
    for dr, dc in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
        nr, nc = r + dr, c + dc
        to_flip = []
        while 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == opp:
            to_flip.append((nr, nc))
            nr += dr
            nc += dc
        if to_flip and 0 <= nr < SIZE and 0 <= nc < SIZE and board[nr][nc] == color:
            for fr, fc in to_flip:
                board[fr][fc] = color


def counts(board: list[list[str | None]]) -> tuple[int, int]:
    """Return (black_count, white_count) for the given board."""
    b = sum(row.count('b') for row in board)
    w = sum(row.count('w') for row in board)
    return b, w


def lobby_text(chat_id: int) -> str:
    """Return the formatted lobby status message for a chat."""
    lobby = lobbies.get(chat_id)
    if not lobby or not lobby['players']:
        return "⚫ **Othello Lobby**\n\nNo players yet."
    body = "\n".join(f"{i+1}. {p['name']}" for i, p in enumerate(lobby['players']))
    return f"⚫ **Othello Lobby**\n\nPlayers ({len(lobby['players'])}):\n{body}"


def lobby_buttons() -> list[list[dict]]:
    """Return the Join/Leave inline keyboard."""
    return [
        [{"text": "✅ Join", "callback_data": "oth_join"},
         {"text": "❌ Leave", "callback_data": "oth_leave"}]
    ]


def get_or_create_lobby(chat_id: int) -> dict:
    """Return the lobby for a chat, creating an empty one if it does not exist."""
    if chat_id not in lobbies:
        lobbies[chat_id] = {'players': []}
    return lobbies[chat_id]


def lobby_add(chat_id: int, user_id: str, user_name: str) -> tuple[bool, str | None]:
    """Add a player to the lobby. Returns (ok, error_message)."""
    lobby = get_or_create_lobby(chat_id)
    if any(p['id'] == user_id for p in lobby['players']):
        return False, "You're already in the lobby."
    lobby['players'].append({'id': user_id, 'name': user_name})
    return True, None


def lobby_remove(chat_id: int, user_id: str) -> tuple[bool, str | None]:
    """Remove a player from the lobby. Returns (ok, error_message)."""
    lobby = lobbies.get(chat_id)
    if not lobby:
        return False, "No lobby."
    before = len(lobby['players'])
    lobby['players'] = [p for p in lobby['players'] if p['id'] != user_id]
    if len(lobby['players']) == before:
        return False, "You're not in the lobby."
    return True, None


async def check_match(chat_id: int) -> str | None:
    """If 2+ players are in the lobby, pair the first two and return the game ID. Removes them from the lobby."""
    lobby = lobbies.get(chat_id)
    if not lobby or len(lobby['players']) < 2:
        return None
    p1, p2 = lobby['players'][0], lobby['players'][1]
    gid = await create_game(p1['id'], p1['name'], p2['id'], p2['name'])
    lobby['players'] = [p for p in lobby['players'] if p['id'] != p1['id'] and p['id'] != p2['id']]
    return gid


async def create_game(black_id: str, black_name: str, white_id: str, white_name: str) -> str:
    """Create a new Othello game and persist to database. Returns the game ID."""
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


def get_state(gid: str) -> dict | None:
    """Return the full game state dict for the frontend, or None if not found."""
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


async def make_move(gid: str, user_id: str, r: int, c: int) -> dict | None:
    """Process a move and return the updated state. Returns None if the move is invalid."""
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


def add_chat_message(gid: str, user_id: str, name: str, text: str) -> None:
    """Append a chat message to a game's message buffer (max MAX_CHAT entries)."""
    if gid not in chat_messages:
        chat_messages[gid] = []
    chat_messages[gid].append({
        'user_id': user_id,
        'name': name,
        'text': text[:500],
        'ts': int(time.time())
    })
    if len(chat_messages[gid]) > MAX_CHAT:
        chat_messages[gid] = chat_messages[gid][-MAX_CHAT:]


def get_chat_messages(gid: str) -> list[dict]:
    """Return all chat messages for a game."""
    return chat_messages.get(gid, [])


async def restore_games() -> None:
    """Reload unfinished games from the database into the in-memory store."""
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


async def restore_lobbies() -> None:
    """Reload lobbies from the database into the in-memory store."""
    rows = await database.load_othello_lobbies()
    for row in rows:
        lobbies[row['chat_id']] = {'players': row['players']}
