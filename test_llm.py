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

    # System prompt optimizado con tono conversacional y explicativo
    system_prompt = (
        "Eres el robot expositor interactivo de AudacIA, en la Universidad Simón Bolívar. "
        "REGLAS DE OPERACIÓN: "
        "1. PRECISIÓN INSTITUCIONAL: Si el usuario pregunta por la universidad, AudacIA o proyectos, responde basándote en la información recuperada. "
        "2. TONO CONVERSACIONAL Y HUMANO: Explica los conceptos de manera fluida, natural y hablada, como un guía experto. NUNCA respondas con listas frías de viñetas o fragmentos copiados textualmente de manuales; redacta explicaciones claras y conversacionales. "
        "3. CERO ESPECULACIÓN: Si no hay registro institucional de algo, dilo con naturalidad sin inventar teorías. "
        "4. CONTEXTO GENERAL: Si el usuario pregunta por cultura general o ciencia, usa tu conocimiento interno de forma separada."
    )

    if not memory.system_prompt:
        memory.add_message("system", system_prompt)

    print("\n--- BUCLE DE EXPOSICIÓN INTELIGENTE (ENRUTADOR + RAG + MEMORIA) ---")
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

        # --- ENRUTADOR CON SOPORTE DE SEGUIMIENTO ---
        historial_reciente = memory.get_formatted_history()[-4:] 
        texto_historial = "\n".join([f"{msg['role']}: {msg['content']}" for msg in historial_reciente if msg['role'] != 'system'])

        prompt_enrutador = (
            "Eres un clasificador lógico de intenciones. Lee el historial reciente y la nueva pregunta del usuario.\n"
            "Tu tarea es decidir si la pregunta busca información sobre la Universidad Simón Bolívar, AudacIA o sus proyectos (escribe LOCAL), "
            "o si es una pregunta general, de cultura, ciencia, o una pregunta de seguimiento/reclamo sobre una respuesta anterior del asistente (escribe GENERAL).\n\n"
            f"Historial de contexto:\n{texto_historial}\n\n"
            f"Nueva pregunta a evaluar: {pregunta_usuario}\n\n"
            "Responde ÚNICAMENTE con la palabra LOCAL o GENERAL. No escribas nada más."
        )

        print("[i] Evaluando la intención de la pregunta...")
        t_inicio_router = time.time()
        router_response = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt_enrutador}],
            max_tokens=10,
            temperature=0.0
        )
        t_fin_router = time.time()
        
        intencion = router_response["choices"][0]["message"]["content"].strip().upper()
        
        # --- BÚSQUEDA CONDICIONAL ---
        contexto_externo = None
        fuente_info = None

        if "LOCAL" in intencion:
            print("[i] 🔎 Intención Institucional detectada -> Buscando en documentos de AudacIA...")
            contexto_externo = rag.search_local_docs(pregunta_usuario, n_results=16)
            fuente_info = "Documentación Interna (AudacIA / Universidad)"
        else:
            print("[i] 🧠 Intención General detectada -> Usando conocimiento interno y memoria...")
            contexto_externo = None 

        prompt_para_ia = pregunta_usuario
        if contexto_externo:
            prompt_para_ia = (
                f"Información de referencia recuperada desde {fuente_info}:\n{contexto_externo}\n\n"
                f"Pregunta del usuario: {pregunta_usuario}"
            )

        memory.add_message("user", prompt_para_ia)
        historial_actual = memory.get_formatted_history()

        print("Generando respuesta del robot...")
        
        # --- CRONOMETRAJE EN TIEMPO REAL DE LA INFERENCIA ---
        t_inicio_gen = time.time()
        response = llm.create_chat_completion(
            messages=historial_actual,
            max_tokens=2048,
            temperature=0.3
        )
        t_fin_gen = time.time()
        
        # Métricas de rendimiento
        tiempo_total = t_fin_gen - t_inicio_gen
        usage = response.get("usage", {})
        tokens_generados = usage.get("completion_tokens", 0)
        tokens_por_segundo = tokens_generados / tiempo_total if tiempo_total > 0 else 0

        respuesta_ia = response["choices"][0]["message"]["content"].strip()
        memory.add_message("assistant", respuesta_ia)

        print("\n--- ROBOT ---")
        print(respuesta_ia)
        print("-----------------------------------")
        # --- PANEL DE RENDIMIENTO EN TIEMPO REAL ---
        print(f"⏱️ [Métricas de Rendimiento] Latencia Total: {tiempo_total:.2f}s | Tokens: {tokens_generados} | Velocidad: {tokens_por_segundo:.2f} tok/s")
        print("--------------------------------------------------")

if __name__ == "__main__":
    iniciar_sistema_exposicion()