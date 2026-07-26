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
import asyncio
import logging
import database

logger = logging.getLogger(__name__)

SIZE = 8

lobbies: dict[int, dict] = {}
games: dict[str, dict] = {}
chat_messages: dict[str, list[dict]] = {}
MAX_CHAT = 100

GAME_IDLE_TIMEOUT = 900  # 15 minutes in seconds
_game_timeout_tasks: dict[str, asyncio.Task] = {}


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


# Global Quick Matchmaking Queue
quick_queue: list[dict] = []


def lobby_text(chat_id: int) -> str:
    """Return the formatted lobby status message for a chat."""
    lobby = lobbies.get(chat_id)
    if not lobby:
        return (
            "⚫⚪ **Othello Match Arena** ⚪⚫\n\n"
            "• **Black (●)**: _Empty_\n"
            "• **White (○)**: _Empty_\n\n"
            "👇 Choose a slot or quick action below:"
        )

    b_name = lobby.get('black', {}).get('name') if lobby.get('black') else "_Empty_"
    w_name = lobby.get('white', {}).get('name') if lobby.get('white') else "_Empty_"

    status = "⏳ Waiting for opponent..."
    if lobby.get('black') and lobby.get('white'):
        status = "⚡ **Match Ready! Starting game...**"

    return (
        f"⚫⚪ **Othello Match Arena** ⚪⚫\n\n"
        f"• **Black (●)**: {b_name}\n"
        f"• **White (○)**: {w_name}\n\n"
        f"Status: {status}"
    )


def lobby_buttons(chat_id: int | None = None) -> list[list[dict]]:
    """Return the interactive inline keyboard for the lobby."""
    lobby = lobbies.get(chat_id) if chat_id else None
    has_b = bool(lobby and lobby.get('black'))
    has_w = bool(lobby and lobby.get('white'))

    b_btn_text = "⚫ Join Black (●)" if not has_b else "⚫ Black Joined ✅"
    w_btn_text = "⚪ Join White (○)" if not has_w else "⚪ White Joined ✅"

    return [
        [{"text": b_btn_text, "callback_data": "oth_slot_b"},
         {"text": w_btn_text, "callback_data": "oth_slot_w"}],
        [{"text": "⚡ Quick Match", "callback_data": "oth_quick"},
         {"text": "🤖 vs Bot AI", "callback_data": "oth_vs_ai"}],
        [{"text": "❌ Leave Slot", "callback_data": "oth_leave"},
         {"text": "🚪 Close Lobby", "callback_data": "oth_close"}]
    ]


def get_or_create_lobby(chat_id: int) -> dict:
    """Return the lobby for a chat, creating an empty one if it does not exist."""
    if chat_id not in lobbies:
        lobbies[chat_id] = {'black': None, 'white': None, 'created_at': time.time(), 'players': []}
    return lobbies[chat_id]


def lobby_add(chat_id: int, user_id: str, user_name: str) -> tuple[bool, str | None]:
    """Legacy helper: add player to first available slot in lobby."""
    lobby = get_or_create_lobby(chat_id)
    if (lobby.get('black') and lobby['black']['id'] == user_id) or (lobby.get('white') and lobby['white']['id'] == user_id):
        return False, "You're already in this lobby!"

    if not lobby.get('black'):
        lobby['black'] = {'id': user_id, 'name': user_name}
    elif not lobby.get('white'):
        lobby['white'] = {'id': user_id, 'name': user_name}
    else:
        return False, "Lobby is full!"

    # Sync legacy 'players' list
    lobby['players'] = [p for p in [lobby.get('black'), lobby.get('white')] if p]
    return True, None


def lobby_join_slot(chat_id: int, user_id: str, user_name: str, slot: str) -> tuple[bool, str | None]:
    """Join specific slot ('black' or 'white'). Returns (ok, error_message)."""
    lobby = get_or_create_lobby(chat_id)
    opp_slot = 'white' if slot == 'black' else 'black'

    # If player is in opposite slot, remove them first
    if lobby.get(opp_slot) and lobby[opp_slot]['id'] == user_id:
        lobby[opp_slot] = None

    if lobby.get(slot) and lobby[slot]['id'] != user_id:
        return False, f"Slot {slot.title()} is already taken!"

    lobby[slot] = {'id': user_id, 'name': user_name}
    lobby['players'] = [p for p in [lobby.get('black'), lobby.get('white')] if p]
    return True, None


