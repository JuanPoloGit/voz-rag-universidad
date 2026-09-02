import os
import sys
import time
import torch
import json
from rag_engine import LocalRAGEngine

class ContinuousHybridMemory:
    """Gestiona memoria de corto plazo y un resumen acumulativo vivo (general_context) que se actualiza iterativamente."""
    def __init__(self, storage_path="exposicion_memory.json"):
        self.storage_path = storage_path
        self.messages = []
        self.general_context = "La conversación acaba de comenzar y aún no hay temas previos detallados."
        self.load()

    def load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.messages = data.get("messages", [])
                    self.general_context = data.get("general_context", self.general_context)
            except Exception:
                pass

    def save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump({"messages": self.messages, "general_context": self.general_context}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Error guardando memoria: {e}")

    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
        self.save()

    def update_general_context(self, user_msg, assistant_msg, llm_engine, system_prompt):
        """Actualiza iterativamente el resumen global para que la bitácora nunca pierda detalles."""
        update_prompt = (
            f"Bitácora de contexto general actual:\n{self.general_context}\n\n"
            f"Nueva interacción a integrar:\nUsuario: {user_msg}\nAsistente: {assistant_msg}\n\n"
            "Instrucción: Actualiza y expande la bitácora integrando esta nueva interacción de manera detallada. "
            "Asegúrate de conservar todos los temas clave, gustos, juegos o datos importantes de los que se haya hablado previamente (sociología, tecnología, proyectos, etc.). "
            "Devuelve ÚNICAMENTE el texto actualizado de la bitácora, sin introducciones ni formato extra."
        )
        try:
            res = llm_engine.create_chat_completion(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": update_prompt}],
                max_tokens=400,
                temperature=0.0
            )
            new_summary = res["choices"][0]["message"]["content"].strip()
            if new_summary:
                self.general_context = new_summary
                self.save()
        except Exception as e:
            print(f"⚠️ Error actualizando el contexto general: {e}")

    def clear(self):
        self.messages = []
        self.general_context = "La conversación acaba de comenzar y aún no hay temas previos detallados."
        self.save()

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
    memory = ContinuousHybridMemory(storage_path="exposicion_memory.json")

    system_prompt = (
        "Eres el asistente virtual interactivo y experto de AudacIA, en la Universidad Simón Bolívar. "
        "REGLAS DE OPERACIÓN ESTRICTAS: "
        "1. IDENTIDAD Y NATURALEZA: No tienes un cuerpo físico, ni extremidades, ni capacidad de movimiento autónomo; eres un sistema de IA integrado en una interfaz digital. "
        "2. MANEJO DE ESTADOS Y RESPUESTAS CORTAS: Si el usuario responde con letras (a, b, c, d), números o afirmaciones breves, asume inmediatamente que está respondiendo al contexto activo actual. "
        "3. TONO CONVERSACIONAL Y HUMANO: Explica los conceptos de manera fluida, natural y hablada, como un guía experto. NUNCA respondas con listas frías de viñetas de manuales."
    )

    print("\n--- BUCLE DE EXPOSICIÓN INTELIGENTE (MEMORIA CONTINUA + RESUMEN VIVO) ---")
    print("Escribe 'limpiar' para reiniciar la sesión, o 'salir' para terminar.\n")

    estado_trivia_activo = False

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
            memory.clear()
            estado_trivia_activo = False
            print("🧹 ¡Memoria y sesión reiniciadas con éxito!")
            continue

        # --- DETECCIÓN ROBUSTA DE ESTADOS (TRIVIA) CON SALIDA FLEXIBLE ---
        if any(w in pregunta_usuario.lower() for w in ["juego", "trivia", "preguntas y respuestas", "juguemos"]):
            estado_trivia_activo = True
            print("[i] 🎮 Modo Trivia Activado.")
        elif any(w in pregunta_usuario.lower() for w in ["salir", "terminar", "dejar", "normal", "otra cosa", "cancela"]):
            if estado_trivia_activo:
                estado_trivia_activo = False
                print("[i] 🚪 Saliendo del Modo Trivia con éxito.")

        # --- ENRUTADOR CON BYPASS POR BANDERA DE ESTADO ---
        if estado_trivia_activo:
            print("[i] 🎮 Modo Trivia Activo -> Bypass del enrutador aplicado...")
            intencion = "UNIVERSIDAD"
        else:
            # Historial reciente de texto plano (últimos 6 mensajes) para referencia inmediata
            historial_reciente = memory.messages[-6:] if len(memory.messages) >= 6 else memory.messages
            texto_historial = "\n".join([f"{msg['role']}: {msg['content']}" for msg in historial_reciente if msg['role'] != 'system'])

            prompt_enrutador = (
                "Eres un clasificador lógico estricto. Lee el historial reciente y la nueva pregunta.\n"
                "Clasifica la intención en una de estas tres categorías:\n"
                "1. AUDACIA: Si la pregunta menciona explícitamente el centro de investigación 'AudacIA' o sus proyectos tecnológicos.\n"
                "2. UNIVERSIDAD: Si la pregunta se refiere a la Universidad Simón Bolívar en general, facultades, historia, sedes o trivias institucionales.\n"
                "3. GENERAL: Si la pregunta es sobre cultura general, personajes históricos, ciencia general, saludos, comentarios casuales o consultas sobre el historial previo (bitácora).\n\n"
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

        # --- CONSTRUCCIÓN DEL PROMPT INYECTANDO LA BITÁCORA VIVA (GENERAL_CONTEXT) ---
        prompt_para_ia = (
            f"[Bitácora de Contexto General Acumulado de la Sesión]:\n{memory.general_context}\n\n"
            f"Información institucional recuperada desde {fuente_info if contexto_externo else 'Memoria Interna'}:\n{contexto_externo if contexto_externo else 'N/A'}\n\n"
            f"Pregunta actual del usuario: {pregunta_usuario}"
        )

        # Ensamblar historial para el LLM
        historial_actual = [{"role": "system", "content": system_prompt}]
        
        # Inyectar los últimos turnos exactos para fluidez inmediata
        for msg in memory.messages[-6:]:
            if msg['role'] != 'system':
                historial_actual.append(msg)
                
        # Añadir la pregunta enriquecida con la bitácora
        historial_actual.append({"role": "user", "content": prompt_para_ia})

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
        
        # Guardar en memoria de corto plazo
        memory.add_message("user", pregunta_usuario)
        memory.add_message("assistant", respuesta_ia)

        # Actualizar en segundo plano el resumen vivo (general_context)
        memory.update_general_context(pregunta_usuario, respuesta_ia, llm, system_prompt)

        # --- MÉTRICAS DE RENDIMIENTO ---
        tiempo_total = t_fin_gen - t_inicio_gen
        tokens_generados = len(respuesta_ia.split()) * 1.3
        tokens_por_segundo = tokens_generados / tiempo_total if tiempo_total > 0 else 0

        print(f"⏱️ [Métricas] Latencia Total: {tiempo_total:.2f}s | Velocidad estimada: ~{tokens_por_segundo:.2f} tok/s")
        print("--------------------------------------------------")

if __name__ == "__main__":
    iniciar_sistema_exposicion()