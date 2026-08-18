import os
import sys

from .logging import LOG_PATH_ENVIRONMENT_VARIABLE


def main() -> None:
    log, *arguments = sys.argv[1:]
    os.environ[LOG_PATH_ENVIRONMENT_VARIABLE] = log
    os.execv(sys.executable, [sys.executable, *arguments])


if __name__ == '__main__':
    main()
