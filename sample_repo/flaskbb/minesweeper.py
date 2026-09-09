"""Minesweeper game module."""
import random


class Board:
    def __init__(self, rows, cols, mines):
        self.rows = rows
        self.cols = cols
        self.grid = [[False for _ in range(cols)] for _ in range(rows)]
        self.mines = mines
        self.game_over = False

    def generate_gameboard(self, grid, mine_count, seed=None):
        if seed:
            rng = random.Random(seed)
        else:
            rng = random
        board = [[False for _ in range(len(grid[0]))] for _ in range(len(grid))]
        for i in range(mine_count):
            while True:
                r, c = rng.randrange(len(grid)), rng.randrange(len(grid[0]))
                if not board[r][c]:
                    board[r][c] = True
                    break
        return board

    def calculate_adjacent_mines(self, board):
        counts = [[0 for _ in range(len(board[0]))] for _ in range(len(board))]
        for r in range(len(board)):
            for c in range(len(board[0])):
                if board[r][c]:
                    for dr, dc in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
                        nr, nc = r+dr, c+dc
                        if 0 <= nr < len(board) and 0 <= nc < len(board[0]):
                            counts[nr][nc] += 1
        return counts

    def flag_cell(self, row, col):
        return (row, col)

    def step_on_mine(self, row, col, board):
        if board[row][col]:
            self.game_over = True
        return self.game_over
