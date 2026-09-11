# Imagen base oficial de NVIDIA con soporte CUDA 12.1
FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Python 3.10 y toolchain de C++ (necesario para compilar llama.cpp)
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3.10-venv \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/python3.10 /usr/bin/python

WORKDIR /app

# Capa de dependencias separada para aprovechar el cache de Docker.
# llama-cpp-python se instala desde el indice CUDA, no desde requirements.txt.
# El resto (chromadb, sentence-transformers) sale de la capa cacheada.
COPY requirements.txt .
RUN grep -v '^llama-cpp-python' requirements.txt > /tmp/req-base.txt \
    && pip install --no-cache-dir -r /tmp/req-base.txt

# El modelo de embeddings se descarga en el build, no en cada arranque: con
# `docker run --rm` y sin volumen de cache eran ~470 MB por cada encendido.
ENV HF_HOME=/opt/hf-cache
ENV SENTENCE_TRANSFORMERS_HOME=/opt/hf-cache
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

ENV CMAKE_ARGS="-DGGML_CUDA=on"
ENV FORCE_CMAKE="1"
ENV CUDA_DOCKER_ARCH=all

RUN pip install --no-cache-dir llama-cpp-python \
    --prefer-binary \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

# Codigo de la aplicacion
COPY . .

CMD ["python", "run_hacu.py"]
