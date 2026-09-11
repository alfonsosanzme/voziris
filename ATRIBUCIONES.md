# Atribuciones y licencias de terceros

El código de Voziris se distribuye bajo licencia MIT (ver `LICENSE`). Los modelos
y bibliotecas que utiliza tienen sus propias licencias.

## Modelos

### NVIDIA Parakeet TDT 0.6B v3 — **CC-BY-4.0**

> Modelo `nvidia/parakeet-tdt-0.6b-v3`, © NVIDIA Corporation.
> Distribuido bajo Creative Commons Attribution 4.0 International.
> https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3

**La licencia CC-BY-4.0 obliga a dar crédito.** Esta atribución debe aparecer, sin
excepción, en tres sitios:

1. Este archivo.
2. El README del repositorio.
3. La ventana «Acerca de» de la aplicación (`ui/ajustes.py`).

Quitar cualquiera de las tres incumple la licencia.

### OpenAI Whisper — MIT

Solo si se activa el motor local alternativo. https://github.com/openai/whisper

### pyannote segmentation 3.0 — MIT

> © pyannote / Hervé Bredin. https://huggingface.co/pyannote/segmentation-3.0
> Conversión a ONNX distribuida por el proyecto sherpa-onnx.

Separación de hablantes en las grabaciones (dónde hay voz, cuándo cambia).
Se descarga a `modelos/hablantes/` la primera vez que se pide.

### NVIDIA TitaNet small — **CC-BY-4.0**

> Modelo `nvidia/speakerverification_en_titanet_small`, © NVIDIA Corporation.
> Distribuido bajo Creative Commons Attribution 4.0 International. Conversión a
> ONNX distribuida por el proyecto sherpa-onnx.

Huella de voz de cada trozo, para agrupar por hablante. Misma obligación de
crédito que Parakeet: figura aquí, en el README y en «Acerca de».

## Bibliotecas que viajan dentro del paquete

- **sherpa-onnx** (Apache-2.0): ejecuta los dos modelos anteriores.
  https://github.com/k2-fsa/sherpa-onnx
- **PyAV** (BSD-3) con **FFmpeg** dentro: decodifica las grabaciones (m4a,
  mp3…). Las ruedas oficiales de PyAV para Windows incluyen FFmpeg compilado
  con x264 y x265, es decir, **bajo GPL**. Voziris no usa esos codificadores,
  pero las DLL van en el ZIP: el paquete binario, en conjunto, se distribuye
  en términos compatibles con la GPL, lo que la licencia MIT del código
  permite. El código fuente completo está en el repositorio.
  https://github.com/PyAV-Org/PyAV · https://ffmpeg.org/legal.html
- **onnxruntime** (MIT), **onnx-asr** (MIT), **pystray** (LGPL-3), **Pillow**
  (MIT-CMU), **sounddevice** (MIT), **numpy** (BSD).

## Servicios

Groq no impone atribución. La clave de API es siempre del usuario final: **el
repositorio no contiene ninguna clave y el binario distribuido tampoco.**