def lobby_remove(chat_id: int, user_id: str) -> tuple[bool, str | None]:
    """Remove a player from the lobby. Returns (ok, error_message)."""
    lobby = lobbies.get(chat_id)
    if not lobby:
        return False, "No active lobby."

    removed = False
    if lobby.get('black') and lobby['black']['id'] == user_id:
        lobby['black'] = None
        removed = True
    if lobby.get('white') and lobby['white']['id'] == user_id:
        lobby['white'] = None
        removed = True

    lobby['players'] = [p for p in [lobby.get('black'), lobby.get('white')] if p]
    if not removed:
        return False, "You're not in this lobby."
    return True, None


async def check_match(chat_id: int) -> str | None:
    """If both Black and White slots are filled, create the game and clear lobby."""
    lobby = lobbies.get(chat_id)
    if not lobby or not lobby.get('black') or not lobby.get('white'):
        return None

    p1, p2 = lobby['black'], lobby['white']
    gid = await create_game(p1['id'], p1['name'], p2['id'], p2['name'])
    del lobbies[chat_id]
    await database.delete_othello_lobby(chat_id)
    return gid


async def join_quick_match(user_id: str, user_name: str, chat_id: int) -> tuple[str | None, dict | None]:
    """
    Join global quick matchmaking queue.
    Returns (game_id, matched_opponent_dict) if matched instantly, else (None, None).
    """
    global quick_queue
    # Filter out expired or duplicate entries
    quick_queue = [q for q in quick_queue if q['id'] != user_id and (time.time() - q['time']) < 300]

    if quick_queue:
        opp = quick_queue.pop(0)
        # Create game between opp and current user
        gid = await create_game(opp['id'], opp['name'], user_id, user_name)
        return gid, opp
    else:
        quick_queue.append({
            'id': user_id,
            'name': user_name,
            'chat_id': chat_id,
            'time': time.time()
        })
        return None, None


async def create_ai_game(user_id: str, user_name: str, color: str = 'b') -> str:
    """Create a new game against Othello Bot AI."""
    ai_player = {'id': 'bot_ai', 'name': '🤖 Othello Bot'}
    if color == 'b':
        return await create_game(user_id, user_name, ai_player['id'], ai_player['name'])
    else:
        return await create_game(ai_player['id'], ai_player['name'], user_id, user_name)


async def check_and_trigger_ai_move(gid: str) -> dict | None:
    """If current turn belongs to 'bot_ai', compute and execute the best move."""
    g = games.get(gid)
    if not g or g['game_over']:
        return None

    ai_color = None
    if g['black']['id'] == 'bot_ai':
        ai_color = 'b'
    elif g['white']['id'] == 'bot_ai':
        ai_color = 'w'

    if not ai_color or g['turn'] != ai_color:
        return None

    import othello_ai
    move = othello_ai.get_best_move(g['board'], ai_color)
    if move:
        r, c = move
        return await make_move(gid, 'bot_ai', r, c)
    return None



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
        'last_move_time': time.time(),
    }
    games[gid] = g
    await database.save_othello_game(gid, board, 'b', black_id, black_name, white_id, white_name)
    _schedule_game_timeout(gid)
    return gid


def _parse_json(val, default):
    """Safely parse JSON data if returned as a string by asyncpg."""
    if val is None:
        return default
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return val


