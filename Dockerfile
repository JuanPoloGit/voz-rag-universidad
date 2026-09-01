# Imagen base oficial de NVIDIA con soporte CUDA 12.1
FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

# Variables de entorno para evitar que la consola pida interacciones
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instalar Python 3.10 y los compiladores de C++ (necesarios para llama.cpp)
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3.10-venv \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

# Configurar python3 como comando principal
RUN ln -s /usr/bin/python3.10 /usr/bin/python

# Crear la carpeta de trabajo dentro del contenedor
WORKDIR /app

# Copiar primero los requerimientos para aprovechar el caché de Docker
COPY requirements.txt .

# Instalar librerías básicas (ChromaDB, LangChain, etc.)
RUN pip install --no-cache-dir -r requirements.txt

# Configurar las rutas y variables de compilación para la GPU
ENV CMAKE_ARGS="-DGGML_CUDA=on"
ENV FORCE_CMAKE="1"
ENV CUDA_DOCKER_ARCH=all

# Compilar forzando el uso de las librerías CUDA del sistema
RUN pip install --no-cache-dir llama-cpp-python \
    --prefer-binary \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
# Copiar el resto de los scripts (test_llm.py, rag_engine.py, etc.)
COPY . .

# El comando que ejecutará el contenedor al encenderse
CMD ["python", "test_llm.py"]