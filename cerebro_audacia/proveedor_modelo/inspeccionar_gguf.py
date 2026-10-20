"""Inspección de metadatos GGUF de Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf.

Lee la cabecera binaria del estándar GGUF (v3) sin requerir librerías externas.
"""

import struct
from pathlib import Path

GGUF_MAGIC = 0x46554747  # 'GGUF' en little endian

# Tipos de valores GGUF
GGUF_TYPE_UINT8 = 0
GGUF_TYPE_INT8 = 1
GGUF_TYPE_UINT16 = 2
GGUF_TYPE_INT16 = 3
GGUF_TYPE_UINT32 = 4
GGUF_TYPE_INT32 = 5
GGUF_TYPE_FLOAT32 = 6
GGUF_TYPE_BOOL = 7
GGUF_TYPE_STRING = 8
GGUF_TYPE_ARRAY = 9
GGUF_TYPE_UINT64 = 10
GGUF_TYPE_INT64 = 11
GGUF_TYPE_FLOAT64 = 12

def read_string(f):
    len_bytes = f.read(8)
    if not len_bytes:
        return ""
    length = struct.unpack("<Q", len_bytes)[0]
    return f.read(length).decode("utf-8", errors="replace")

def read_value(f, val_type):
    if val_type == GGUF_TYPE_UINT8:
        return struct.unpack("<B", f.read(1))[0]
    elif val_type == GGUF_TYPE_INT8:
        return struct.unpack("<b", f.read(1))[0]
    elif val_type == GGUF_TYPE_UINT16:
        return struct.unpack("<H", f.read(2))[0]
    elif val_type == GGUF_TYPE_INT16:
        return struct.unpack("<h", f.read(2))[0]
    elif val_type == GGUF_TYPE_UINT32:
        return struct.unpack("<I", f.read(4))[0]
    elif val_type == GGUF_TYPE_INT32:
        return struct.unpack("<i", f.read(4))[0]
    elif val_type == GGUF_TYPE_FLOAT32:
        return struct.unpack("<f", f.read(4))[0]
    elif val_type == GGUF_TYPE_BOOL:
        return bool(struct.unpack("<B", f.read(1))[0])
    elif val_type == GGUF_TYPE_STRING:
        return read_string(f)
    elif val_type == GGUF_TYPE_UINT64:
        return struct.unpack("<Q", f.read(8))[0]
    elif val_type == GGUF_TYPE_INT64:
        return struct.unpack("<q", f.read(8))[0]
    elif val_type == GGUF_TYPE_FLOAT64:
        return struct.unpack("<d", f.read(8))[0]
    elif val_type == GGUF_TYPE_ARRAY:
        elem_type, count = struct.unpack("<IQ", f.read(12))
        arr = []
        for _ in range(min(count, 5)):  # Guardar solo primeros 5 elementos si es un array grande
            arr.append(read_value(f, elem_type))
        if count > 5:
            # Saltar el resto si no son strings complejos
            if elem_type in (GGUF_TYPE_UINT8, GGUF_TYPE_INT8, GGUF_TYPE_BOOL):
                f.seek((count - 5) * 1, 1)
            elif elem_type in (GGUF_TYPE_UINT16, GGUF_TYPE_INT16):
                f.seek((count - 5) * 2, 1)
            elif elem_type in (GGUF_TYPE_UINT32, GGUF_TYPE_INT32, GGUF_TYPE_FLOAT32):
                f.seek((count - 5) * 4, 1)
            elif elem_type in (GGUF_TYPE_UINT64, GGUF_TYPE_INT64, GGUF_TYPE_FLOAT64):
                f.seek((count - 5) * 8, 1)
            elif elem_type == GGUF_TYPE_STRING:
                for _ in range(count - 5):
                    read_string(f)
        return arr
    else:
        raise ValueError(f"Tipo desconocido: {val_type}")

def inspect_gguf(path: Path):
    print(f"=== Inspeccionando Metadatos GGUF: {path.name} ===")
    with open(path, "rb") as f:
        magic, version, tensor_count, kv_count = struct.unpack("<IIQQ", f.read(24))
        if magic != GGUF_MAGIC:
            print(f"ERROR: Magic invalido {hex(magic)}, esperado {hex(GGUF_MAGIC)}")
            return
            
        print(f"GGUF Version      : {version}")
        print(f"Total Tensores    : {tensor_count}")
        print(f"Total Claves KV   : {kv_count}")
        
        metadata = {}
        for i in range(kv_count):
            key = read_string(f)
            val_type = struct.unpack("<I", f.read(4))[0]
            val = read_value(f, val_type)
            metadata[key] = val
            
        print("\n--- Metadatos Principales del Modelo ---")
        interesting_keys = [
            "general.architecture",
            "general.name",
            "general.file_type",
            "general.quantization_version",
            "llama.context_length",
            "llama.embedding_length",
            "llama.block_count",
            "llama.feed_forward_length",
            "llama.attention.head_count",
            "llama.attention.head_count_kv",
            "llama.rope.freq_base",
            "tokenizer.chat_template"
        ]
        
        for k in interesting_keys:
            if k in metadata:
                v = metadata[k]
                if k == "tokenizer.chat_template":
                    # Imprimir snippet de plantilla
                    preview = str(v)[:150].replace('\n', ' ')
                    print(f"{k}: {preview}...")
                else:
                    print(f"{k}: {v}")
                    
        return metadata

if __name__ == "__main__":
    modelo = Path(__file__).resolve().parent.parent.parent / "models" / "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    inspect_gguf(modelo)
