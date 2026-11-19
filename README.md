# Fovea Gaze Preview

[English](#english) · [Español](#español)

## English

Fovea Gaze Preview is an experimental Linux accessibility project. It turns iris movement from a webcam into a pointer inside a local browser panel. Select a control by holding your gaze, then look at **Confirmar** to carry out the action.

- Nine-point gaze calibration with a separate step for learning compensation for small head movements.
- Preview with one visible eye when that eye’s calibration passes validation. Actions require both eyes.
- Eye detection and frame-rate indicators, plus a four-point accuracy test separate from calibration.
- Local processing with a camera-free simulator. The browser panel never sends input to the desktop.

**Status.** 106 targeted tests passed with the camera blocked. Early webcam trials showed unstable tracking. Reliable pointer control with a webcam remains unverified.

### Run locally

1. From the repository root, create the Python 3.12 environment shown below.
2. Try **Simulation** with the camera off. For **Webcam preview**, fetch the verified model first. That command downloads only the model.
3. Open [the local panel](http://127.0.0.1:8766/). In webcam preview, follow nine points, then hold your gaze at the center while moving your head slightly. Look at **Reanudar** to activate the panel or use **Medir puntería** to check accuracy. Press **Ctrl+C** in the terminal to stop.

## Español

Fovea Gaze Preview es un proyecto experimental de accesibilidad para Linux. Usa el movimiento de los iris captado por una webcam para mover un puntero dentro de un panel local. Mantén la mirada en un control para seleccionarlo y después mira **Confirmar** para ejecutar la acción.

- Calibración de nueve puntos con un paso adicional que aprende a compensar pequeños movimientos de cabeza.
- Vista previa con un solo ojo visible si su calibración pasa la comprobación. Las acciones requieren los dos ojos.
- Indicadores de ojos detectados y fotogramas por segundo, más una prueba de puntería de cuatro puntos separada de la calibración.
- Procesamiento local y simulador sin cámara. El panel del navegador no envía acciones al escritorio.

**Estado.** Pasaron 106 pruebas dirigidas con la cámara bloqueada. Las primeras pruebas con webcam mostraron un seguimiento inestable. El manejo fiable del puntero con webcam sigue sin validar.

### Ejecutar en local

1. Desde la raíz del repositorio, crea el entorno de Python 3.12 que se indica abajo.
2. Usa **Simulación** para probar sin cámara. Para la **Vista previa con webcam**, descarga primero el modelo verificado. Ese comando solo descarga el modelo.
3. Abre [el panel local](http://127.0.0.1:8766/). En la vista previa con webcam, sigue nueve puntos y después mantén la mirada en el centro mientras mueves un poco la cabeza. Mira **Reanudar** para activar el panel o usa **Medir puntería** para comprobar la precisión. Pulsa **Ctrl+C** en la terminal para detenerlo.

## Commands · Comandos

### Setup · Instalación

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

### Simulation · Simulación

```bash
python -m examples.simulation.server
```

### Webcam preview · Vista previa con webcam

Stop the simulator before running the preview. Detén el simulador antes de ejecutar la vista previa.

```bash
export FOVEA_MODEL_PATH="$PWD/.local-model/face_landmarker.task"
python scripts/download_mediapipe_model.py
python -m examples.simulation.server --camera-preview 0
```

### Gaze regression tests · Pruebas de mirada

These tests use synthetic landmarks. Estas pruebas usan puntos faciales sintéticos.

```bash
python -m pytest tests/test_gaze_preview.py tests/test_eye_geometry.py tests/test_gaze_check.py
```

## Credits and license · Créditos y licencia

Based on [Fovea by Wega Labs](https://github.com/Wega-Labs/fovea). Basado en [Fovea de Wega Labs](https://github.com/Wega-Labs/fovea).

This derivative adds the local gaze panel, calibration, diagnostics and synthetic tests. Esta versión derivada añade el panel ocular local, la calibración, el diagnóstico y pruebas sintéticas.

[Apache-2.0](LICENSE) · [Guía de validación en español](PAQUETE_VALIDACION.md)
