from rq import Queue, Worker

from .core.config import settings
from .core.logging import setup_logging


def main() -> None:
    setup_logging()

    queue = Queue("default", connection=settings.redis)
    Worker([queue], connection=settings.redis).work()


if __name__ == "__main__":
    main()
