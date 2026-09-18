# Activos visuales

## `app/static/images/cohort-avatars.webp`

- Origen: generado con la herramienta integrada de generación de imágenes.
- Fecha de generación: 2026-09-18.
- Uso: sprite de 6 × 6 con 36 avatares sintéticos para distinguir estudiantes en
  el portal y en el leaderboard.
- Dirección del prompt: retratos ilustrados, diversos y ficticios de estudiantes
  universitarios; encuadre frontal consistente; colores individuales combinados
  con verde y oro institucional; sin nombres, texto, marcas ni personas reales.
- Restricciones: no representa identidades reales y nunca debe usarse como dato
  biométrico, fotografía de perfil oficial o mecanismo de autenticación.
- Implementación: el índice `avatar_index` selecciona una celda del sprite; el
  nombre visible sigue siendo la identificación accesible y canónica.
- Asignación: los índices del demo están revisados explícitamente para que la
  presentación del avatar sea coherente con cada identidad mostrada. En datos
  reales, `avatar_index` debe asignarse de manera explícita durante la preparación
  de la cohorte o mediante elección del estudiante; no debe inferirse
  automáticamente a partir del nombre.
