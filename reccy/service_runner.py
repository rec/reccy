import runpy
import sys
from pathlib import Path

from . import logging


def main() -> None:
    log, service_name, module, *arguments = sys.argv[1:]
    logging.configure(Path(log), service_name=service_name)
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__':
    main()
