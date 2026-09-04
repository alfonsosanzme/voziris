# Voziris 0.1.0

Primera versión. Dictado por voz para Windows con dos motores: local (NVIDIA
Parakeet TDT 0.6B v3 en ONNX, sin GPU y sin conexión) y por API (Groq, con tu
propia clave).

## Qué hay

- `Ctrl+Win` graba mientras lo mantienes; `Ctrl+Shift+Espacio` deja el
  micrófono clavado hasta que vuelves a pulsar o te callas; `Ctrl+Alt+M`
  manda el dictado a un archivo Markdown.
- Puntuación y mayúsculas de serie en español, también sin conexión.
- Búfer previo de 500 ms: no se pierde la primera sílaba.
- Indicador flotante con nivel de audio que nunca roba el foco; tonos de
  inicio, fin y error; icono de bandeja con cuatro estados.
- Diccionario personal y sustituciones sin red; limpieza de muletillas y
  formato con un LLM opcional.
- El portapapeles vuelve a lo que tenías copiado. Historial con reentrega
  y borrado desde la bandeja.
- Panel de ajustes con seis pestañas. Portable: sin instalador, sin
  registro, sin permisos de administrador.

## Instalación

Descomprime el ZIP y ejecuta `voziris.exe`. El primer arranque descarga el
modelo local (640 MB) a `modelos/`. Ver el README para el primer arranque,
la clave de Groq y qué sale de tu equipo en cada modo.

## Verificación del archivo

```
SHA-256  voziris-0.1.0-win64.zip
46250d160b22fc3c567b125b70f5afc5b312c10079eed84b8959e60631d6b605
```

Un `.exe` de PyInstaller con un hook global de teclado es una firma clásica
de falso positivo de antivirus. Si el archivo que has descargado tiene este
hash, es el que se publicó aquí.

## Limitaciones conocidas

- Sobre una ventana que corre como administrador, Windows no entrega la
  pulsación sintética: el dictado queda en el historial y en el portapapeles.
- Solo se conserva el portapapeles si contenía texto.
- Algunas terminales rechazan el pegado sintético: `metodo = "tecleo"`.

## Licencias

Código bajo MIT. El motor local usa **NVIDIA Parakeet TDT 0.6B v3**,
© NVIDIA Corporation, distribuido bajo **CC-BY-4.0**:
https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3. La atribución viaja en
`ATRIBUCIONES.md` dentro del paquete y en la ventana «Acerca de».
