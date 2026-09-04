# Cómo verificar el paquete y publicar una versión

El paquete portable se construye con PyInstaller en modo `onedir` (ver
`build/voziris.spec` para el porqué). Esta es la lista de comprobación de
VOZ-61 y VOZ-62: lo automático lo hace un comando; lo que exige otro equipo
lo hace una persona.

## 1. Construir

```bash
pyinstaller build/voziris.spec --noconfirm --distpath build/dist --workpath build/work
```

Deja `build/dist/voziris/` con `voziris.exe` y la carpeta `_internal/`.
Dentro de `_internal/` deben viajar `ATRIBUCIONES.md`, `LICENSE`,
`config.ejemplo.toml` y `assets/` (el `.spec` los añade; comprobarlo).

## 2. Comprobar en el equipo de desarrollo

Sin tocar `modelos/` del repositorio, con un `config.toml` de prueba que
apunte la carpeta de modelos al del repositorio:

```bash
build\dist\voziris\voziris.exe --archivo banco\muestra-08.wav --config build\work\prueba\config.toml
```

El ejecutable no tiene consola: el resultado queda en el `voziris.log` junto
a ese `config.toml` («consola: motor=local rtf=… entregado=True»). Después:

```bash
build\dist\voziris\voziris.exe --config build\work\prueba\config.toml
build\dist\voziris\voziris.exe --salir
```

En el log, la diferencia entre la primera línea y «arrancado» es el tiempo de
arranque: tiene que ser menor de 3 segundos. «cerrando» al final confirma el
cierre limpio.

### Primer arranque de verdad, sin `--config`

Lo anterior no prueba lo que ve un usuario: una carpeta recién extraída, sin
`config.toml`. Hay que copiar `build/dist/voziris/` a una carpeta temporal
(con `modelos/` del repositorio al lado, para no descargar 640 MB) y
ejecutar `voziris.exe` sin argumentos. Tiene que copiar `config.ejemplo.toml`
desde `_internal/` a `config.toml`, arrancar y responder a `voziris.exe
--salir`. La primera versión del paquete falló justo aquí.

## 3. Empaquetar

```powershell
Compress-Archive -Path build\dist\voziris -DestinationPath build\dist\voziris-<versión>-win64.zip
Get-FileHash build\dist\voziris-<versión>-win64.zip -Algorithm SHA256
```

El ZIP tiene que pesar menos de 120 MB. El hash va en las notas de la release
y en `SHA256SUMS.txt`.

## 4. Verificar en una máquina limpia (a mano)

Esto no se puede automatizar desde el equipo de desarrollo y es lo que
convierte «funciona aquí» en «funciona»:

- [ ] Windows 10 o 11 recién instalado, **sin Python**. Descomprimir el ZIP y
      ejecutar `voziris.exe` desde una carpeta normal y desde un USB.
- [ ] Arranca en menos de 3 segundos desde el doble clic (icono en la bandeja).
- [ ] Descarga el modelo a `modelos/` con avisos de progreso y, al terminar,
      dicta con `Ctrl+Win`.
- [ ] No pide permisos de administrador en ningún momento (ni UAC al
      arrancar, ni al crear el acceso directo de Inicio, ni al guardar ajustes).
- [ ] Copiar la carpeta a otro equipo con el `config.toml` ya editado: los
      ajustes viajan con ella.
- [ ] Si el antivirus lo marca: comprobar que el SHA-256 del ZIP coincide con
      el publicado y anotar qué antivirus y qué firma dice.

## 5. Publicar

Solo después del punto 4:

```bash
git tag -a v<versión> -m "Voziris <versión>"
git push origin v<versión>
gh release create v<versión> build/dist/voziris-<versión>-win64.zip build/dist/SHA256SUMS.txt \
  --title "Voziris <versión>" --notes-file docs/notas-release.md
```

Las notas de la release deben llevar: qué hay de nuevo, el hash SHA-256 del
ZIP, la advertencia sobre antivirus y ventanas elevadas, y la atribución a
NVIDIA (CC-BY-4.0), que tiene que aparecer en el repositorio, en el binario
y en «Acerca de».
