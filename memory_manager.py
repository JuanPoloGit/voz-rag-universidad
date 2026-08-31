import json
import os
from datetime import datetime

class ConversationalMemory:
    def __init__(self, storage_path="exposicion_memory.json", max_exact_turns=5):
        self.storage_path = storage_path
        self.max_exact_turns = max_exact_turns  # Últimos 5 turnos exactos (10 mensajes)
        self.system_prompt = ""
        self.general_context = "La conversación acaba de comenzar y aún no hay temas previos detallados."
        self.exact_history = []  # Almacena los últimos turnos literales
        self.load_memory()

    def add_message(self, role: str, content: str):
        """Añade un mensaje y gestiona el traspaso al contexto general si supera el límite."""
        if role == "system":
            self.system_prompt = content
            self.save_memory()
            return

        new_msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.exact_history.append(new_msg)

        # Si el historial exacto supera el límite (max_exact_turns * 2), 
        # el par más antiguo pasa a enriquecer el contexto general.
        limit = self.max_exact_turns * 2
        if len(self.exact_history) > limit:
            old_pair = self.exact_history[:2]  # Sacamos la pareja más vieja (User + Assistant)
            self.exact_history = self.exact_history[2:]
            
            # Comprimimos la vieja pareja en el resumen del contexto general
            for m in old_pair:
                speaker = "El usuario" if m["role"] == "user" else "El asistente"
                self.general_context += f"\n- {speaker} conversó sobre: {m['content'][:120]}..."

        self.save_memory()

    def get_formatted_history(self):
        """Prepara el payload combinando el System Prompt, el Contexto General y los 5 turnos exactos."""
        messages = []
        
        # 1. System Prompt principal
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
            
        # 2. Contexto general permanente (Evita confusión ante cambios abruptos de tema)
        directive_context = (
            f"[CONTEXTO GLOBAL Y RESUMEN HISTÓRICO DE LA SESIÓN]\n"
            f"{self.general_context}\n"
            f"--------------------------------------------------\n"
            f"Usa este resumen global para recordar los temas anteriores si el usuario cambia de tema o pregunta por algo ya tratado."
        )
        messages.append({"role": "system", "content": directive_context})
        
        # 3. Turnos exactos recientes (Alta fidelidad)
        for m in self.exact_history:
            messages.append({"role": m["role"], "content": m["content"]})
            
        return messages

    def save_memory(self):
        """Persiste la estructura híbrida en disco."""
        data = {
            "last_updated": datetime.now().isoformat(),
            "system_prompt": self.system_prompt,
            "general_context": self.general_context,
            "exact_history": self.exact_history
        }
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"⚠️ Error al guardar la memoria: {e}")

    def load_memory(self):
        """Carga la memoria previa desde el JSON si existe."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.system_prompt = data.get("system_prompt", "")
                    self.general_context = data.get("general_context", "La conversación acaba de comenzar.")
                    self.exact_history = data.get("exact_history", [])
                    print(f"[i] Memoria híbrida cargada: Contexto general + {len(self.exact_history)} mensajes exactos.")
            except Exception as e:
                print(f"⚠️ No se pudo cargar la memoria previa: {e}")

    def clear_memory(self):
        """Reinicia la memoria por completo."""
        self.exact_history = []
        self.general_context = "La conversación acaba de comenzar y aún no hay temas previos detallados."
        if os.path.exists(self.storage_path):
            os.remove(self.storage_path)
        print("[i] Memoria restablecida por completo.")