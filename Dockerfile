FROM nvidia/cuda:12.8.1-devel-ubuntu24.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Model publisher tested this revision, including the custom sigma schedule fix.
RUN git init . \
    && git remote add origin https://github.com/AtomicBot-ai/stable-diffusion.cpp.git \
    && git fetch --depth 1 origin 36f1b1adce7929ea2d028074ea05a9f9248eda8f \
    && git checkout --detach FETCH_HEAD \
    && git submodule update --init --depth 1 ggml

RUN cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DSD_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES="86;89" \
    -DGGML_NATIVE=OFF \
    -DSD_BUILD_SHARED_LIBS=OFF \
    -DSD_BUILD_SHARED_GGML_LIB=OFF \
    -DSD_SERVER_BUILD_FRONTEND=OFF \
    -DSD_WEBP=OFF -DSD_WEBM=OFF \
    && cmake --build build --target sd-server --parallel 2

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgomp1 python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/runpod-volume/huggingface \
    HF_HUB_CACHE=/runpod-volume/huggingface/hub \
    NO_PROXY=localhost,127.0.0.1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=builder /src/build/bin/sd-server /usr/local/bin/sd-server
COPY worker.py test_input.json ./

CMD ["python", "-u", "worker.py"]
