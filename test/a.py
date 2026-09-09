print("a")

import sys
try:
    with open("soumya.py", "r") as f:
        print(f.read(), end="")
    sys.exit(0)
except FileNotFoundError:
    sys.exit(1)
