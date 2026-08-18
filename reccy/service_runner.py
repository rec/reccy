import runpy
import sys
from pathlib import Path

from . import logging


def main() -> None:
    log, module, *arguments = sys.argv[1:]
    logging.configure(Path(log))
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__':
    main()