def get_state(gid: str) -> dict | None:
    """Return the full game state dict for the frontend, or None if not found."""
    g = games.get(gid)
    if not g:
        return None
    b, w = counts(g['board'])
    valid = [] if g['game_over'] else valid_moves(g['board'], g['turn'])
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
    if str(g['black']['id']) == str(user_id) or str(user_id) == 'black':
        color = 'b'
    elif str(g['white']['id']) == str(user_id) or str(user_id) == 'white':
        color = 'w'
    if not color or g['turn'] != color:
        return None
    board = g['board']
    valid = valid_moves(board, color)
    if (r, c) not in valid:
        return None
    apply_move(board, r, c, color)
    g['last_move'] = (r, c)
    g['last_move_time'] = time.time()
    
    next_turn = 'w' if color == 'b' else 'b'
    next_valid = valid_moves(board, next_turn)
    
    if next_valid:
        g['turn'] = next_turn
    else:
        # Opponent has no legal moves -> check if current player can move
        same_valid = valid_moves(board, color)
        if same_valid:
            g['turn'] = color
        else:
            # Neither player has legal moves -> game over
            g['game_over'] = True
            b, w = counts(board)
            g['winner'] = 'draw' if b == w else ('black' if b > w else 'white')
            _cancel_game_timeout(gid)

    await database.save_othello_game(
        gid, board, g['turn'],
        g['black']['id'], g['black']['name'],
        g['white']['id'], g['white']['name'],
        g['game_over'], g['winner'], g['last_move']
    )
    if not g['game_over']:
        _schedule_game_timeout(gid)
        if user_id != 'bot_ai' and (g['black']['id'] == 'bot_ai' or g['white']['id'] == 'bot_ai'):
            asyncio.create_task(async_ai_step(gid))
    return get_state(gid)


async def async_ai_step(gid: str) -> None:
    """Execute AI move asynchronously with realistic thinking delay."""
    await asyncio.sleep(0.5)
    await check_and_trigger_ai_move(gid)


def _schedule_game_timeout(gid: str) -> None:
    """Schedule or reschedule the idle timeout for a game."""
    _cancel_game_timeout(gid)
    task = asyncio.create_task(_game_idle_worker(gid))
    _game_timeout_tasks[gid] = task


def _cancel_game_timeout(gid: str) -> None:
    task = _game_timeout_tasks.pop(gid, None)
    if task:
        task.cancel()


async def _game_idle_worker(gid: str) -> None:
    """Auto-forfeit the game if no move is made within GAME_IDLE_TIMEOUT seconds."""
    try:
        await asyncio.sleep(GAME_IDLE_TIMEOUT)
        g = games.get(gid)
        if not g or g['game_over']:
            return
        g['game_over'] = True
        # The player whose turn it is forfeits
        if g['turn'] == 'b':
            g['winner'] = 'white'
        else:
            g['winner'] = 'black'
        g['last_move'] = None
        await database.save_othello_game(
            gid, g['board'], g['turn'],
            g['black']['id'], g['black']['name'],
            g['white']['id'], g['white']['name'],
            g['game_over'], g['winner'], g['last_move']
        )
        logger.info("Game %s auto-forfeited after idle timeout (winner: %s)", gid, g['winner'])
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Game idle timeout error for %s: %s", gid, e)


def add_chat_message(gid: str, user_id: str, name: str, text: str) -> None:
    """Append a chat message to a game's message buffer (max MAX_CHAT entries)."""
    if gid not in chat_messages:
        chat_messages[gid] = []
    chat_messages[gid].append({
        'user_id': str(user_id),
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
        board = _parse_json(row['board'], new_board())
        last_move = _parse_json(row['last_move'], None)
        games[gid] = {
            'board': board,
            'turn': row['turn'],
            'black': {'id': str(row['black_id']), 'name': row['black_name']},
            'white': {'id': str(row['white_id']), 'name': row['white_name']},
            'game_over': bool(row['game_over']),
            'winner': row['winner'],
            'last_move': last_move,
            'last_move_time': time.time(),
        }
        if not row['game_over']:
            _schedule_game_timeout(gid)


async def restore_lobbies() -> None:
    """Reload lobbies from the database into the in-memory store."""
    rows = await database.load_othello_lobbies()
    for row in rows:
        players = _parse_json(row['players'], [])
        lobbies[row['chat_id']] = {'players': players}

