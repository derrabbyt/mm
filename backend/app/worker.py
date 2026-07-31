from rq import Queue, Worker

from .core.logging import setup_logging
from .core.redis import get_redis


def main() -> None:
    setup_logging()

    connection = get_redis()
    queue = Queue("default", connection=connection)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
