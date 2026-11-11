# Voziris: condiciones de uso y privacidad

Última revisión: 11 de noviembre de 2026.

## En pocas palabras

- **Voziris no recoge ningún dato tuyo.** No tiene telemetría, estadísticas de uso, publicidad ni cuentas de usuario, y no existe ningún servidor nuestro al que mande nada. No sabemos quién lo instala, cuánto lo usa ni qué dicta.
- **Lo que dictas se queda en tu equipo.** Solo sale si tú conectas un servicio externo, como la API de Groq, con tu propia clave.
- **Es gratis y de código abierto.** No hay versión de pago, ni anuncios, ni nada que vender detrás.

## Por qué es gratis

Voziris es un proyecto de Kairis (kairis.es). Nació como una herramienta de uso personal y lo compartimos porque nos gusta enseñar lo que vamos haciendo. No tenemos ningún interés comercial en él ni en tus datos.

Todo el código es público, en https://github.com/alfonsosanzme/voziris, bajo licencia MIT: puedes usarlo, copiarlo y compartirlo libremente, también en el trabajo, y cualquiera puede comprobar en el código lo que aquí se dice.

## Qué guarda en tu equipo

Voziris guarda sus datos en su propia carpeta, en tu disco, y no los manda a ningún sitio:

- Tus ajustes, en el archivo `config.toml`. Si pones una clave de Groq, se guarda ahí, sin cifrar.
- El texto de tus últimos dictados, para que puedas copiarlos o volver a escribirlos desde el menú.
- La grabación de cada dictado mientras se transcribe y, después, la de los últimos: como mucho 20 dictados, 7 días y 1 GB. Sirve para volver a transcribirlos si el texto salió mal. Con `conservar_audio = false` en `config.toml` solo se guardan, esos mismos 7 días, las de los dictados que no se llegaron a transcribir, para que no se pierdan.
- Un registro técnico de funcionamiento. En el uso normal no contiene lo que dictas; sí el nombre del programa en el que se escribió y el de los archivos que transcribes.

Para escribir en otros programas, Voziris usa el portapapeles de Windows: si tienes activado su historial, lo dictado puede quedar también ahí.

Todo eso se borra al desinstalar Voziris. El «diagnóstico» que ayuda a resolver problemas solo se crea si tú lo pides, se guarda en tu equipo y eres tú quien decide si se lo envías a alguien.

## Qué sale de tu equipo, y cuándo

Voziris solo se conecta a internet en estos casos:

- **Para descargar los modelos de voz**, la primera vez, desde Hugging Face. Y desde GitHub, la primera vez que transcribes una grabación con la opción de distinguir a varios hablantes.
- **Para saber si hay conexión**, antes de cada dictado: abre y cierra una conexión con el servidor de la API (el de Groq, de serie) sin enviar ningún dato. No lo hace si eliges el motor «Local» y el nivel de limpieza «Literal».
- **Para buscar versiones nuevas** en GitHub: una vez al día si lo aceptas cuando te lo pregunta, y siempre que pulses «Buscar actualizaciones» o «Actualizar».
- **Para usar la API**, solo si tú pones tu clave. Entonces el audio de cada dictado y de cada grabación que transcribas, junto con las palabras de tu diccionario, va a Groq para transcribirlo, salvo que elijas el motor «Local». Y si activas la limpieza con IA, va también el texto, con cualquier motor. Esos datos los trata Groq, o el servicio que tú pongas en su lugar, con tu cuenta y según sus condiciones (https://console.groq.com/docs/your-data), no las nuestras.

En esas conexiones, el servicio al que se llama ve lo mismo que cualquier web que visitas: tu dirección IP y datos técnicos como la versión del programa. Voziris no envía tu nombre ni ningún identificador de tu equipo. A la API viaja tu clave, que es lo que identifica tu cuenta ante Groq.

## Sin garantía

Voziris se ofrece tal cual, sin garantía de ningún tipo, como establece su licencia MIT. Úsalo bajo tu responsabilidad y revisa lo que escribe antes de enviarlo: ningún reconocimiento de voz acierta siempre.

Todavía no lleva firma digital, y por eso Windows puede mostrar un aviso al instalarlo.

## Contacto

Para dudas, problemas o sugerencias: https://github.com/alfonsosanzme/voziris/issues
