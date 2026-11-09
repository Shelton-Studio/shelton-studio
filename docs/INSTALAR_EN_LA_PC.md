# Instalar Shelton Studio en la PC con la GPU (máquina `shelton` de Tailscale)

Guía para la persona que instala. Al final, el equipo entra con **https://shelton.tailc84616.ts.net**, con usuario y contraseña.

**Resultado:**
- Shelton vive en esta PC.
- Desde el primer día funciona con la nube (OpenRouter).
- Cuando la IA local esté lista, el switch **Local** usa la RTX 3090 para imágenes, video y voz.

Necesitas:
- acceso a la terminal de la PC (Linux), como `root` o con `sudo`;
- la clave de OpenRouter (la pide el instalador sin mostrarla);
- unos 150 GB libres para los modelos locales (paso 4, opcional al principio).

Cada comando va en su propio bloque: cópialo, pégalo en la terminal y pulsa Enter.

---

## Paso 0 · Desconectar por completo el Shelton anterior

El Shelton que hay hoy en esta PC es **otro proyecto**, sin relación con Shelton Studio. Se apaga, se desactiva, se saca del link https y su carpeta se mueve aparte, para que no quede conectado a nada nuestro. No se borra: si algún día lo quieren, sigue en su carpeta.

Entrar como root:

```bash
sudo -i
```

Ver qué servicios o programas de Shelton hay corriendo:

```bash
systemctl list-units --type=service --all | grep -i -E "shelton|comfy|voz|tts"
```

```bash
ss -ltnp | grep -E ":80|:443|:8[0-9]{3}"
```

Ver qué publica hoy Tailscale en el link https:

```bash
tailscale serve status
```

Por cada servicio del Shelton viejo que apareció, detenlo y desactívalo. Cambia `NOMBRE` por el nombre que salió, por ejemplo `shelton.service`:

```bash
systemctl disable --now NOMBRE
```

- Si corre en una ventana de terminal, en `screen` o en `tmux`, ciérralo ahí (`Ctrl+C`).
- Si corre con Docker, detenlo para que no vuelva a arrancar solo. Cambia `CONTENEDOR` por el nombre que muestra `docker ps`:

```bash
docker update --restart=no CONTENEDOR
```

```bash
docker stop CONTENEDOR
```

Sacarlo del link https (el link queda libre para el nuestro):

```bash
tailscale serve reset
```

Si el proyecto viejo está en la carpeta `~/shelton-studio`, muévelo aparte para que no se mezcle con el nuevo (si no existe, este comando no hace nada):

```bash
[ -d ~/shelton-studio ] && mv ~/shelton-studio ~/shelton-viejo-desconectado
```

Comprueba que ya no responde nada en el link (debe dar error o no conectar):

```bash
curl -sk -o /dev/null -w "%{http_code}\n" https://shelton.tailc84616.ts.net
```

## Paso 1 · Tailscale con HTTPS

1. En https://login.tailscale.com, con la cuenta dueña de la red (`chalausky.audit@gmail.com`), entra a **DNS** y activa **MagicDNS** y **HTTPS Certificates**.
2. La máquina debe seguir llamándose `shelton` (así sale el link `shelton.tailc84616.ts.net`). Si tiene otro nombre:

```bash
tailscale set --hostname=shelton
```

## Paso 2 · Instalar Shelton (con la nube, funciona enseguida)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juancamp7777/alligator-AI/main/install.sh)
```

El instalador pregunta tres cosas:
1. **¿Link https privado de Tailscale?** → Enter (sí).
2. **Clave de OpenRouter** → pégala (no se ve al escribir).
3. **Usuario admin y contraseña** → mínimo 10 caracteres. Si la dejas vacía, genera una segura y la muestra **una sola vez**: guárdala.

Al final muestra: `Abre: https://shelton.tailc84616.ts.net`. Ábrelo desde cualquier equipo de la red Tailscale y entra con ese usuario.

> Si dice que no pudo publicar el link https: revisa el paso 1 (HTTPS Certificates) y vuelve a correr el instalador.

## Paso 3 · Dar acceso al equipo (Abeth, Marc…)

- **Tailscale:** en la consola, Machines → `shelton` → **Share…**, o invítalos a la red. Ellos instalan Tailscale en su equipo y entran con su cuenta.
- **Shelton:** con tu usuario admin, arriba a la derecha → **Usuarios** → crea uno para cada persona.
  - **admin**: puede todo.
  - **editor**: usa el estudio, pero no administra cuentas.
  - Cada uno cambia su contraseña desde su menú. No mandes contraseñas por chat.

## Paso 4 · IA local en la RTX 3090 (cuando quieran probar lo local)

Necesita el driver de NVIDIA (`nvidia-smi` debe mostrar la 3090). Si no está:

```bash
ubuntu-drivers install
```

```bash
reboot
```

Instala ComfyUI y la voz local como servicios, y baja los modelos. Tarda: son unos 120 GB.

```bash
bash ~/shelton-studio/scripts/gpu_setup.sh
```

- **Solo algunos modelos para empezar**, por ejemplo imágenes y edición:

```bash
SHELTON_MODELS="imagen edicion" bash ~/shelton-studio/scripts/gpu_setup.sh
```

- **Si se corta**, vuelve a correrlo: retoma donde quedó.
- **El sonido** (Stable Audio) pide aceptar su licencia en Hugging Face y entrar una vez:

```bash
~/ComfyUI/venv/bin/hf auth login
```

- **Después, en Shelton:**
  - pon el switch de arriba en **Local**, y pide "hazme 2 fotos de prueba";
  - **Ajustes → Estado** dice si falta algún modelo o nodo;
  - mientras trabaja, `nvidia-smi` muestra la GPU en uso.

## Uso diario

| Para | Comando |
|---|---|
| Ver si Shelton está vivo | `systemctl status shelton-studio` |
| Ver sus mensajes | `journalctl -u shelton-studio -f` |
| ComfyUI / voz | `journalctl -u comfyui -f` · `journalctl -u shelton-voz -f` |
| Actualizar Shelton | vuelve a correr el comando del paso 2 |
| Olvidé la contraseña | `cd ~/shelton-studio && venv/bin/python -m app.auth reset-password admin` |
| Copia de seguridad | en la web: Ajustes → **Exportar todo** |

**Seguridad:**
- Shelton, ComfyUI y la voz escuchan solo dentro de la PC (`127.0.0.1`).
- Desde afuera se entra únicamente por el link https de Tailscale, con usuario y contraseña.
- No abras puertos en el router.
