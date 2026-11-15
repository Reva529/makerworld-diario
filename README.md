# Diseños del día

Cada día a las 6:00 (hora del centro de México) una tarea de GitHub Actions busca diseños
nuevos y en tendencia de **Hobby & DIY** y **Toys & Games** en MakerWorld, y elige 20 que:

- tienen un perfil de impresión para **Bambu A1** (o A1 mini), y
- sin AMS imprimen en **10 horas o menos**, o con AMS en **7 horas o menos**.

La página (GitHub Pages, carpeta `docs/`) muestra las tarjetas con foto. Con **Guardar** y
**Descartar** la app aprende tus gustos (etiquetas, palabras del título, categorías, autores).

## Archivos

| Archivo | Para qué |
|---|---|
| `scripts/fetch.py` | Recolector, filtros y puntuación (ajustes al inicio del archivo) |
| `.github/workflows/daily.yml` | Tarea diaria |
| `docs/index.html` | La página |
| `docs/data/feed.json` | Selecciones de los últimos 7 días |
| `docs/data/state.json` | Tus decisiones (lo escribe la página con tu token) |
| `data/seen.json` | Diseños ya revisados, para no repetirlos |

## Token para la página

GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token.
Acceso solo a este repositorio, permiso **Contents: Read and write**. Pégalo en ⚙︎ en la página;
se guarda solo en ese dispositivo.

Usa la API interna (no oficial) de MakerWorld, con pocas peticiones y pausas entre ellas.
