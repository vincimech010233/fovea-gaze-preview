# Fovea — Guion de validación

Duración aproximada de la práctica segura: 12 minutos. Usa un navegador y una sola pestaña. El puntero y Espacio generan señales ficticias. La práctica no abre la webcam ni controla Linux.

## Iniciar y detener

Instala el entorno de Python 3.12 con las instrucciones del [README](README.md). Desde la raíz del repositorio ejecuta:

```bash
.venv/bin/python -m examples.simulation.server
```

Abre [Fovea en el navegador](http://127.0.0.1:8766/). Si dice que el puerto está ocupado, comprueba si ya hay un simulador en esa dirección. No es necesario cerrar otro programa para repetir el benchmark por terminal.

**Esc** detiene la señal del panel. Salir del panel o cambiar de pestaña libera el arrastre simulado. **Ctrl+C** en la terminal cierra el servidor. Si el panel deja de recibir muestras durante más de 250 ms, su vigilancia disarma el controlador.

## Práctica segura

| Tiempo | Acción | Qué anotar |
| --- | --- | --- |
| 0–2 min | Apunta a **Reanudar** hasta llenar la barra. Mantén Espacio unos 0,8 s y suelta. Apunta a **Clic** sin pulsar nada. | Reanudar cambia a Activo solo tras el gesto. Apuntar muestra una candidata y mantiene el contador. |
| 2–4 min | En **Clic**, suelta Espacio antes de 0,5 s. Repite con 0,8–1 s. Prueba **Doble clic**. | El corto no activa. El largo suma 1 al soltar. Doble clic suma 2 una sola vez. |
| 4–6 min | Confirma **Iniciar arrastre**, luego **Soltar arrastre**. Repite el inicio y pulsa Esc. Reanuda, prueba scroll horizontal y vertical y usa **Pausar**. | Cada arrastre termina. Esc libera y exige reanudar. El scroll cambia solo después de confirmar. |
| 6–11 min | Reanuda y confirma dos letras de cada palabra. Elige una sugerencia cuando aparezca. Cronometra desde la primera selección hasta la última. | Pasos, errores, tiempo, molestias y frase final. Prueba un carácter con acento y Ctrl con una letra. Comprueba que el atajo se muestra como texto en el panel. |
| 11–12 min | Mantén el puntero en **Recalibrar** hasta llenar la barra. Mantén la pose simulada un segundo. Tras el aviso de centro calibrado, repite **Reanudar**. | El panel queda en pausa durante la recalibración y exige confirmar la reanudación. |

Frase de 10 palabras:

> hola mundo quiero usar mi ordenador con la cabeza gracias

Cada grupo y cada letra requieren apuntar y confirmar. Para pedir una sugerencia, apunta a la palabra y confirma. La sugerencia añade un espacio. Para repetir una casilla, espera a llenar de nuevo la barra.

Criterios fijados antes de una prueba manual del simulador:

- Cero acciones al apuntar o al usar un gesto corto.
- Una confirmación por gesto deliberado. Un clic doble suma dos clics al contador, una sola vez.
- Cero botones simulados retenidos después de Esc, pérdida de señal o pausa.
- Frase exacta y reanudación explícita tras recalibración. Anota la duración real de la sesión por separado del reloj simulado.

## Prueba humana con webcam

**NO VALIDADO, pendiente de prueba humana.** Este arranque enciende la webcam. Los movimientos y las acciones quedan dentro del panel local. Instala el entorno y descarga el modelo verificado con las instrucciones del [README](README.md).

Cierra el servidor anterior con **Ctrl+C** en su terminal. Después ejecuta:

```bash
bash iniciar-mirada.sh
```

1. Abre [Fovea](http://127.0.0.1:8766/) o recarga la pestaña. Mantén la cabeza cómoda y quieta. Mira cada uno de los nueve puntos amarillos hasta que cambie.
2. En **Ajuste de movimiento**, mira el punto central durante cinco segundos. Gira un poco la cabeza a ambos lados, arriba y abajo. Incluye un pequeño cambio de distancia si es cómodo. Cuando aparezca **Mirada calibrada**, mira **Reanudar** hasta que se llene su barra.
3. Mira **Clic** hasta ver una candidata. El contador debe mantenerse en cero. Mira **Confirmar** hasta llenar la segunda barra. El contador debe subir a uno.
4. Prueba con el teclado. Mira un grupo, luego **Confirmar**, luego una letra y otra vez **Confirmar**. La palabra sugerida se activa del mismo modo.
5. Para ajustar la puntería, mira **Recalibrar** y sigue los puntos otra vez. Para detener el panel, usa **Esc**. Para detener la captura, pulsa **Ctrl+C** en la terminal.

Apuntar dura un segundo por casilla. Los parpadeos breves detienen el avance. Si un fotograma de la cara es dudoso, el panel cancela la candidata y permite elegir otra al recuperar la señal a tiempo. Una pérdida de cara o una pausa de más de 250 ms desarma el panel y libera el arrastre virtual. La calibración se guarda en memoria para esa ejecución.

Prueba manteniendo la vista en una casilla mientras mueves levemente la cabeza o cambias un poco la inclinación del portátil. Comprueba si el punto se mantiene en la casilla y anota las pausas. **Compensación activa** indica que el ajuste aprendido pasó la comprobación interna. **Ajuste básico** indica que se usa la geometría de los ojos. Si cambias mucho el ángulo del portátil, la posición o el tamaño de la ventana, recalibra. Para ejercitar el arrastre, confirma **Iniciar arrastre** y después interrumpe la visión de la cámara. Debe quedar **En pausa**, con el arrastre **liberado**.

## Medir puntería

El indicador muestra **ojos útiles** de 0 a 2 y la cadencia con que llegan lecturas al panel. Si solo se lee un ojo y su ajuste pasó la comprobación, el marcador hueco sirve de referencia. El panel espera a recuperar ambos ojos para aceptar acciones.

Tras calibrar, elige **Medir puntería** o mira la casilla **Probar puntería**. Sigue los cuatro puntos amarillos. Son distintos de los usados para calibrar. Las acciones del panel quedan en pausa durante toda la prueba. Al terminar, se muestra el error medio y el error que abarca nueve de cada diez muestras, expresados en coordenadas normalizadas del panel. Si no puedes seguir los puntos, el botón **Cancelar prueba** vuelve a la vista previa en pausa. Mira **Reanudar** al volver.

## Plantilla de resultados

| Campo | Resultado |
| --- | --- |
| Fecha, modalidad, iluminación, gafas y resolución | |
| Objetivos y error mediano, p95 o máximo con unidad | |
| Rango y estabilidad de cabeza, por eje | |
| Gesto más corto detectado, falsos por minuto y aciertos de 10 | |
| Frase, segundos, pasos y correcciones | |
| Cortes de señal, pausa y liberación | |
| FPS y latencia p50/p95 si aparecen | |
| Tareas completadas sin ayuda y observaciones | |

Anota **NO VALIDADO** junto a cada prueba que quede pendiente.
