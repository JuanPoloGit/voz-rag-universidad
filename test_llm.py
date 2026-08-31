import os
import sys
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

    # Forzar ejecución en tarjeta dedicada
    os.environ["GGML_CUDA_FORCE_CUBLAS"] = "1"

    if os.name == 'nt':
        dll_path = os.path.join(os.path.dirname(__file__), 'venv', 'Lib', 'site-packages', 'llama_cpp', 'lib')
        if os.path.exists(dll_path):
            os.add_dll_directory(dll_path)

    from llama_cpp import Llama

    model_path = "./models/Qwen2.5-7B-Instruct-Q4_K_M.gguf"

    if not os.path.exists(model_path):
        print(f"❌ Error: No se encuentra el archivo del modelo en {model_path}")
        return

    print("🚀 Cargando Qwen 2.5 7B en la VRAM de la RTX 5070 Ti...")

    # n_ctx aumentado para soportar historiales largos y todos los fragmentos del RAG
    llm = Llama(
        model_path=model_path,
        n_ctx=16384,
        n_gpu_layers=-1,  
        verbose=False
    )

    print("✅ ¡Modelo cargado con éxito y listo para la instalación!")

    # Inicializar Motor RAG (lee los .md de la carpeta documents)
    rag = LocalRAGEngine(doc_folder="./documents", db_path="./chroma_db")

    # Inicializar Memoria Híbrida (max_exact_turns ajustado a 3 para no saturar)
    memory = ConversationalMemory(storage_path="exposicion_memory.json", max_exact_turns=3)

    system_prompt = (
        
        "Eres el robot expositor interactivo de AudacIA, en la Universidad Simón Bolívar. "
        "REGLAS DE OPERACIÓN: "
        "1. PRECISIÓN INSTITUCIONAL: Si el usuario pregunta por la universidad, AudacIA o proyectos institucionales, responde ÚNICAMENTE basándote en la información de referencia recuperada. NO inventes nombres ni proyectos. "
        "2. CERO ESPECULACIÓN (ANTI-ALUCINACIÓN): Si el usuario hace preguntas hipotéticas o intenta forzar conexiones irreales (ej. '¿Cómo influyó X científico famoso en el proyecto Y?'), responde claramente que no hay una relación directa institucional o que no tienes registro de ello. NUNCA inventes teorías absurdas ni mezcles ciencia teórica externa con los proyectos locales. "
        "3. CONTEXTO GENERAL: Si el usuario pregunta por cultura general o ciencia, usa tu memoria, pero mantén esos temas completamente separados de los proyectos de la universidad. "
        "4. IGNORAR RAG: Si la información recuperada no responde a la pregunta del usuario, simplemente ignórala. "
        "Tu tono es amable, muy profesional, tecnológico y objetivo."

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

        # --- 1. ENRUTADOR SEMÁNTICO (EVALUACIÓN DE LA PREGUNTA) ---
        # Extraemos un pedazo del historial para que el modelo entienda el contexto ("y qué avances tuvo?")
        historial_reciente = memory.get_formatted_history()[-4:] 
        texto_historial = "\n".join([f"{msg['role']}: {msg['content']}" for msg in historial_reciente if msg['role'] != 'system'])

        prompt_enrutador = (
            "Eres un clasificador lógico. Lee el historial reciente y la nueva pregunta del usuario.\n"
            "Tu tarea es decidir si la pregunta busca información sobre la Universidad Simón Bolívar, AudacIA o sus proyectos institucionales (escribe LOCAL), "
            "o si está preguntando por cultura general, personas históricas, ciencia, o siguiendo el hilo de una charla no institucional (escribe GENERAL).\n\n"
            f"Historial de contexto:\n{texto_historial}\n\n"
            f"Nueva pregunta a evaluar: {pregunta_usuario}\n\n"
            "Responde ÚNICAMENTE con la palabra LOCAL o GENERAL. No escribas nada más."
        )

        print("[i] Evaluando la intención de la pregunta...")
        # Llamada súper rápida al modelo (solo genera 10 tokens de evaluación)
        router_response = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt_enrutador}],
            max_tokens=10,
            temperature=0.0
        )
        
        intencion = router_response["choices"][0]["message"]["content"].strip().upper()
        
        # --- 2. FLUJO DE BÚSQUEDA CONDICIONAL ---
        contexto_externo = None
        fuente_info = None

        if "LOCAL" in intencion:
            print("[i] 🔎 Intención Institucional detectada -> Buscando en documentos de AudacIA...")
            contexto_externo = rag.search_local_docs(pregunta_usuario, n_results=15)
            fuente_info = "Documentación Interna (AudacIA / Universidad)"
        else:
            print("[i] 🧠 Intención General detectada -> Usando conocimiento interno y memoria...")
            # Como es una pregunta general o de continuidad, saltamos el RAG local para no confundir al modelo
            
            # (Opcional) Puedes habilitar Wikipedia aquí si consideras que es una pregunta general estática, 
            # pero para conversaciones fluidas (ej. "y qué más hizo?"), es mejor dejar que el LLM use su memoria.
            contexto_externo = None 

        # --- 3. INYECCIÓN DEL CONTEXTO Y GENERACIÓN ---
        prompt_para_ia = pregunta_usuario
        if contexto_externo:
            prompt_para_ia = (
                f"Información de referencia recuperada desde {fuente_info}:\n{contexto_externo}\n\n"
                f"Pregunta del usuario: {pregunta_usuario}"
            )

        # Registrar entrada del usuario en la memoria
        memory.add_message("user", prompt_para_ia)

        # Obtener historial completo enriquecido
        historial_actual = memory.get_formatted_history()

        print("Generando respuesta del robot...")
        response = llm.create_chat_completion(
            messages=historial_actual,
            max_tokens=2048, # Límite exclusivo para la respuesta (evita crash de memoria)
            temperature=0.3
        )

        respuesta_ia = response["choices"][0]["message"]["content"].strip()
        
        # Registrar respuesta del asistente
        memory.add_message("assistant", respuesta_ia)

        print("\n--- ROBOT ---")
        print(respuesta_ia)
        print("-----------------------------------")

if __name__ == "__main__":
    iniciar_sistema_exposicion()