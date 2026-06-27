import datetime
import os
import traceback
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit, join_room

# === PASSWORD HASHING ===
from werkzeug.security import generate_password_hash, check_password_hash

import chess
from database import get_db, init_db


def log_move_debug(msg):
    try:
        with open(
            os.path.join(os.path.dirname(__file__), "debug_moves.log"),
            "a",
            encoding="utf-8",
        ) as f:
            f.write(f"[{datetime.datetime.utcnow().isoformat()}] {msg}\n")
    except Exception:
        pass


app = Flask(__name__)
app.secret_key = "zedchess-secret-key"

# SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)
connected_users = {}

connected_users = {}


@socketio.on("connect")
def handle_connect():
    if "user_id" in session:
        connected_users[session["user_id"]] = session["username"]

        join_room(f"user_{session['user_id']}")

        socketio.emit("online_users", list(connected_users.values()))


# =========================
# INIT DATABASE
# =========================
with app.app_context():
    init_db()


# =========================
# LOGIN REQUIRED
# =========================
def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


# =========================
# ROUTES
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        db = get_db()
        
        user = db.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            connected_users[session["user_id"]] = session["username"]
            db.execute("UPDATE users SET online=TRUE WHERE id=?", (user["id"],))
            db.commit()
            return redirect(url_for("lobby"))
        
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]
        accepted_terms = request.form.get("accepted_terms") == "on"

        if not accepted_terms:
            flash("You must accept the Terms and Conditions and Privacy Policy to sign up.")
            return render_template("signup.html")

        # === PASSWORD STRENGTH VALIDATION ===
        if len(password) < 6:
            flash("Password must be at least 6 characters long.")
            return render_template("signup.html")
        
        if not any(char.isdigit() for char in password):
            flash("Password must contain at least one number.")
            return render_template("signup.html")
        
        if not any(char.isalpha() for char in password):
            flash("Password must contain at least one letter.")
            return render_template("signup.html")

        db = get_db()
        try:
            hashed_password = generate_password_hash(password)
            
            db.execute(
                "INSERT INTO users (username, email, password, accepted_terms) VALUES (?, ?, ?, ?)",
                (username, email, hashed_password, int(accepted_terms)),
            )
            db.commit()
            flash("Account created successfully! Please login.")
            return redirect(url_for("login"))
        except Exception:
            flash("Username or email already exists")
    return render_template("signup.html")
@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/lobby")
@login_required
def lobby():
    db = get_db()
    search = request.args.get("search", "").strip()

    query_params = [session["user_id"]]
    query_sql = """
        SELECT id, username
        FROM users
        WHERE online = 1
        AND id != ?
    """

    if search:
        query_sql += " AND LOWER(username) LIKE ?"
        query_params.append(f"%{search.lower()}%")

    online_players = db.execute(query_sql, tuple(query_params)).fetchall()

    challenges = db.execute("""
        SELECT challenges.*,
               users.username AS creator_name
        FROM challenges
        JOIN users ON challenges.creator_id = users.id
        WHERE challenges.status='waiting'
    """).fetchall()

    incoming_challenges = db.execute(
        """
        SELECT dc.id,
               u.username AS challenger_name,
               dc.stake
        FROM direct_challenges dc
        JOIN users u ON dc.challenger_id = u.id
        WHERE dc.challenged_id = ?
        AND dc.status='pending'
    """,
        (session["user_id"],),
    ).fetchall()

    return render_template(
        "lobby.html",
        online_players=online_players,
        challenges=challenges,
        incoming_challenges=incoming_challenges,
        search=search,
    )


