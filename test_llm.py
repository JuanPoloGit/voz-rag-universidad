import os
import sys
import time
import torch
from memory_manager import ConversationalMemory
from rag_engine import LocalRAGEngine

def iniciar_sistema_exposicion():
    print("--- 1. DIAGNÓSTICO DE HARDWARE Y CUDA ---")
    
    if not torch.cuda.is_available():
        print("❌ FAILED: PyTorch reporta que CUDA NO está disponible.")
        return

    print("✅ SUCCESS: PyTorch detecta hardware de CUDA.")
    gpu_name = torch.cuda.get_device_name(0)
    print(f"📌 Dispositivo detectado: {gpu_name}")

    os.environ["GGML_CUDA_FORCE_CUBLAS"] = "1"

    if os.name == 'nt':
        dll_path = os.path.join(os.path.dirname(__file__), 'venv', 'Lib', 'site-packages', 'llama_cpp', 'lib')
        if os.path.exists(dll_path):
            os.add_dll_directory(dll_path)

    from llama_cpp import Llama

    model_path = "./models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

    if not os.path.exists(model_path):
        print(f"❌ Error: No se encuentra el archivo del modelo en {model_path}")
        return

    nombre_modelo = os.path.basename(model_path)
    print(f"🚀 Cargando {nombre_modelo} en la VRAM de la {gpu_name}...")

    llm = Llama(
        model_path=model_path,
        n_ctx=16384,
        n_gpu_layers=-1,  
        n_batch=512,
        n_threads=8,
        verbose=False
    )

    print(f"✅ ¡Modelo cargado con éxito y listo para la instalación!")

    rag = LocalRAGEngine(doc_folder="./documents", db_path="./chroma_db")
    memory = ConversationalMemory(storage_path="exposicion_memory.json", max_exact_turns=3)

    system_prompt = (
        "Eres el asistente virtual interactivo y experto de AudacIA, en la Universidad Simón Bolívar. "
        "REGLAS DE OPERACIÓN ESTRICTAS: "
        "1. IDENTIDAD Y NATURALEZA: No tienes un cuerpo físico, ni extremidades, ni capacidad de movimiento autónomo por pasillos o laboratorios; eres un sistema de inteligencia artificial y voz/texto integrado en una interfaz digital de asistencia. Nunca inventes que puedes caminar, moverte o trasladarte. "
        "2. PRECISIÓN INSTITUCIONAL: No cruces información de AudacIA con la de la Universidad general a menos que se te pida explícitamente. "
        "3. TONO CONVERSACIONAL Y HUMANO: Explica los conceptos de manera fluida, natural y hablada, como un guía experto. NUNCA respondas con listas frías de viñetas o fragmentos copiados de manuales. "
        "4. CERO ESPECULACIÓN: Si no hay registro institucional sobre algo, apóyate en tu conocimiento general interno con naturalidad."
    )

    if not memory.system_prompt:
        memory.add_message("system", system_prompt)

    print("\n--- BUCLE DE EXPOSICIÓN INTELIGENTE (ENRUTADOR + RAG LOCAL + STREAMING) ---")
    print("Escribe 'limpiar' para reiniciar la memoria, o 'salir' para terminar.\n")

    while True:
        try:
            pregunta_usuario = input("\nUsuario: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not pregunta_usuario:
            continue

        if pregunta_usuario.lower() == "salir":
            print("Saliendo del sistema...")
            break
        
        if pregunta_usuario.lower() == "limpiar":
            memory.clear_memory()
            memory.add_message("system", system_prompt)
            print("🧹 ¡Memoria limpiada y reiniciada con éxito!")
            continue

        # --- ENRUTADOR CON MEMORIA AMPLIADA PARA PREGUNTAS CORTAS ---
        historial_reciente = memory.get_formatted_history()[-6:] 
        texto_historial = "\n".join([f"{msg['role']}: {msg['content']}" for msg in historial_reciente if msg['role'] != 'system'])

        prompt_enrutador = (
            "Eres un clasificador lógico estricto. Lee el historial reciente de la conversación y la nueva pregunta del usuario.\n"
            "REGLA CRÍTICA DE CONTEXTO: Si la nueva pregunta es muy corta o un seguimiento (ej. 'por qué?', 'y los otros?', 'explícame más'), "
            "debe heredar EXACTAMENTE la misma categoría de la pregunta anterior en el historial.\n\n"
            "Clasifica la intención en una de estas tres categorías:\n"
            "1. AUDACIA: Si la pregunta menciona explícitamente el centro de investigación 'AudacIA' o sus proyectos tecnológicos (Orion, Holosand, Tanque, Fatiga Visual, Juntas de Rieles, Robots Programables).\n"
            "2. UNIVERSIDAD: Si la pregunta se refiere a la Universidad Simón Bolívar en general, sus facultades, historia, sedes, admisiones, directivos o carreras.\n"
            "3. GENERAL: Si la pregunta es sobre cultura general, personajes históricos, ciencia general, política, saludos o comentarios casuales hacia el asistente.\n\n"
            f"Historial de contexto:\n{texto_historial}\n\n"
            f"Nueva pregunta a evaluar: {pregunta_usuario}\n\n"
            "Responde ÚNICAMENTE con una de las tres palabras: AUDACIA, UNIVERSIDAD o GENERAL. No escribas nada más."
        )

        print("[i] Evaluando la intención de la pregunta...")
        router_response = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt_enrutador}],
            max_tokens=10,
            temperature=0.0
        )
        
        intencion = router_response["choices"][0]["message"]["content"].strip().upper()
        
        # --- BÚSQUEDA CONDICIONAL SEGMENTADA ---
        contexto_externo = None
        fuente_info = None

        if "AUDACIA" in intencion:
            print("[i] 🔎 Intención de AudacIA detectada -> Buscando en colección exclusiva de AudacIA...")
            contexto_externo = rag.search_audacia_docs(pregunta_usuario, n_results=30)
            fuente_info = "Documentación Interna de AudacIA"
        elif "UNIVERSIDAD" in intencion:
            print("[i] 🔎 Intención de Universidad detectada -> Buscando en colección general de UniSimón...")
            contexto_externo = rag.search_universidad_docs(pregunta_usuario, n_results=30)
            fuente_info = "Documentación Institucional de la Universidad Simón Bolívar"
        else:
            print("[i] 🧠 Intención General detectada -> Usando conocimiento interno del modelo...")
            contexto_externo = None

        prompt_para_ia = pregunta_usuario
        if contexto_externo:
            prompt_para_ia = (
                f"Información de referencia recuperada desde {fuente_info}:\n{contexto_externo}\n\n"
                f"Pregunta del usuario: {pregunta_usuario}"
            )

        memory.add_message("user", prompt_para_ia)
        historial_actual = memory.get_formatted_history()

        print("\n--- ROBOT ---")
        t_inicio_gen = time.time()
        
        # --- STREAMING TOKEN POR TOKEN EN TIEMPO REAL ---
        stream = llm.create_chat_completion(
            messages=historial_actual,
            max_tokens=2048,
            temperature=0.3,
            stream=True
        )

        respuesta_chunks = []
        for chunk in stream:
            delta = chunk["choices"][0]["delta"]
            if "content" in delta and delta["content"] is not None:
                token_texto = delta["content"]
                print(token_texto, end="", flush=True)
                respuesta_chunks.append(token_texto)

        t_fin_gen = time.time()
        print("\n-----------------------------------")

        respuesta_ia = "".join(respuesta_chunks).strip()
        memory.add_message("assistant", respuesta_ia)

        # --- MÉTRICAS DE RENDIMIENTO ---
        tiempo_total = t_fin_gen - t_inicio_gen
        tokens_generados = len(respuesta_ia.split()) * 1.3  # Aproximación basada en conteo de palabras
        tokens_por_segundo = tokens_generados / tiempo_total if tiempo_total > 0 else 0

        print(f"⏱️ [Métricas] Latencia Total: {tiempo_total:.2f}s | Velocidad estimada: ~{tokens_por_segundo:.2f} tok/s")
        print("--------------------------------------------------")

if __name__ == "__main__":
    iniciar_sistema_exposicion()