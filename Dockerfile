# syntax=docker/dockerfile:1
# HACU en contenedor. Dos etapas: una compila, la otra ejecuta.
#
# Por que 12.8 y no 12.1: la portatil de la exhibicion lleva una RTX 5070 Ti
# (Blackwell, sm_120) y CUDA 12.1 no conoce esa arquitectura —ni para compilar ni
# para generar PTX que el driver pueda traducir al vuelo—. El indice de ruedas
# precompiladas de llama-cpp-python llega hasta cu125 (comprobado), asi que en
# Blackwell NO hay rueda que valga: hay que compilar contra CUDA 12.8. Eso son
# entre quince y veinticinco minutos la primera vez, y cero las siguientes
# gracias al cache de capas.
#
# En una GPU anterior a Blackwell se ahorra la compilacion entera:
#   docker build --build-arg RUEDA_CUDA=cu125 -t hacu .
#
# Arquitecturas habituales para --build-arg CUDA_ARCH:
#   120 Blackwell (RTX 50xx)   89 Ada (RTX 40xx)   86 Ampere (RTX 30xx)
ARG CUDA_VERSION=12.8.2
ARG UBUNTU=ubuntu22.04


# ---------------------------------------------------------------- 1. compilar
FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU} AS constructor

ARG CUDA_ARCH=120
ARG RUEDA_CUDA=""
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3.10-dev python3-pip \
        build-essential cmake git ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Con RUEDA_CUDA se baja la rueda precompilada de ese indice; sin el, se compila
# desde el fuente para la arquitectura pedida. `--no-binary` es lo que impide que
# pip se traiga una rueda de PyPI sin CUDA y deje el modelo corriendo en la CPU,
# que es un fallo que no avisa: solo va veinte veces mas lento.
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p /ruedas && \
    if [ -n "$RUEDA_CUDA" ]; then \
        pip wheel --wheel-dir /ruedas llama-cpp-python --prefer-binary \
            --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/${RUEDA_CUDA}" ; \
    else \
        CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH}" \
        FORCE_CMAKE=1 \
        pip wheel --wheel-dir /ruedas --no-binary llama-cpp-python llama-cpp-python ; \
    fi


# ---------------------------------------------------------------- 2. ejecutar
# `runtime` en vez de `devel`: sin el compilador de CUDA la imagen baja de unos
# 6 GB a unos 2,5 GB, y nada de lo que HACU ejecuta necesita nvcc.
FROM nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU}

# La voz y la ventana se instalan solo si se piden. En Windows ni el audio ni el
# servidor grafico cruzan la frontera del contenedor sin montar mas cosas (ver
# docs/docker.md), asi que la imagen util por defecto es la de consola.
ARG CON_VOZ=0
ARG CON_UI=0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HF_HOME=/opt/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache \
    HF_HUB_OFFLINE=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3-pip \
        libgomp1 \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Capa de dependencias antes que el codigo: cambiar una linea de Python no
# reinstala chromadb ni vuelve a bajar el modelo de embeddings.
COPY requirements.txt requirements-voz.txt requirements-ui.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -v '^llama-cpp-python' requirements.txt > /tmp/req-base.txt \
    && pip install --no-cache-dir -r /tmp/req-base.txt

# PortAudio y libsndfile son bibliotecas del sistema: `pip install sounddevice`
# instala el enlace, no la biblioteca, y sin ella el fallo aparece al abrir el
# microfono y no al instalar.
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$CON_VOZ" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libportaudio2 libsndfile1 \
        && rm -rf /var/lib/apt/lists/* \
        && grep -v 'sys_platform == "win32"' requirements-voz.txt > /tmp/req-voz.txt \
        && pip install --no-cache-dir -r /tmp/req-voz.txt ; \
    fi

# PySide6 arrastra media docena de bibliotecas de X que no vienen en la imagen de
# CUDA; sin ellas Qt falla al arrancar con "could not load the Qt platform plugin".
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$CON_UI" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
            libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libdbus-1-3 libfontconfig1 \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir -r requirements-ui.txt ; \
    fi

# La rueda con CUDA que se compilo arriba.
COPY --from=constructor /ruedas /ruedas
RUN pip install --no-cache-dir /ruedas/llama_cpp_python-*.whl && rm -rf /ruedas

# El modelo de embeddings entra en la imagen: con `docker run --rm` y sin volumen
# de cache eran ~470 MB en cada encendido, y una exhibicion sin red no arranca.
# `a+rX` porque quien lo ejecuta no es root y solo tiene que leerlo.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')" \
    && chmod -R a+rX /opt/hf-cache

COPY . .

# Nada de esto necesita root, y el contenedor escribe en volumenes montados desde
# el anfitrion: corriendo como root, los ficheros que crea salen con propietario
# root y despues no hay quien los borre desde Windows ni desde Linux.
RUN useradd --create-home --uid 1000 hacu \
    && mkdir -p /app/models /app/chroma_db /app/logs /app/datos \
    && chown -R hacu:hacu /app
USER hacu

# El modelo GGUF, el indice y la memoria se montan, no se copian: meterlos en la
# imagen la haria de varios gigas y la ataria a unos datos concretos.
# /app/datos guarda hacu_memory.db; sin montarlo, cada `docker run --rm` arranca
# sin memoria. Se apunta ahi con HACU_DB (ver docs/docker.md).
VOLUME ["/app/models", "/app/chroma_db", "/app/logs", "/app/datos"]

# Falla pronto y con un mensaje claro si falta el GGUF o el indice, en vez de
# reventar a mitad del arranque delante del publico.
HEALTHCHECK --interval=60s --timeout=20s --start-period=180s --retries=2 \
    CMD python -c "import sys,pathlib; sys.exit(0 if any(pathlib.Path('/app/models').glob('*.gguf')) else 1)"

# Consola por defecto: es lo unico que funciona igual dentro y fuera del
# contenedor. La ventana y la voz necesitan lo de docs/docker.md.
CMD ["python", "run_hacu.py"]
