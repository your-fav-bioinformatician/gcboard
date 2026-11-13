"""A small tic-tac-toe repo and an honest writeup about it, shared by tests."""

BOARD_PY = '''\
"""The game board."""

EMPTY = " "


class Board:
    def __init__(self, size=3):
        self.size = size
        self.cells = [[EMPTY] * size for _ in range(size)]

    def place_mark(self, row, col, mark):
        if self.cells[row][col] != EMPTY:
            raise ValueError("cell taken")
        self.cells[row][col] = mark

    def is_full(self):
        return all(c != EMPTY for line in self.cells for c in line)

    def check_winner(self):
        lines = list(self.cells)
        lines += [list(col) for col in zip(*self.cells)]
        lines.append([self.cells[i][i] for i in range(self.size)])
        lines.append([self.cells[i][self.size - 1 - i] for i in range(self.size)])
        for line in lines:
            if line[0] != EMPTY and all(c == line[0] for c in line):
                return line[0]
        return None

    def render(self):
        rows = [" | ".join(r) for r in self.cells]
        return "\\n---------\\n".join(rows)
'''

GAME_PY = '''\
from board import Board
from ai import best_move


class Player:
    def __init__(self, mark, human=True):
        self.mark = mark
        self.human = human


def ask_move(board):
    while True:
        text = input("row col: ")
        try:
            row, col = map(int, text.split())
            return row, col
        except ValueError:
            print("two numbers, please")


def switch_player(current, players):
    return players[1] if current is players[0] else players[0]


def play_turn(board, player):
    if player.human:
        row, col = ask_move(board)
    else:
        row, col = best_move(board, player.mark)
    board.place_mark(row, col, player.mark)
'''

AI_PY = '''\
def score_board(board, mark):
    winner = board.check_winner()
    if winner == mark:
        return 1
    if winner is None:
        return 0
    return -1


def minimax(board, mark, maximizing):
    if board.check_winner() or board.is_full():
        return score_board(board, mark)
    scores = []
    for row in range(board.size):
        for col in range(board.size):
            if board.cells[row][col] == " ":
                board.cells[row][col] = mark if maximizing else "O"
                scores.append(minimax(board, mark, not maximizing))
                board.cells[row][col] = " "
    return max(scores) if maximizing else min(scores)


def best_move(board, mark):
    best, choice = -2, None
    for row in range(board.size):
        for col in range(board.size):
            if board.cells[row][col] == " ":
                board.cells[row][col] = mark
                value = minimax(board, mark, False)
                board.cells[row][col] = " "
                if value > best:
                    best, choice = value, (row, col)
    return choice
'''

FILES = {"board.py": BOARD_PY, "game.py": GAME_PY, "ai.py": AI_PY}

KEYWORD_GROUPS = [
    ["undo"],
    ["stack", "history", "list of moves", "previous move"],
    ["pop", "remove", "revert", "restore"],
    ["board", "grid"],
]

Q1 = (
    "This part is the check_winner method on the Board class. It collects every line that can win: the three "
    "rows, the three columns built with zip, and both diagonals. Then it walks through those lines and returns "
    "the mark if all cells in a line match and are not empty. I wrote it this way because building the list of "
    "lines first keeps the loop simple, and the same code works for bigger boards. The game calls it after "
    "every place mark call to decide whether someone has won, and the minimax search uses it to stop early."
)
Q2 = (
    "The project has three files. The board module holds the Board class, which stores cells in a nested list "
    "and knows how to place a mark, render itself, and detect a winner or a full grid. The game module handles "
    "people: a small Player class records the mark and whether a human is playing, ask_move reads coordinates "
    "from input and retries on bad text, and switch_player alternates turns. Each round runs play_turn, which "
    "asks either the human or the computer for a move. The ai module contains the opponent. It uses minimax, "
    "a recursive search that tries every free cell, scores finished positions with score_board, and returns "
    "the best value for the side to move. Finally best_move picks the cell with the highest value."
)
Q3 = (
    "To add undo I would keep a history of moves as a stack. Every time a mark is placed, play_turn would push "
    "the row and column onto that list. When the player asks to undo, the game would pop the last entry and "
    "restore that cell on the board to empty, then call switch_player so the right person moves again. "
    "Against the computer I would revert two moves at once, so the human gets their turn back."
)
Q4 = (
    "Early on the computer sometimes picked a cell that was already taken. I found it by printing the board "
    "inside best_move during a game and noticing that minimax left marks behind. The recursive search changed "
    "a cell but forgot to clear it on one branch. I fixed it by always resetting the cell right after the "
    "recursive call, and I added a check in place_mark that raises an error when a cell is occupied."
)

ANSWERS = [Q1, Q2, Q3, Q4]

# Long enough to pass the gate, but vague: no identifiers, no change keywords.
VAGUE = [
    "It handles the main logic and makes sure everything works as expected for the user when they play. " * 5,
    "There are several files that each do one job, which keeps things tidy and easy to follow later on. " * 6,
    "I would add a button and make it work properly with what already exists in the program somehow. " * 4,
    "Something broke once and I looked at it for a while until I eventually figured out what was wrong. " * 4,
]
