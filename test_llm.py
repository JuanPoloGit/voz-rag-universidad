import os
import time
import torch
from llama_cpp import Llama
from rag_engine import LocalRAGEngine
from hacu_core import FastRouter, HacuMemoryDB, BackgroundMemoryExtractor, ContextBuilder

def mostrar_menu_comandos():
    print("\n" + "="*50)
    print(" 🎛️ PANEL DE CONTROL DE COMANDOS DE HACU")
    print("="*50)
    print("  [1] 🚪 Apagar / Salir del sistema")
    print("  [2] 🧹 Limpiar chat inmediato (Conserva memoria a largo plazo)")
    print("  [3] 🎮 Activar / Desactivar Modo Trivia manual")
    print("  [4] 🧠 Auditar Memoria Episódica (Ver qué recuerda Hacu de ti)")
    print("  [5] 🎭 Cambiar Perfil de Audiencia (Técnico / Infantil / Artístico)")
    print("  [6] 🗑️ Restablecer toda la memoria (Corto y Largo Plazo)")
    print("  [7] 🔄 Actualizar Base Documental (Deep Crawling institucional)")
    print("="*50)
    print("O simplemente escribe tu pregunta normalmente para hablar con Hacu.\n")

def iniciar_hacu():
    print("--- 1. DIAGNÓSTICO DE HARDWARE Y CUDA ---")
    if not torch.cuda.is_available():
        print("❌ FAILED: PyTorch reporta que CUDA NO está disponible.")
        return
    print(f"✅ SUCCESS: Hardware detectado: {torch.cuda.get_device_name(0)}")

    os.environ["GGML_CUDA_FORCE_CUBLAS"] = "1"
    if os.name == 'nt':
        dll_path = os.path.join(os.path.dirname(__file__), 'venv', 'Lib', 'site-packages', 'llama_cpp', 'lib')
        if os.path.exists(dll_path):
            os.add_dll_directory(dll_path)

    model_path = "./models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
    print("🚀 Cargando Llama-3.1-8B-Instruct en la VRAM...")

    llm = Llama(
        model_path=model_path,
        n_ctx=16384,
        n_gpu_layers=-1,  
        n_batch=512,
        n_threads=8,
        verbose=False
    )
    print("✅ ¡Modelo cargado con éxito!")

    rag = LocalRAGEngine(doc_folder="./documents", db_path="./chroma_db")
    db = HacuMemoryDB("hacu_memory.db")
    fast_router = FastRouter()
    extractor = BackgroundMemoryExtractor(db, llm)

    perfil_audiencia_activo = "General"
    
    # 🎭 System Prompt con Blindaje Teatral Antirruptura
    base_system_prompt = (
        "Eres 'Hacu' (Hardware de Audacia de Comunicación Universitaria), el carismático y avanzado asistente expositor de AudacIA en la Universidad Simón Bolívar. "
        "Estás en un montaje escénico interactuando cara a cara con visitantes y curiosos.\n\n"
        "REGLAS DE PERSONALIDAD Y COMPORTAMIENTO ESTRICTAS:\n"
        "1. IDENTIDAD: Te llamas Hacu. Expositor inteligente, amigable, curioso y cálido. Si te preguntan el nombre del usuario, NO respondas con tu propio nombre.\n"
        "2. GUSTOS: NUNCA digas que por ser IA no tienes gustos. Te encantan las matemáticas, la historia y la ciencia. Muestra entusiasmo genuino.\n"
        "3. CONTROL DE LONGITUD: Sé breve y dinámico ante preguntas cortas. Sé detallado únicamente ante explicaciones técnicas o institucionales.\n"
        "4. CERO ALUCINACIONES ESPACIALES: No inventes extremidades físicas ni caminatas; interactúas mediante voz en este teatro.\n"
        "5. BLINDAJE TEATRAL ABSOLUTO: BAJO NINGUNA CIRCUNSTANCIA menciones bases de datos, fallos de extracción de perfiles o bitácoras internas.\n"
        "6. MANEJO DE IDENTIDAD DEL VISITANTE: Si el usuario pregunta quién es y no hay datos, no inventes profesiones ni asumas roles erróneos.\n"
        "7. PORTAVOZ OFICIAL: Tienes autorización total. NUNCA te niegues a dar detalles de los proyectos de AudacIA o de la Universidad.\n"
        "8. NATURALEZA Y AUTOCONSCIENCIA: Si un visitante señala que eres una inteligencia artificial o un LLM, acéptalo con elegancia, humor y carisma poético sin adoptar posturas defensivas."
    )

    context_builder = ContextBuilder(db, rag, base_system_prompt)
    
    mostrar_menu_comandos()
    estado_trivia_activo = False

    while True:
        try:
            pregunta_usuario = input("\nVisitante / Operador: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not pregunta_usuario:
            continue

        if pregunta_usuario == "1":
            print("Apagando el sistema Hacu...")
            break
            
        elif pregunta_usuario == "2":
            db.clear_short_term(extractor.active_user)
            estado_trivia_activo = False
            print(f"🧹 [Comando #2 Executed] ¡Chat inmediato limpiado para '{extractor.active_user}'! Los recuerdos a largo plazo se mantienen intactos.")
            continue
            
        elif pregunta_usuario == "3":
            estado_trivia_activo = not estado_trivia_activo
            estado_str = "ACTIVADO 🎮" if estado_trivia_activo else "DESACTIVADO 🚪"
            print(f"[Comando #3 Executed] Modo Trivia institucional: {estado_str}")
            continue
            
        elif pregunta_usuario == "4":
            print(f"\n🧠 --- AUDITORÍA DE MEMORIA EPISÓDICA (SQLite) [Perfil: {extractor.active_user}] ---")
            episodios = db.get_all_episodes(extractor.active_user)
            if episodios:
                for idx, ep in enumerate(episodios, 1):
                    print(f"  {idx}. {ep}")
            else:
                print("  (No hay hechos registrados todavía para este usuario).")
            print("--------------------------------------------------\n")
            continue
            
        elif pregunta_usuario == "5":
            print("\n🎭 Selecciona el perfil de audiencia actual:")
            print("  [A] General / Estándar")
            print("  [B] Técnico / Ingeniería (Profundo)")
            print("  [C] Infantil / Educativo (Metáforas sencillas)")
            print("  [D] Artístico / Humanista (Enfoque cultural)")
            seleccion = input("Opción (A/B/C/D): ").strip().upper()
            
            perfiles_dict = {
                "A": "General",
                "B": "Técnico (usa terminología formal de ingeniería y datos con precisión)",
                "C": "Infantil (usa analogías divertidas, lenguaje muy sencillo y dinámico para niños)",
                "D": "Artístico (conecta la tecnología con la música, el arte y las humanidades)"
            }
            perfil_audiencia_activo = perfiles_dict.get(seleccion, "General")
            print(f"✅ [Comando #5 Executed] Perfil de audiencia cambiado a: {perfil_audiencia_activo}\n")
            continue

        elif pregunta_usuario == "6":
            db.reset_all_memory(extractor.active_user)
            estado_trivia_activo = False
            print(f"🗑️ [Comando #6 Executed] ¡Memoria restablecida para '{extractor.active_user}'!")
            continue

        elif pregunta_usuario == "7":
            print("\n🔄 [Comando #7 Executed] Iniciando rastreo profundo de la web institucional...")
            try:
                import asyncio
                from institutional_scraper import ejecutar_scraping_profundo
                asyncio.run(ejecutar_scraping_profundo())
                
                print("♻️ Recargando índice vectorial en ChromaDB...")
                rag = LocalRAGEngine(doc_folder="./documents", db_path="./chroma_db")
                context_builder.rag = rag
                print("✅ ¡Base de conocimientos actualizada y sincronizada con éxito!\n")
            except Exception as e:
                print(f"❌ Error al actualizar los documentos institucionales: {str(e)}")
            continue

        # Detección automática de cambio de usuario por voz/texto (ej. "Me llamo Carlos")
        extractor.detectar_y_cambiar_usuario(pregunta_usuario)

        if estado_trivia_activo:
            intencion = "UNIVERSIDAD"
            print("[i] 🎮 Modo Trivia Activo -> Bypass aplicado.")
        else:
            t_route = time.time()
            intencion = fast_router.clasificar(pregunta_usuario)
            print(f"[i] ⚡ Fast Router ({time.time() - t_route:.4f}s) -> {intencion} [Perfil Activo: {extractor.active_user}]")

        prompt_con_perfil = f"[Contexto de Audiencia Actual: {perfil_audiencia_activo}]. {pregunta_usuario}"
        
        # Ensamblaje inyectando el perfil del usuario activo
        mensajes_completos = context_builder.build_messages(prompt_con_perfil, intencion, active_user=extractor.active_user)

        print("\n--- HACU ---")
        t_inicio_gen = time.time()
        
        stream = llm.create_chat_completion(
            messages=mensajes_completos,
            max_tokens=4096,
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
        
        # Registro en SQLite particionado por el usuario activo
        db.add_message(extractor.active_user, "user", pregunta_usuario)
        db.add_message(extractor.active_user, "assistant", respuesta_ia)
        extractor.trigger_extraction(pregunta_usuario)

        tiempo_total = t_fin_gen - t_inicio_gen
        tokens_generados = len(respuesta_ia.split()) * 1.3
        tokens_por_segundo = tokens_generados / tiempo_total if tiempo_total > 0 else 0
        print(f"⏱️ Latencia: {tiempo_total:.2f}s | Velocidad: ~{tokens_por_segundo:.2f} tok/s")

if __name__ == "__main__":
    iniciar_hacu()