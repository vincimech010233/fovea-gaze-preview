# Adaptador X11 experimental

`fovea.x11_adapter` contiene un adaptador de puntero X11 basado en las bibliotecas del sistema `libX11.so.6` y `libXtst.so.6`. Está desconectado del motor, de la interfaz simulada y de la CLI. No se inicia automáticamente. El constructor exige `enabled=True`; esto es una barrera de código, no sustituye el consentimiento de la persona para controlar el escritorio real.

El proceso auxiliar posee la conexión X11. Acepta movimiento normalizado, pulsación y liberación de botones, y scroll mediante los botones X11. `apply()` traduce un subconjunto de las acciones sintéticas. El teclado real, otros modos de clic, selección de monitor y coordinación segura de arrastre con una interfaz ocular siguen pendientes. En particular, la liberación actual no mueve el cursor al punto de destino: evita moverlo tras pérdida de señal, pero no resuelve aún la suelta mediante un panel de confirmación. No conectar este prototipo a un escritorio real todavía.

## Recuperación

- El host debe llamar a `heartbeat()` desde su bucle de control. Si el auxiliar no recibe una orden o latido durante 0,75 s, si el canal se cierra, o si recibe una orden inválida, intenta liberar cada botón que haya pulsado y cierra la conexión.
- El controlador que detecte una pérdida de seguimiento debe dejar de enviar órdenes, solicitar `release()` y desarmarse. Si el proceso principal muere y el canal se cierra, el auxiliar intenta la misma liberación. `SIGTERM` y `SIGINT` al auxiliar también ejecutan la limpieza de Python.
- Un segundo proceso independiente confirma el registro de cada posible pulsación **antes** de que el auxiliar la envíe a X11. El registro se borra solo después de una liberación exitosa. Si el auxiliar muere o deja de responder durante 1,5 s, el vigilante lo detiene usando `pidfd` y trata de liberar los botones desde otra conexión. La ausencia de `pidfd` impide iniciar el auxiliar.
- Si X11 no está disponible, el vigilante reintenta hasta 30 veces, separadas por 0,5 s. Cada intento se ejecuta en un proceso que se termina si tarda más de 1 s. Si no logra liberar, se conserva el fallo; no se rearma el control. Son límites experimentales, no tiempos medidos con X11 real.
- Si la liberación falla, el auxiliar termina con error; el host debe considerar inseguro el control. `close()` informa de una salida anómala o una espera excedida. Si el auxiliar muere, el host consulta `recovery_status()`. Obtiene `recovering` hasta que el guardián informa `released` o `failed`. Un canal cerrado sin resultado o 50 s sin respuesta dan `unknown`. Cualquier estado distinto de `running` bloquea órdenes. Un resultado positivo solo confirma la secuencia del guardián, que puede haber constatado que no había botones en el registro.
- **Límites:** un fallo simultáneo del vigilante y el auxiliar, un servidor X que no vuelve durante el plazo, un cambio de `DISPLAY` o una operación del sistema que no termina tras `SIGKILL` pueden impedir la recuperación. Una pulsación registrada antes de ejecutarse puede causar una liberación conservadora innecesaria; no se ha verificado cómo afecta XTEST al ratón físico. Sigue siendo necesaria una salida de recuperación externa y no debe usarse para controlar el escritorio real aún.

La semántica de XTEST está descrita por [X.Org](https://www.x.org/docs/Xext/xtestlib.pdf). La sincronización se solicita tras cada evento; el comportamiento efectivo y la recuperación en un servidor real no se han comprobado.

## Pruebas realizadas

Las pruebas usan bibliotecas X11 falsas y un escritorio falso. Un ensayo mata el auxiliar con `SIGKILL` tras pulsar; el vigilante independiente registra la liberación. Otros cubren controlador o auxiliar bloqueado, ausencia de botón pulsado, X11 simulado que se recupera tras dos fallos, indisponibilidad permanente, operación de recuperación bloqueada y ausencia de confirmación del vigilante. No se abrió la cámara ni se enviaron eventos a X11 real.

Resultado local 2026-11-16: 357 pruebas pasan, una queda omitida por no estar descargado el modelo y hay nueve advertencias. El control de red estático del motor, Ruff y mypy de ambos módulos pasan. Estos resultados no prueban precisión ocular ni recuperación real.
