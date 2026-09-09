def main():
    print("Hello World")

def test__mine_minesweeper_():
    import minesweeper
    b = minesweeper.Board(5, 5, 3)
    assert b.rows == 5
    assert b.cols == 5
    assert not b.game_over
    b2 = b.generate_gameboard(b.grid, 3, 42)
    assert sum(sum(r) for r in b2) == 3


if __name__ == "__main__":
    main()
