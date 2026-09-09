import sys


if __name__ == "__main__":
    print("hi " + (sys.argv[1] if len(sys.argv) > 1 else ""))
