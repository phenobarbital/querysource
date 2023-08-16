"""QS Worker server entry point."""
import asyncio
from .process import SpawnProcess
from .utils import cPrint
from .utils.events import enable_uvloop


def main():
    """Main Worker Function."""
    enable_uvloop()
    process = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cPrint('::: Starting QS Workers ::: ')
        process = SpawnProcess()
        process.start()
        loop.run_forever()
    except KeyboardInterrupt:
        process.terminate()
    except Exception as ex:
        # log the unexpected error
        print(
            f"Unexpected error: {ex}"
        )
        if process:
            process.terminate()
    finally:
        cPrint(
            'Shutdown all workers ...', level='WARN'
        )
        loop.close()  # close the event loop


if __name__ == '__main__':
    main()
