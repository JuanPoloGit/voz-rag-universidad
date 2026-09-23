# GitHub: publicar y replicar el proyecto

Cómo poner HACU en un repositorio y cómo alguien más lo baja y lo pone a
funcionar. Pensado para que dentro de un año, con la exhibición montada otra vez,
nadie tenga que reconstruir nada de memoria.

---

## Qué NO entra en el repositorio, y por qué

Un repositorio de Git guarda **todas** las versiones de todo lo que entra. Un
archivo de 5 GB añadido y borrado sigue pesando 5 GB en el historial, para
siempre, para todo el que clone. GitHub además rechaza archivos de más de 100 MB.

| Se queda fuera | Peso | De dónde sale |
|---|---|---|
| `models/*.gguf` | ~4,9 GB | Se descarga de Hugging Face ([instalacion.md](instalacion.md#4-el-modelo)) |
| `models/voz/` | ~110 MB por voz | `python -m hacu.voz --descargar` |
| `chroma_db/` | ~decenas de MB | Se reconstruye sola desde `documents/` al arrancar |
| `hacu_memory.db` | variable | **Datos personales de visitantes.** Nunca al repositorio |
| `logs/` | crece sin techo | Se abre uno nuevo en cada arranque |
| `pruebas/informes/` | ~1 MB por corrida | Miden una versión concreta del corpus: envejecen mal |
| `venv/`, `__pycache__/`, `*.wav` | — | Basura de ejecución |

Todo eso ya está en `.gitignore`. No lo quites.

> **Git LFS no es la solución para el modelo.** LFS mueve el peso a otro
> almacén, pero sigue siendo peso, y la cuota gratuita de GitHub se mide en
> gigabytes de almacenamiento y de tráfico al mes: un puñado de clones de un
> `.gguf` de 4,9 GB la agota. El modelo es un artefacto público con URL propia:
> se descarga, no se versiona.

Lo que **sí** entra: el código, el corpus de `documents/` (son 60 KB de Markdown
y son el conocimiento de HACU), las pruebas, las herramientas y esta
documentación.

---

## A. Publicar el proyecto por primera vez

### A.1 Comprobar que no hay nada que no deba subir

```powershell
cd D:\Proyectos\voz-rag-universidad

python -m herramientas.limpiar         # lista lo prescindible; con --aplicar lo borra
git status --short
git status --ignored --short           # confirma que el modelo y la memoria salen como ignorados
```

Antes del primer `commit`, revisa el tamaño de lo que va a entrar:

```powershell
git add -A
git status --short | Measure-Object -Line
git ls-files -s | ForEach-Object { ($_ -split "\t")[1] } |
  ForEach-Object { Get-Item $_ } | Sort-Object Length -Descending |
  Select-Object -First 10 Length, Name
```

Si en esa lista aparece algo de más de un megabyte que no sea un `.md` del
corpus, **para y averigua qué es** antes de seguir.

### A.2 Inicializar y primer commit

Si el repositorio ya existe (hay carpeta `.git`), salta a A.3.

```powershell
git init
git branch -M main
git add -A
git commit -m "HACU: asistente expositor de AudacIA"
```

`.gitattributes` fuerza finales de línea LF dentro del repositorio. Sin eso, un
checkout en Windows escribe CRLF y el mismo árbol clonado en Linux o construido
en el contenedor difiere del original, y `git diff` marca archivos enteros como
cambiados sin que nadie los haya tocado.

### A.3 Crear el repositorio remoto

Con la línea de órdenes de GitHub (`gh`, <https://cli.github.com>):

```powershell
gh auth login
gh repo create voz-rag-universidad --private --source . --remote origin --push
```

O a mano: crear el repositorio **vacío** en <https://github.com/new> (sin README,
sin `.gitignore`, sin licencia — los tiene ya el proyecto) y después:

```powershell
git remote add origin https://github.com/<usuario>/voz-rag-universidad.git
git push -u origin main
```

> **Privado o público.** El corpus describe proyectos de investigación de la
> Universidad Simón Bolívar, con nombres de personas, patentes y aliados. Que eso
> se publique no es una decisión técnica: consúltalo con el centro antes de poner
> el repositorio en público. Con `--private` siempre se puede abrir después; al
> revés no.

### A.4 Marcar la versión que se exhibió

Cuando una versión se lleva a una exhibición, ponle una etiqueta. Es lo que
permite volver exactamente a lo que funcionó ese día:

```powershell
git tag -a exhibicion-2026-09 -m "Versión llevada a la exhibición de septiembre"
git push origin exhibicion-2026-09
```

---

## B. Replicar el proyecto en otra máquina

Los pasos completos, desde un equipo que no tiene nada.

```powershell
# 1. El código
git clone https://github.com/<usuario>/voz-rag-universidad.git
cd voz-rag-universidad

# 2. El entorno
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install llama-cpp-python --prefer-binary `
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

# 3. El modelo (~4,9 GB; no viene en el repositorio)
#    Ver instalacion.md, paso 4
#    -> models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf

# 4. Los extras
pip install -r requirements-ui.txt
pip install -r requirements-voz.txt
python -m hacu.voz --descargar

# 5. Comprobar antes de confiar
python -m pruebas.test_unidades
python run_hacu.py --debug
```

El índice vectorial **no se clona y no hace falta clonarlo**: `bootstrap.py` lo
reconstruye desde `documents/` en el primer arranque, y en los siguientes solo
sincroniza lo que haya cambiado. Un `chroma_db/` copiado de otra máquina, con
otra versión de ChromaDB, es una fuente de problemas y no ahorra nada.

Detalle de cada paso y qué hacer cuando falla: [instalacion.md](instalacion.md).

---

## C. Trabajar en el proyecto

### Ritmo de trabajo

```powershell
git switch -c voz/callar-a-mitad-de-frase     # una rama por cambio
# ... trabajar ...
python -m pruebas.test_unidades               # NO se comitea en rojo
git add -A
git commit -m "La orden de callar corta a mitad de frase, no en el siguiente punto"
git push -u origin voz/callar-a-mitad-de-frase
gh pr create --fill
```

`python -m pruebas.test_unidades` tarda dos segundos y no necesita GPU: no hay
excusa para saltárselo. Las 742 comprobaciones existen porque cada una cubre algo
que se rompió de verdad alguna vez.

### Mensajes de commit

Describen **qué cambia de comportamiento**, no qué archivo se tocó. «Arreglos
varios» no le sirve a nadie dentro de seis meses; «El índice se carga, no se
busca: buscarlo devolvía 5 proyectos de 32» sí.

### Cuando cambia el corpus

`documents/` no se edita a mano: se regenera desde los informes en Word (ver
[corpus.md](corpus.md)). Después de regenerar, **medir siempre** antes de
comitear:

```powershell
python -m pruebas.recuperacion --n 6 --detalle
```

### Ganchos de pre-commit (opcional pero recomendado)

Un gancho que corra la suite antes de cada commit evita el commit en rojo:

```bash
# .git/hooks/pre-commit   (chmod +x; no se versiona: es local a cada clon)
#!/bin/sh
python -m pruebas.test_unidades || exit 1
```

### Integración continua

`pruebas/test_unidades.py` corre sin GPU, sin red y sin tarjeta de sonido — está
escrito así a propósito, y el bloque de la interfaz usa el backend `offscreen` de
Qt. Eso lo hace apto para GitHub Actions tal cual. Guarda esto como
`.github/workflows/pruebas.yml`:

```yaml
name: pruebas
on: [push, pull_request]
jobs:
  unidades:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install langchain-text-splitters numpy PySide6
      - run: python -m pruebas.test_unidades
        env:
          QT_QPA_PLATFORM: offscreen
```

Nota: el flujo **no** instala `requirements.txt`, y es a propósito. La suite no
necesita `llama-cpp-python` ni ChromaDB: los bloques que los usarían trabajan con
dobles, y esas dos librerías se importan de forma perezosa. Las tres del `pip
install` son las únicas que se importan a nivel de módulo en el camino de las
pruebas. La suite omite el bloque que no puede correr en vez de fallar, así que la
cuenta depende de lo instalado. Medido en entornos limpios: **742/742** con todo,
**741/741** con esas tres (sin ChromaDB no corre una comprobación de red),
**710/710** sin PySide6 pero con ChromaDB, y **709/709** sin ninguna de las dos
—que es el caso del flujo—. Si en el futuro un bloque nuevo importa
el motor real, habrá que añadir la instalación y el flujo pasará de segundos a
minutos.

---

## D. Problemas típicos

| Síntoma | Qué pasa | Solución |
|---|---|---|
| `remote: error: File ... is 4.90 GB; this exceeds GitHub's file size limit` | El modelo entró al commit | `git rm --cached models/*.gguf`, confirmar `.gitignore`, rehacer el commit |
| El modelo entró en un commit **antiguo** | Sigue en el historial aunque ya no esté en el árbol | Reescribir con `git filter-repo`, o empezar un repositorio limpio si aún no lo ha clonado nadie |
| `git diff` marca archivos enteros sin haberlos tocado | Finales de línea | `git add --renormalize .` una vez; `.gitattributes` evita que vuelva a pasar |
| Un clon nuevo no arranca | Falta el modelo, o las voces | Son descargas aparte: pasos 3 y 4 de la sección B |
| `hacu_memory.db` aparece en `git status` | Alguien lo añadió con `-f`, o el `.gitignore` se editó | `git rm --cached hacu_memory.db` y revisar quién tiene ya ese commit: **son datos de personas** |

---

## E. Qué revisar antes de hacer público el repositorio

1. `git log --all --stat | Select-String "hacu_memory|\.gguf|\.wav"` — que no haya
   datos de visitantes ni binarios pesados en **ningún** punto del historial.
2. El corpus de `documents/` lo aprueba el centro, no el repositorio.
3. `logs/` fuera: el log registra las preguntas de los visitantes.
4. Licencia: el proyecto no la declara todavía. Sin archivo `LICENSE`, un
   repositorio público sigue siendo «todos los derechos reservados» y nadie puede
   reutilizarlo legalmente — decidir con la universidad si eso es lo que se quiere.
