import os
import sys


def main() -> None:
    os.environ["PYTHONUNBUFFERED"] = "1"
    from depcon.cli import app
    app()


if __name__ == "__main__":
    main()
