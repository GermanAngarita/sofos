import threading
import webbrowser

import uvicorn

from sofos.config import HOST, PORT
from sofos.photos import clear_view_cache


def main() -> None:
    clear_view_cache()
    url = f"http://{HOST}:{PORT}"
    print(f"Sofos corriendo en {url}  (Ctrl+C para salir)")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # Sólo escucha en 127.0.0.1: nadie más en la red puede ver tus fotos/mensajes.
    uvicorn.run("sofos.server:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
