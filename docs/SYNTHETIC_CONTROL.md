# Controlador sintético inicial

La vista previa opcional `--camera-preview INDEX` conecta puntos faciales de MediaPipe al mismo panel virtual. Requiere activación expresa, modelo local verificado y una pestaña visible. Solo se ha ensayado con cámara falsa y caras generadas. El inicio habitual sigue siendo simulado. Consulta [PAQUETE_VALIDACION.md](../PAQUETE_VALIDACION.md).


Actualización 2026-11-19. La modalidad por defecto del ejemplo convierte el puntero en una pose ficticia. Mantén Espacio entre 0,65 y 1,5 s para simular el parpadeo y confirma al soltar. Hay clic doble, desplazamiento, recalibración del centro y teclado por grupos con sugerencias locales. Esc detiene el panel. El procedimiento está en [PAQUETE_VALIDACION.md](../PAQUETE_VALIDACION.md). Las notas siguientes describen la modalidad original con permanencia en Confirmar.


## Interfaz local de simulación

El ejemplo `examples/simulation/server.py` muestra un panel local en el navegador. El puntero o el foco del teclado sobre una casilla representa una señal artificial; no hay seguimiento ocular ni cámara. Cada permanencia dura un segundo. Empieza en pausa: mantener la señal en **Reanudar** la activa. Elegir una acción y luego **Confirmar** registra el resultado en una zona virtual. **Pausar** y **Cancelar** liberan cualquier arrastre simulado. Si la señal desaparece o deja de llegar durante 250 ms, se pausa; para recuperar el control, volver a **Reanudar** y mantener la señal un segundo.

El panel ofrece clic, arrastre y suelta, scroll de cuatro direcciones y las teclas de ejemplo `a`, `ñ`, Intro y Borrar. La señal representa el centro de la casilla, no una posición ocular continua. El servidor mantiene una sola sesión en memoria para sus pestañas. Se detiene con Ctrl+C. No guardar datos personales en el texto de la simulación.

Desde el directorio del proyecto, para iniciar el ejemplo:

```bash
.venv/bin/python -m examples.simulation.server
```

Abrir `http://127.0.0.1:8766/`. El servidor escucha solo en el equipo local. El módulo de red está en el ejemplo; el motor `src/fovea` sigue superando la comprobación de ausencia de módulos de red. La simulación no utiliza adaptadores de escritorio ni captura imágenes.

`fovea.synthetic_control.SyntheticControl` es una máquina de estados para pruebas. Solo añade tuplas a una lista en memoria. No abre cámaras, no crea ventanas y no genera eventos del sistema operativo. La página de ejemplo presenta sus resultados; ninguno de los dos es un controlador de escritorio.

Empieza en pausa. El host de pruebas llama explícitamente a `resume()` y suministra eventos existentes de Fovea: `TrackingState`, `GazePoint`, `Dwell` y `TargetLeave`. `now_ns` debe usar la misma base de tiempo que los eventos (en captura Fovea usa `time.time_ns()`). El host debe llamar a `tick(now_ns)` periódicamente incluso cuando no llegan eventos.

Con seguimiento activo y confianza al menos 0,8, se registra un movimiento simulado. Una permanencia sobre un objetivo prepara una candidata. Una segunda permanencia sobre un objetivo con ID `confirm` registra un clic izquierdo simulado. El objetivo `cancel` cancela. La candidata caduca en cinco segundos. Una lectura inválida, antigua, futura, fuera de orden, o una pérdida de señal, desarma el controlador y exige volver a armarlo explícitamente.

Los IDs `confirm` y `cancel` están reservados. El anfitrión debe registrar sus rectángulos con `TargetTracker` y mostrar progreso y candidata antes de aceptar cualquier selección. El módulo **no** valida que un evento Dwell externo represente una permanencia real: las pruebas de integración usan el `TargetTracker` existente. No conectar este prototipo a un adaptador real sin diseño, pruebas y autorización adicionales.

## Arrastre, desplazamiento y teclas simuladas

- `drag:start` prepara una pulsación; `confirm` la registra. `drag:drop` seguido de `confirm` registra la liberación. Un arrastre interrumpido por pausa, cierre, señal insuficiente, datos caducados o fuera de orden, o cancelación registra exactamente una liberación. La candidata de soltar caduca a los cinco segundos; el arrastre completo se desarma y libera tras 15 segundos. Son umbrales experimentales de simulación.
- `scroll:up`, `scroll:down`, `scroll:left` y `scroll:right` requieren confirmación y registran un paso en memoria.
- `key:` seguido de un carácter imprimible, como `ñ`, o una de las teclas nombradas en el código, como `Enter`, requiere confirmación y registra una tecla en memoria. No implementa atajos ni un teclado virtual completo.
- Durante un arrastre no se registran teclas, scroll ni clics nuevos. La lista en memoria puede contener las teclas probadas; el módulo no la escribe en disco.

La liberación ante ausencia de eventos depende de que el anfitrión siga llamando a `tick()`. Si el proceso deja de ejecutarse, esta simulación no puede demostrar recuperación de un botón real. Un futuro adaptador necesita su propio mecanismo independiente de liberación y pruebas reales autorizadas.

## Pruebas sin cámara

La instalación se realizó exclusivamente en `.venv` con `pip install -e '.[dev]'`, según `pyproject.toml`; no se aplicó `uv.lock`. La resolución obtenida incluye MediaPipe 0.10.35, NumPy 2.5.5, OpenCV contrib 5.0.0.93 y pytest 9.2.0. Antes de distribuir o comparar con la CI, reproducir las versiones de `uv.lock` y comprobar compatibilidad con el modelo.

Para esta fase se configuró `.venv/audit_guard/sitecustomize.py` para rechazar `cv2.VideoCapture`. Se ubicaron datos, caché y ruta de modelo de prueba dentro de `.venv`; no se descargó el modelo. Comando desde la raíz del proyecto:

```bash
PYTHONPATH="$PWD/.venv/audit_guard" FOVEA_DATA_DIR="$PWD/.venv/testdata" \
FOVEA_MODEL_PATH="$PWD/.venv/testdata/missing.task" XDG_CACHE_HOME="$PWD/.venv/testcache" \
MPLCONFIGDIR="$PWD/.venv/testcache" PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -m pytest -p no:cacheprovider
```

El bloqueo de `cv2.VideoCapture` protege esta ejecución de OpenCV, no es una barrera general contra cualquier API de cámara. Las pruebas del repositorio usan cámaras simuladas; el archivo de modelo ausente hace que se omita la prueba real del grafo.

Resultado local 2026-11-16: 336 pruebas aprobadas, una omitida y nueve advertencias. Las pruebas incluyen la interfaz simulada, recuperación, confirmación, arrastre interrumpido, desplazamiento en cuatro direcciones, caracteres internacionales, teclas especiales y el `TargetTracker` existente. Ruff y mypy pasaron en el código nuevo. `pip check` no encontró requisitos rotos.

## Límites

Faltan otros clics, atajos, una interfaz ocular completa, adaptadores X11/Wayland, control de ventanas, recuperación física, Codex y pruebas humanas. La cámara y el escritorio reales no se han probado y siguen requiriendo autorización explícita.
