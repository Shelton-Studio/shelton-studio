# Instalar Shelton Studio en la PC con la GPU (máquina `shelton` de Tailscale)

Guía para la persona que instala. Al final, el equipo entra con el link que muestra el instalador (en la PC compartida: **https://shelton.tailc84616.ts.net:8443**), con usuario y contraseña.

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

## Paso 0 · No tocar nada de lo que ya corre en la PC

Esta PC es **compartida**: tiene otros proyectos funcionando, con sus servicios, Docker, bases de datos, Ollama, su propio ComfyUI y su voz. **No se apaga ni se borra nada de eso.** Shelton Studio se instala **al lado**, totalmente separado:
- carpeta propia: `~/shelton-studio`;
- servicio propio: `shelton-studio`;
- puerto propio (el primero libre desde el 8010, solo dentro de la PC);
- base de datos propia, dentro de su carpeta;
- link https propio: si el link sin número ya lo usa otra app, el instalador usa `https://shelton.tailc84616.ts.net:8443` (o el siguiente puerto libre) y no pisa lo de ellos.

No corras `tailscale serve reset`: borraría los links de las otras apps.

## Paso 1 · Tailscale con HTTPS

1. En https://login.tailscale.com, con la cuenta dueña de la red (`chalausky.audit@gmail.com`), entra a **DNS** y activa **MagicDNS** y **HTTPS Certificates**.
2. La máquina debe seguir llamándose `shelton` (así sale el link `shelton.tailc84616.ts.net`). Si tiene otro nombre:

```bash
tailscale set --hostname=shelton
```

## Paso 2 · Instalar Shelton (con la nube, funciona enseguida)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Shelton-Studio/shelton-studio/main/install.sh)
```

El instalador pregunta tres cosas:
1. **¿Link https privado de Tailscale?** → Enter (sí).
2. **Clave de OpenRouter** → pégala (no se ve al escribir).
3. **Usuario admin y contraseña** → mínimo 5 caracteres. Si la dejas vacía, genera una segura y la muestra **una sola vez**: guárdala.

Al final muestra el link, por ejemplo `Abre: https://shelton.tailc84616.ts.net:8443`. Ábrelo desde cualquier equipo de la red Tailscale y entra con ese usuario.

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

Instala **nuestro propio** ComfyUI y nuestra voz local, como servicios `shelton-studio-comfy` (puerto 8288) y `shelton-studio-voz` (puerto 8289), y baja los modelos. No pisa el ComfyUI ni la voz que ya existan. Tarda: son unos 120 GB.

> Si la GPU ya la usan otros programas (Ollama, otro ComfyUI), los modelos grandes pueden quedarse sin memoria. Coordínenlo antes de probar video local.

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
| ComfyUI / voz (las nuestras) | `journalctl -u shelton-studio-comfy -f` · `journalctl -u shelton-studio-voz -f` |
| Actualizar Shelton | vuelve a correr el comando del paso 2 |
| Olvidé la contraseña | `cd ~/shelton-studio && venv/bin/python -m app.auth reset-password admin` |
| Copia de seguridad | en la web: Ajustes → **Exportar todo** |

**Seguridad:**
- Shelton, ComfyUI y la voz escuchan solo dentro de la PC (`127.0.0.1`).
- Desde afuera se entra únicamente por el link https de Tailscale, con usuario y contraseña.
- No abras puertos en el router.