@app.route("/create_challenge", methods=["POST"])
@login_required
def create_challenge():
    stake = max(float(request.form["stake"]), 1.0)
    db = get_db()
    user = db.execute(
        "SELECT wallet FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    if not user or user["wallet"] < stake:
        flash("Not enough wallet balance to create that challenge.")
        return redirect(url_for("lobby"))

    db.execute(
        "UPDATE users SET wallet = wallet - ? WHERE id=?", (stake, session["user_id"])
    )
    db.execute(
        "INSERT INTO challenges (creator_id, stake) VALUES (?, ?)",
        (session["user_id"], stake),
    )
    db.commit()
    flash("Open challenge successfully created.")
    return redirect(url_for("lobby"))


@app.route("/send_challenge/<int:user_id>", methods=["POST"])
@login_required
def send_challenge(user_id):
    stake = max(float(request.form.get("stake", 10)), 1.0)
    db = get_db()

    if user_id == session["user_id"]:
        flash("You cannot challenge yourself.")
        return redirect(url_for("lobby"))

    existing = db.execute(
        """
        SELECT id FROM direct_challenges
        WHERE challenger_id=?
        AND challenged_id=?
        AND status='pending'
    """,
        (session["user_id"], user_id),
    ).fetchone()

    if existing:
        flash("Challenge already sent.")
        return redirect(url_for("lobby"))

    user = db.execute(
        "SELECT wallet FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    if not user or user["wallet"] < stake:
        flash("Not enough wallet balance to send that challenge.")
        return redirect(url_for("lobby"))

    db.execute(
        "UPDATE users SET wallet = wallet - ? WHERE id=?", (stake, session["user_id"])
    )
    db.execute(
        """
        INSERT INTO direct_challenges
        (challenger_id, challenged_id, stake)
        VALUES (?, ?, ?)
    """,
        (session["user_id"], user_id, stake),
    )

    db.commit()

    socketio.emit("new_challenge", {"from": session["username"]}, to=f"user_{user_id}")
    flash("Challenge sent!")
    return redirect(url_for("lobby"))


@app.route("/invite_player", methods=["POST"])
@login_required
def invite_player():
    target_username = request.form.get("target_username", "").strip()
    stake = max(float(request.form.get("stake", 10)), 1.0)
    db = get_db()

    if not target_username:
        flash("Please enter a username to invite.")
        return redirect(url_for("lobby"))

    if target_username == session["username"]:
        flash("You cannot invite yourself.")
        return redirect(url_for("lobby"))

    target_user = db.execute(
        "SELECT id, online FROM users WHERE username=?",
        (target_username,),
    ).fetchone()

    if not target_user:
        flash("User not found.")
        return redirect(url_for("lobby"))

    if not target_user["online"]:
        flash("User must be online to invite.")
        return redirect(url_for("lobby"))

    existing = db.execute(
        "SELECT id FROM direct_challenges WHERE challenger_id=? AND challenged_id=? AND status='pending'",
        (session["user_id"], target_user["id"]),
    ).fetchone()
    if existing:
        flash("You already sent a challenge to that user.")
        return redirect(url_for("lobby"))

    user = db.execute(
        "SELECT wallet FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    if not user or user["wallet"] < stake:
        flash("Not enough wallet balance to send that invite.")
        return redirect(url_for("lobby"))

    db.execute(
        "UPDATE users SET wallet = wallet - ? WHERE id=?", (stake, session["user_id"])
    )
    db.execute(
        "INSERT INTO direct_challenges (challenger_id, challenged_id, stake) VALUES (?, ?, ?)",
        (session["user_id"], target_user["id"], stake),
    )
    db.commit()

    socketio.emit("new_challenge", {"from": session["username"]}, to=f"user_{target_user['id']}")
    flash(f"Invite sent to {target_username}.")
    return redirect(url_for("lobby"))


@app.route("/accept_challenge/<int:challenge_id>", methods=["POST"])
@login_required
def accept_challenge(challenge_id):
    db = get_db()
    
    challenge = db.execute(
        "SELECT * FROM challenges WHERE id=? AND status='waiting'",
        (challenge_id,),
    ).fetchone()
    if not challenge:
        flash("Challenge no longer available. It may have already been accepted or canceled.")
        return redirect(url_for("lobby"))

    if challenge["creator_id"] == session["user_id"]:
        flash("You cannot accept your own open challenge.")
        return redirect(url_for("lobby"))

    stake = challenge["stake"]
    acceptor = db.execute(
        "SELECT wallet FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    if not acceptor or acceptor["wallet"] < stake:
        flash("Not enough balance to accept this challenge.")
        return redirect(url_for("lobby"))

    db.execute(
        "UPDATE users SET wallet = wallet - ? WHERE id=?", (stake, session["user_id"])
    )

    white_id = challenge["creator_id"]
    black_id = session["user_id"]
    total_pot = stake * 2

    db.execute(
        """
        INSERT INTO games (player1_id, player2_id, bet, status, board_state, current_turn, white_time, black_time)
        VALUES (?, ?, ?, 'active', ?, 'white', 600, 600)
    """,
        (white_id, black_id, total_pot, chess.STARTING_FEN),
    )

    game_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    if not game_id:
        row = db.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone()
        game_id = row[0] if row else None

    if not game_id:
        flash("Failed to create game.")
        return redirect(url_for("lobby"))

    db.execute("UPDATE challenges SET status='accepted' WHERE id=?", (challenge_id,))
    db.commit()

    socketio.emit(
        "challenge_accepted",
        {"from": session["username"], "game_id": game_id},
        to=f"user_{white_id}",
    )
    
    # FIXED BROADCAST
    socketio.emit("lobby_refresh", {}, to=None)

    return redirect(url_for("game", game_id=game_id))
@app.route("/accept_direct_challenge/<int:challenge_id>", methods=["POST"])
@login_required
def accept_direct_challenge(challenge_id):
    db = get_db()

    challenge = db.execute(
        """
        SELECT *
        FROM direct_challenges
        WHERE id=?
        AND status='pending'
    """,
        (challenge_id,),
    ).fetchone()

    if not challenge or challenge["status"] != "pending":
        flash("Challenge not found or already resolved.")
        return redirect(url_for("lobby"))

    if challenge["challenged_id"] != session["user_id"]:
        flash("This direct challenge is not addressed to you.")
        return redirect(url_for("lobby"))

    stake = challenge["stake"] if "stake" in challenge.keys() else 10
    acceptor = db.execute(
        "SELECT wallet FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    if not acceptor or acceptor["wallet"] < stake:
        flash("Not enough balance to accept this direct challenge.")
        return redirect(url_for("lobby"))

    db.execute(
        "UPDATE users SET wallet = wallet - ? WHERE id=?", (stake, session["user_id"])
    )

    white_id = session["user_id"]
    black_id = challenge["challenger_id"]
    total_pot = stake * 2

    db.execute(
        """
        INSERT INTO games
        (
            player1_id,
            player2_id,
            bet,
            status,
            board_state,
            current_turn,
            white_time,
            black_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            white_id,
            black_id,
            total_pot,
            "active",
            chess.STARTING_FEN,
            "white",
            600,
            600,
        ),
    )

    # FIXED: Reliable way to get the new game ID
    game_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Extra safety fallback
    if not game_id:
        row = db.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone()
        game_id = row[0] if row else None

    if not game_id:
        flash("Failed to create game.")
        return redirect(url_for("lobby"))

    db.execute(
        """
        UPDATE direct_challenges
        SET status='accepted'
        WHERE id=?
    """,
        (challenge_id,),
    )

    db.commit()
    socketio.emit(
        "challenge_accepted",
        {"from": session["username"], "game_id": game_id},
        to=f"user_{challenge['challenger_id']}",
    )

    return redirect(url_for("game", game_id=game_id))
@app.route("/decline_direct_challenge/<int:challenge_id>", methods=["POST"])
@login_required
def decline_direct_challenge(challenge_id):
    db = get_db()

    challenge = db.execute(
        "SELECT * FROM direct_challenges WHERE id=?", (challenge_id,)
    ).fetchone()
    if challenge and challenge["status"] == "pending":
        db.execute(
            "UPDATE users SET wallet = wallet + ? WHERE id=?",
            (challenge["stake"], challenge["challenger_id"]),
        )
        db.execute(
            "UPDATE direct_challenges SET status='declined' WHERE id=?", (challenge_id,)
        )
        db.commit()
        
        # FIXED: Correct SocketIO broadcast
    socketio.emit("lobby_refresh", {}, broadcast=True, namespace='/')

    flash("Challenge declined.")
    return redirect(url_for("lobby"))

@app.route("/game/<int:game_id>")
@login_required
def game(game_id):
    db = get_db()
    game = db.execute(
        """
        SELECT games.*, p1.username AS p1_name, p2.username AS p2_name
        FROM games
        JOIN users p1 ON games.player1_id = p1.id
        JOIN users p2 ON games.player2_id = p2.id
        WHERE games.id = ?
    """,
        (game_id,),
    ).fetchone()

    if not game or game["status"] != "active":
        flash("Game not found or finished")
        return redirect(url_for("lobby"))

    return render_template("game.html", game=game)


@app.route("/profile")
@login_required
def profile():
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    return render_template("profile.html", user=user)


@app.route("/add_money", methods=["POST"])
@login_required
def add_money():
    amount = float(request.form["amount"])
    db = get_db()
    db.execute(
        "UPDATE users SET wallet = wallet + ? WHERE id=?", (amount, session["user_id"])
    )
    db.commit()
    return redirect(url_for("profile"))


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        db = get_db()
        db.execute("UPDATE users SET online=FALSE WHERE id=?", (user_id,))
        db.commit()
        connected_users.pop(user_id, None)
    session.clear()
    return redirect(url_for("index"))


@app.route("/cancel_challenge/<int:challenge_id>", methods=["POST"])
@login_required
def cancel_challenge(challenge_id):
    db = get_db()
    challenge = db.execute(
        "SELECT * FROM challenges WHERE id=? AND creator_id=?",
        (challenge_id, session["user_id"]),
    ).fetchone()
    if challenge:
        db.execute(
            "UPDATE users SET wallet = wallet + ? WHERE id=?",
            (challenge["stake"], session["user_id"]),
        )
        db.execute(
            "DELETE FROM challenges WHERE id=? AND creator_id=?",
            (challenge_id, session["user_id"]),
        )
        db.commit()
    return redirect(url_for("lobby"))


@app.route("/forfeit/<int:game_id>", methods=["POST"])
@login_required
def forfeit(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not game or game["status"] != "active":
        return redirect(url_for("lobby"))

    user_id = session.get("user_id")
    if user_id not in (game["player1_id"], game["player2_id"]):
        return redirect(url_for("lobby"))

    if game["player1_id"] == user_id:
        winner_id = game["player2_id"]
        loser_color = "White"
    else:
        winner_id = game["player1_id"]
        loser_color = "Black"

    db.execute(
        "UPDATE users SET wallet = wallet + ? WHERE id=?", (game["bet"], winner_id)
    )
    db.execute(
        "UPDATE games SET status='finished', winner_id=? WHERE id=?",
        (winner_id, game_id),
    )
    db.commit()

    socketio.emit(
        "game_over",
        {
            "reason": f"{loser_color} forfeited",
            "loser_id": user_id,
            "winner_id": winner_id,
            "bet": game["bet"],
        },
        room=f"game_{game_id}",
    )
    flash("Game over. You have forfeited the game, you lose!")
    return redirect(url_for("lobby"))


# =========================
# SOCKET.IO EVENTS - FULL MOVE LOGIC
# =========================
@socketio.on("join_game")
def on_join(data):
    user = session.get("user_id")
    room = f"game_{data['game_id']}"
    print(f"join_game: user={user} joining room={room}")
    join_room(room)


@socketio.on("make_move")
def on_make_move(data):
    game_id = data["game_id"]
    uci_move = data["move"]
    entry_msg = f"on_make_move called: user={session.get('user_id')} game={game_id} move={uci_move} data={data}"
    print(entry_msg)
    log_move_debug(entry_msg)

    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()

    if not game or game["status"] != "active":
        emit("invalid_move", {"reason": "Game not active or not found."})
        log_move_debug(f"Game not active or found for game_id={game_id}")
        return {"error": "Game not active or not found."}

    user_id = session.get("user_id")
    if user_id not in (game["player1_id"], game["player2_id"]):
        emit("invalid_move", {"reason": "You are not part of this game."})
        log_move_debug(f"User {user_id} not part of game {game_id}")
        return {"error": "You are not part of this game."}

    # show the stored board state for debugging
    try:
        print(f"Stored board_state (FEN): {game['board_state']}")
    except Exception:
        print("Could not read game['board_state']")

    board = chess.Board(game["board_state"])
    try:
        legal_uci = [m.uci() for m in board.legal_moves]
        print(f"Legal moves (sample {min(20,len(legal_uci))}): {legal_uci[:20]}")
    except Exception as e:
        print("Error enumerating legal moves:", type(e).__name__, e)

    # ✅ Determine player color
    is_white = game["player1_id"] == user_id
    player_color = "white" if is_white else "black"

    # ❌ Not your turn
    if game["current_turn"] != player_color:
        emit("invalid_move", {"reason": "Not your turn."})
        return {"error": "Not your turn."}

    try:
        move = chess.Move.from_uci(uci_move)
        parsed_msg = f"Parsed move: {move} from_square={move.from_square} to_square={move.to_square}"
        print(parsed_msg)
        log_move_debug(parsed_msg)

        # ❌ illegal move
        if move not in board.legal_moves:
            emit("invalid_move", {"reason": "Illegal move"})
            msg = f"illegal move attempted by user={user_id}: {uci_move} on FEN {board.fen()}"
            print(msg)
            log_move_debug(msg)
            return {"error": "Illegal move"}

        piece = board.piece_at(move.from_square)
        piece_msg = f"Piece at source: {piece}"
        print(piece_msg)
        log_move_debug(piece_msg)

        if not piece:
            emit("invalid_move", {"reason": "No piece at source square"})
            print(f"no piece at from_square for user={user_id}: {uci_move}")
            return {"error": "No piece at source square"}

        if piece.color == chess.WHITE and not is_white:
            emit("invalid_move", {"reason": "Not your piece (white)"})
            print(f"wrong piece color attempted by user={user_id}: {uci_move}")
            return {"error": "Not your piece"}

        if piece.color == chess.BLACK and is_white:
            emit("invalid_move", {"reason": "Not your piece (black)"})
            print(f"wrong piece color attempted by user={user_id}: {uci_move}")
            return {"error": "Not your piece"}

        # handle timers: subtract elapsed seconds if provided
        elapsed = (
            int(data.get("elapsed", 0))
            if isinstance(data, dict) and "elapsed" in data
            else 0
        )
        if elapsed > 0:
            if player_color == "white":
                remaining = max((game["white_time"] or 0) - elapsed, 0)
                db.execute(
                    "UPDATE games SET white_time=? WHERE id=?", (remaining, game_id)
                )
            else:
                remaining = max((game["black_time"] or 0) - elapsed, 0)
                db.execute(
                    "UPDATE games SET black_time=? WHERE id=?", (remaining, game_id)
                )

        board.push(move)
        applied_msg = f"Move applied. New FEN: {board.fen()}"
        print(applied_msg)
        log_move_debug(applied_msg)

        # ✅ CHECK / CHECKMATE DETECTION
        check = board.is_check()
        checkmate = board.is_checkmate()
        stalemate = board.is_stalemate()

        if checkmate:
            status = "finished"
            result = f"{player_color} wins by checkmate"
            winner_id = (
                game["player1_id"] if player_color == "white" else game["player2_id"]
            )
            db.execute(
                "UPDATE users SET wallet = wallet + ? WHERE id=?",
                (game["bet"], winner_id),
            )
            db.execute("UPDATE games SET winner_id=? WHERE id=?", (winner_id, game_id))
        elif stalemate:
            status = "finished"
            result = "draw by stalemate"
            db.execute(
                "UPDATE users SET wallet = wallet + ? WHERE id=?",
                (game["bet"] / 2, game["player1_id"]),
            )
            db.execute(
                "UPDATE users SET wallet = wallet + ? WHERE id=?",
                (game["bet"] / 2, game["player2_id"]),
            )
        else:
            status = "active"
            result = None

        next_turn = "black" if player_color == "white" else "white"

        # include updated timers when saving and broadcasting
        # fetch updated times
        try:
            row_white_time = game["white_time"] if "white_time" in game.keys() else None
            row_black_time = game["black_time"] if "black_time" in game.keys() else None
        except Exception:
            row_white_time = None
            row_black_time = None
        try:
            cur = db.execute(
                "SELECT white_time, black_time FROM games WHERE id=?", (game_id,)
            ).fetchone()
            if cur:
                row_white_time = cur["white_time"]
                row_black_time = cur["black_time"]
        except Exception:
            pass

        db.execute(
            """
            UPDATE games
            SET board_state = ?,
                current_turn = ?,
                status = ?,
                winner_id = COALESCE(winner_id, NULL)
            WHERE id = ?
        """,
            (board.fen(), next_turn, status, game_id),
        )
        db.commit()

        print(f"Broadcasting move_made to room game_{game_id}")
        log_move_debug(f"Broadcasting move_made to room game_{game_id}")
        # 🔥 Broadcast real game update
        socketio.emit(
            "move_made",
            {
                "game_id": game_id,
                "move": uci_move,
                "fen": board.fen(),
                "check": check,
                "checkmate": checkmate,
                "stalemate": stalemate,
                "status": status,
                "result": result,
                "next_turn": next_turn,
                "white_time": row_white_time,
                "black_time": row_black_time,
            },
            to=f"game_{game_id}",
        )
        return {"success": True}

    except Exception as e:
        err_msg = f"Move error: {type(e).__name__} {e}"
        tb = traceback.format_exc()
        print(err_msg)
        print(tb)
        log_move_debug(err_msg + "\n" + tb)
        emit("invalid_move", {"reason": "Server error"})
        return {"error": "Server error"}


# =========================
# RUN APP
# =========================
@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


if __name__ == "__main__":
    socketio.run(app, debug=True, host="127.0.0.1", port=5000)

