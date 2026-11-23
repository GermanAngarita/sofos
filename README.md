# Sofos

Fotos y mensajes de tu teléfono en una sola interfaz, conectándolo **por cable** a la Mac.
Sin cuentas ni contraseñas: sólo autorizas la computadora una vez desde el teléfono.

| Fuente | Fotos | Mensajes | Cómo se autoriza |
|---|---|---|---|
| **iPhone** (USB) | ✅ se ven directo del teléfono, sin copiar (o copia todo si quieres) | ✅ SMS + iMessage (respaldo local sólo de `sms.db`) | «¿Confiar en esta computadora?» + código de desbloqueo, una sola vez |
| **Android** (USB) | ✅ DCIM y Pictures | ⚠️ SMS por `adb`, si el teléfono lo permite | «Permitir depuración USB» → «Permitir siempre» |
| **Esta Mac** | — | ✅ iMessage sincronizado por iCloud | «Acceso total al disco» para la Terminal |

## Instalación

Requisitos: macOS, Python 3.11+ y las herramientas de línea de comandos de Apple
(`xcode-select --install`; sólo se usan para compilar el visor de pantalla del iPhone).

```bash
git clone https://github.com/GermanAngarita/sofos.git
cd sofos
python3 -m venv .venv
.venv/bin/pip install -e .
```

El repositorio es privado: `git clone` te pedirá iniciar sesión en GitHub la primera vez.

## Uso

```bash
.venv/bin/sofos          # abre http://127.0.0.1:8765
```

Para actualizar a la última versión: `git pull` (y reinicia Sofos).

Para desarrollo (tests): `.venv/bin/pip install -e ".[dev]"` y `.venv/bin/pytest`.

El servidor sólo escucha en `127.0.0.1`, así que nada queda expuesto en tu red.

Lo que guardas del teléfono queda dentro del proyecto (ignorado por git):

```
descargas/
  Fotos/AAAA-MM/   fotos y videos, por mes en que se tomaron
  Capturas/        capturas de la pantalla del iPhone
```

Una foto del teléfono cuenta como «ya guardada» si en `descargas/Fotos` hay un archivo con el mismo
nombre y tamaño (en cualquier carpeta de mes). Puedes cambiar la carpeta con `SOFOS_DOWNLOADS_DIR`.
Lo que se copia del teléfono se guarda en `~/.sofos/` (puedes cambiarlo con `SOFOS_DATA_DIR`).

### iPhone
1. Conecta el cable y desbloquea el iPhone.
2. Toca **Confiar** e ingresa tu código de desbloqueo. Es la única vez.
3. En Sofos → **Fotos**: ves todo lo que hay en el iPhone agrupado por día, sin copiar nada.
   Clic para elegir (Shift+clic = rango, «Elegir día» = el día completo) y **Guardar en la Mac**.
   Las ya guardadas aparecen con ✓ y no se vuelven a copiar. La vista **Guardadas** muestra lo que ya
   tienes en la Mac.
4. **Mensajes → Actualizar mensajes**.

- **Velocidad:** el límite es el cable. Con el cable USB-C que trae el iPhone (USB 2.0) son ~40 MB/s;
  con un cable USB 3 (10 Gb/s) en un iPhone Pro puede ser varias veces más rápido.
- **iCloud:** se ven las fotos tomadas con el iPhone y las que iCloud ya bajó al teléfono. Las que sólo
  están en iCloud (por «Optimizar almacenamiento») no pasan por el cable.
- **Respaldo cifrado:** si en Finder tienes activado «Cifrar respaldo local», `sms.db` no se puede leer
  sin esa contraseña. O lo desactivas (Finder te la pide una vez) o usas la fuente «Esta Mac».
- Las miniaturas son las que el propio iPhone ya generó (`/PhotoData/Thumbnails/V2`), por eso cargan
  al instante. Las fotos HEIC se convierten a JPEG al vuelo para mostrarlas en el navegador.
- Por seguridad, la app sólo acepta rutas tipo `125APPLE/IMG_5226.JPG`: desde el navegador no se puede
  pedir ningún otro archivo del teléfono.

### Android
1. `brew install android-platform-tools`
2. En el teléfono: Ajustes → Acerca del teléfono → toca 7 veces «Número de compilación»;
   luego Opciones de desarrollador → **Depuración USB**.
3. Conecta el cable y acepta «Permitir siempre desde esta computadora».

Algunos fabricantes no dejan leer los SMS por `adb`. En ese caso el plan es una app compañera
pequeña en el teléfono que exponga los SMS por `adb forward` (pendiente).

### Esta Mac (iMessage)
Ajustes del Sistema → Privacidad y seguridad → **Acceso total al disco** → activa la app desde la que
ejecutas Sofos (Terminal, iTerm, VS Code) y reiníciala.

## Estructura

```
sofos/
  __main__.py          # comando `sofos`: levanta el servidor y abre el navegador
  server.py            # API FastAPI + sirve la interfaz web
  manager.py           # une las fuentes; tareas en segundo plano (copiar fotos/respaldos)
  photos.py            # miniaturas y HEIC→JPEG
  saved.py             # descargas/Fotos: qué hay y qué del teléfono ya está guardado
  screen.py            # pantalla del iPhone en vivo (lanza native/screen.swift)
  devices/
    base.py            # interfaz DeviceAdapter (agregar una fuente = implementar esto)
    ios.py             # iPhone vía pymobiledevice3 (AFC para fotos, mobilebackup2 para mensajes)
    android.py         # Android vía adb
    mac.py             # ~/Library/Messages/chat.db
  messages/
    apple_db.py        # lector del esquema de Mensajes de Apple (sms.db / chat.db)
    android_sms.py     # parser de `adb shell content query`
  web/                 # interfaz (HTML/CSS/JS sin build)
tests/
```

## Ideas siguientes
- WhatsApp: en iPhone se puede agregar `BackupSelection.WHATSAPP` al respaldo y leer `ChatStorage.sqlite`.
- Mostrar adjuntos (fotos dentro de mensajes) y nombres de contactos (AddressBook del respaldo).
- Empaquetar como app de Mac (Tauri o `py2app`) para abrirla con doble clic.
