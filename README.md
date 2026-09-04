# Voziris

Dictado por voz para Windows. Mantienes un atajo, hablas, sueltas, y el texto
aparece donde tengas el cursor.

Dos motores a elegir: **local**, gratis y sin conexión, que funciona en
portátiles sin GPU; o **por API**, con tu propia clave, por unos céntimos al
mes. Sin cuentas, sin servidor propio, sin suscripción.

> **Estado: en desarrollo.** El esqueleto, los contratos y el plan están
> completos; la implementación está por hacer. Ver `docs/ENCARGO.md`.

## Por qué existe

Wispr Flow cuesta 144 $/año, procesa todo en la nube —no tiene modo local en
ningún plan— y su ajuste de tono y estilo solo funciona en inglés. Voziris
cubre esos tres huecos. Con Groq, transcribir media hora de audio al día sale
por unos 0,60 $ al mes; en local, por cero.

## Qué hace

| | |
|---|---|
| **Tres atajos** | Uno graba mientras lo mantienes pulsado, otro deja el micrófono clavado, y un tercero manda el dictado a un archivo Markdown |
| **Puntuación de serie** | El modelo emite puntuación y mayúsculas en español, también sin conexión |
| **Limpia lo que dices** | Quita muletillas y resuelve tus autocorrecciones al hablar («el martes, no, el jueves») |
| **No te toca el portapapeles** | Lo guarda antes de escribir y lo devuelve después |
| **Historial con reintento** | Si el pegado falla, el texto no se pierde |
| **Diccionario propio** | Para los nombres propios y la jerga que el modelo falla siempre |
| **Portable** | Se copia y funciona. Sin instalador, sin registro, sin permisos de administrador |

## Cómo funciona

```
Atajo → búfer previo 500 ms → captura 16 kHz → motor (local o API)
      → post-proceso (diccionario, sustituciones, LLM opcional)
      → destino (app activa o archivo .md) → historial
```

El búfer previo es lo que evita que se pierda la primera sílaba: el micrófono
graba siempre en un anillo de medio segundo, así que cuando pulsas el atajo ya
hay audio anterior a la pulsación.

## Desarrollo

Requiere Python 3.11–3.13 y Windows 10 (1903+) o 11, 64 bits.

```bash
git clone <repo> && cd voziris
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,banco]"

copy config.ejemplo.toml config.toml    # y editar la ruta del Markdown

pytest                                  # tests
ruff check src tests                    # linter
mypy                                    # tipos (estricto)
python -m voziris                        # arrancar
```

**Antes de escribir código de la aplicación, ejecutar el hito 0:**

```bash
python tools/banco_h0.py grabar --n 10 --segundos 15
python tools/banco_h0.py medir
```

Mide en tu equipo si el motor local es lo bastante rápido. Los RTF publicados
son de un i7 de sobremesa y en portátil serán peores. El script trae el
criterio de decisión escrito.

### Empaquetado

```bash
pyinstaller build/voziris.spec --noconfirm
```

Produce `build/dist/voziris/`. Se comprime y se distribuye tal cual: es la
carpeta portable. El modelo (~680 MB) no va dentro, se descarga en el primer
arranque.

### Documentación

| Documento | Para qué |
|---|---|
| `docs/ENCARGO.md` | El encargo: qué construir, qué está decidido, qué no tocar |
| `docs/BACKLOG.md` | Las 25 tareas con criterios de aceptación |
| `docs/ARQUITECTURA.md` | Módulos, contratos y por qué están así |
| `docs/issues.csv` | El backlog, importable a GitHub Issues |

## Limitaciones conocidas

1. **Ventanas elevadas.** Si la aplicación en primer plano corre como
   administrador y Voziris no, Windows no le entrega la pulsación sintética.
   No hay solución que no sea ejecutar Voziris elevado, y eso choca con no
   pedir permisos de administrador.
2. **Antivirus.** Un `.exe` de PyInstaller con un hook global de teclado es una
   firma clásica de falso positivo. Los hashes de cada versión están en las
   notas de la release.
3. **Portapapeles.** Voziris guarda lo que tenías copiado antes de dictar y lo
   devuelve después, pero solo si era texto. Una imagen o unos archivos
   copiados se pierden al dictar: en su lugar queda el texto dictado.
4. **Aplicaciones que rechazan el pegado.** Algunas terminales y programas
   con protección de entrada ignoran el Ctrl+V sintético. Para ellos,
   `metodo = "tecleo"` en `[destino.app_activa]`: más lento, pero entra.

## Licencia

Código bajo licencia MIT (ver `LICENSE`).

El motor local usa **NVIDIA Parakeet TDT 0.6B v3**, © NVIDIA Corporation,
distribuido bajo **CC-BY-4.0**:
https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3

Ver `ATRIBUCIONES.md`. Esa atribución es una obligación de la licencia y debe
seguir apareciendo en el repositorio, en el binario y en la ventana «Acerca de».
