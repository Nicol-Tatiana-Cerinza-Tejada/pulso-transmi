# Portal del estudiante

El portal reúne la activación de identidad, la API key, la ronda abierta, los
recibos y el leaderboard. Está disponible en:

`https://pulso-transmi.72-60-245-2.sslip.io/`

## Acceso

Ingresa nombre completo, correo institucional y número de documento tal como
aparecen en la lista oficial. La aplicación normaliza mayúsculas, espacios,
tildes y puntuación del documento. La cédula viaja por HTTPS, no se registra en
logs y se compara contra una firma criptográfica; el VPS no necesita conservarla
en texto legible.

Este acceso es una verificación académica simplificada para el reto. No es un
mecanismo apropiado para notas oficiales ni información de mayor sensibilidad.

## Obtener la API key

1. Presiona **Generar mi API key**.
2. Cópiala o descarga el archivo `.env`.
3. Guárdala en la variable `PULSO_API_KEY`.
4. No la publiques en commits, notebooks, capturas, logs o variables de frontend.

La llave completa se muestra una sola vez. El portal conserva únicamente el
prefijo y el servidor almacena el secreto con scrypt. Si se pierde, el profesor
debe revocar la anterior y habilitar una nueva.

## Tablero

Durante la prueba inicial, el tablero muestra activación y entregas aceptadas;
no inventa una métrica cuando todavía no hay verdad revelada. Cuando comience la
competencia cambia a modo de scoring y presenta accuracy, cobertura y posición.

La cohorte puede ver nombres y estado académico del reto, pero nunca correos,
documentos, llaves, payloads o predicciones individuales.
