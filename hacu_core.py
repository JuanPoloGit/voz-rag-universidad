import sqlite3
import re
import threading

class FastRouter:

    def __init__(self):
        self.reglas_audacia = r'\b(audacia|tanque|orion|holosand|rob[óo]tica|visi[óo]n artificial|sensores|cuda|machine learning|ia|inteligencia artificial)\b'
        self.reglas_unisimon = r'\b(universidad|sim[óo]n bol[íi]var|rector|facultades|campus|historia|institucional|carreras)\b'

    def clasificar(self, texto: str) -> str:
        texto_lower = texto.lower()
        if re.search(self.reglas_audacia, texto_lower):
            return "AUDACIA"
        elif re.search(self.reglas_unisimon, texto_lower):
            return "UNIVERSIDAD"
        return "GENERAL"

class HacuMemoryDB:
    """Gestor de memoria SQLite multi-perfil con migración automática de esquema."""
    def __init__(self, db_path="hacu_memory.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        
        # Migración automática si las tablas antiguas no tienen la columna user_id
        cursor.execute("PRAGMA table_info(short_term_history)")
        columns_st = [col[1] for col in cursor.fetchall()]
        if columns_st and "user_id" not in columns_st:
            cursor.execute("DROP TABLE short_term_history")
            
        cursor.execute("PRAGMA table_info(episodic_memory)")
        columns_ep = [col[1] for col in cursor.fetchall()]
        if columns_ep and "user_id" not in columns_ep:
            cursor.execute("DROP TABLE episodic_memory")

        cursor.execute('''CREATE TABLE IF NOT EXISTS short_term_history
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS episodic_memory
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, fact TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        self.conn.commit()

    def add_message(self, user_id, role, content):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO short_term_history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        self.conn.commit()

    def get_recent_history(self, user_id, limit=6):
        cursor = self.conn.cursor()
        cursor.execute("SELECT role, content FROM short_term_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        rows = cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

    def add_episode(self, user_id, fact):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO episodic_memory (user_id, fact) VALUES (?, ?)", (user_id, fact))
        self.conn.commit()

    def get_all_episodes(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT fact FROM episodic_memory WHERE user_id = ? ORDER BY id DESC", (user_id,))
        return [row[0] for row in cursor.fetchall()]

    def overwrite_episodes(self, user_id, facts_list):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM episodic_memory WHERE user_id = ?", (user_id,))
        for fact in facts_list:
            cursor.execute("INSERT INTO episodic_memory (user_id, fact) VALUES (?, ?)", (user_id, fact))
        self.conn.commit()
        
    def clear_short_term(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM short_term_history WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def reset_all_memory(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM short_term_history WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM episodic_memory WHERE user_id = ?", (user_id,))
        self.conn.commit()

class BackgroundMemoryExtractor:
    """Extrae hechos crudos y delega la resolución de contradicciones a la consolidación automática."""
    def __init__(self, db, llm_engine):
        self.db = db
        self.llm = llm_engine
        self.active_user = "visitante_principal"

    def detectar_y_cambiar_usuario(self, user_msg):
        msg_lower = user_msg.lower()
        match = re.search(r'^(hola,?\s+)?(me llamo|soy|mi nombre es)\s+([a-záéíóúñ]+)', msg_lower)
        if match:
            nombre = match.group(3).capitalize()
            self.active_user = nombre
            return nombre
        return self.active_user

    def trigger_extraction(self, user_msg):
        current_user = self.detectar_y_cambiar_usuario(user_msg)
        cleaned = user_msg.strip()
        
        if len(cleaned.split()) < 3 or cleaned.isdigit() or "?" in cleaned:
            return current_user

        def run():
            prompt = (
                "Extrae un único hecho objetivo, profesional o interés personal del siguiente mensaje de forma directa en tercera persona. "
                "REGLAS:\n"
                "1. NO uses viñetas ni prefijos (ej. Escribe 'Usa Windows 11' en lugar de '- Usa Windows 11').\n"
                "2. Si el mensaje es una broma, saludo, pregunta o no aporta datos permanentes, responde EXACTAMENTE: NINGUNO.\n"
                f"Mensaje: {user_msg}\nHecho extraído:"
            )
            try:
                res = self.llm.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=40,
                    temperature=0.0
                )
                hecho = res["choices"][0]["message"]["content"].strip()
                hecho = hecho.replace("El hecho extraído es:", "").replace("*", "").replace("-", "").strip()
                
                if hecho.upper() != "NINGUNO" and "NINGUNO" not in hecho.upper() and len(hecho) > 4:
                    self.db.add_episode(current_user, hecho)
                    
                    # Disparar consolidación más rápido (al llegar a 5 hechos)
                    if len(self.db.get_all_episodes(current_user)) >= 5:
                        self.consolidate_memory(current_user)
            except Exception:
                pass
        
        t = threading.Thread(target=run)
        t.start()
        return current_user

    def consolidate_memory(self, user_id):
        """Lee el historial, resuelve contradicciones cronológicas y reescribe la base de datos de forma limpia."""
        hechos = self.db.get_all_episodes(user_id)
        if not hechos:
            return
        
        prompt = (
            "Analiza la siguiente lista de datos sobre un usuario (ordenados del más antiguo al más reciente). "
            "Tu tarea es crear un perfil final limpio y conciso. \n"
            "REGLAS CRÍTICAS:\n"
            "1. ELIMINA datos repetidos.\n"
            "2. RESUELVE CONTRADICCIONES respetando el dato más reciente (ej. si antes usaba Linux y luego dice Windows, mantén solo Windows).\n"
            "3. Devuelve cada hecho válido en una línea separada, estrictamente SIN VIÑETAS.\n"
            "Historial a consolidar:\n"
            + "\n".join([f"- {h}" for h in reversed(hechos)]) # Se invierte para que el LLM lea de viejo a nuevo
        )
        try:
            res = self.llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.0
            )
            texto_limpio = res["choices"][0]["message"]["content"].strip()
            nuevos_hechos = [line.strip("- *").strip() for line in texto_limpio.split("\n") if len(line.strip()) > 4]
            if nuevos_hechos:
                self.db.overwrite_episodes(user_id, nuevos_hechos)
        except Exception:
            pass

class ContextBuilder:
    """Ensamblador dinámico redactado en prosa natural para aislar perfiles sin fugas de etiquetas."""
    def __init__(self, db, rag_engine, system_prompt):
        self.db = db
        self.rag = rag_engine
        self.system_prompt = system_prompt

    def build_messages(self, user_msg, intencion, active_user="visitante_principal"):
        contexto_externo = ""
        fuente_info = ""
        
        if intencion == "AUDACIA":
            n_res = 6 if any(k in user_msg.lower() for k in ["proyecto", "todos", "cuales", "listar", "lista"]) else 2
            contexto_externo = self.rag.search_audacia_docs(user_msg, n_results=n_res)
            fuente_info = "documentación interna de AudacIA"
        elif intencion == "UNIVERSIDAD":
            n_res = 4 if any(k in user_msg.lower() for k in ["facultad", "todos", "cuales", "historia"]) else 2
            contexto_externo = self.rag.search_universidad_docs(user_msg, n_results=n_res)
            fuente_info = "documentación institucional de la Universidad Simón Bolívar"

        episodios = self.db.get_all_episodes(active_user)
        
        # Redacción en lenguaje natural para que el LLM no imite metadatos en corchetes
        prompt_enriquecido = f"Estás hablando con {active_user}.\n"
        if episodios:
            prompt_enriquecido += f"Lo que sabes previamente sobre esta persona es lo siguiente:\n" + "\n".join([f"- {e}" for e in episodios]) + "\n\n"
        
        if contexto_externo:
            prompt_enriquecido += f"Información recuperada de la {fuente_info}:\n{contexto_externo}\n\n"
        
        prompt_enriquecido += f"Comentario actual del usuario: {user_msg}"

        historial = [{"role": "system", "content": self.system_prompt}]
        historial.extend(self.db.get_recent_history(active_user, limit=6))
        historial.append({"role": "user", "content": prompt_enriquecido})
        
        return historial 