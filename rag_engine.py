import os
import wikipedia
from langchain_text_splitters import RecursiveCharacterTextSplitter
import chromadb

class LocalRAGEngine:
    def __init__(self, doc_folder="./documents", db_path="./chroma_db"):
        self.doc_folder = doc_folder
        self.db_path = db_path
        
        # Configurar idioma de Wikipedia en español
        wikipedia.set_lang("es")
        
        # Inicializar ChromaDB de forma local y persistente
        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        self.collection = self.chroma_client.get_or_create_collection(name="audacia_knowledge")
        
        # Procesar documentos automáticamente al iniciar
        self.ingest_documents_if_needed()

    def ingest_documents_if_needed(self):
        """Lee los archivos .md (y opcionalmente .txt) de la carpeta, los divide y los indexa."""
        if not os.path.exists(self.doc_folder):
            os.makedirs(self.doc_folder)
            print(f"[i] Carpeta '{self.doc_folder}' creada. Coloca tus archivos .md institucionales ahí.")
            return

        # Buscamos archivos .md o .txt
        doc_files = [f for f in os.listdir(self.doc_folder) if f.endswith((".md", ".txt"))]
        if not doc_files:
            print("[i] No se encontraron archivos .md en la carpeta de documentos por ahora.")
            return

        print(f"[i] Procesando {len(doc_files)} documentos institucionales en Markdown...")
        
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        
        for file in doc_files:
            file_path = os.path.join(self.doc_folder, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                
                # Dividir el texto en fragmentos (chunks)
                chunks = text_splitter.split_text(full_text)
                
                # Registrar en ChromaDB asegurando IDs únicos
                ids = [f"{file}_chunk_{i}" for i in range(len(chunks))]
                metadatas = [{"source": file} for _ in chunks]
                
                # Añadir a la colección
                self.collection.upsert(
                    documents=chunks,
                    ids=ids,
                    metadatas=metadatas
                )
                print(f"✅ Indexado con éxito: {file} ({len(chunks)} fragmentos)")
            except Exception as e:
                print(f"⚠️ Error procesando el archivo {file}: {e}")

    def search_local_docs(self, query: str, n_results=30):
        """Busca en los documentos locales en Markdown de AudacIA y la Universidad."""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            documents = results.get("documents", [[]])[0]
            if not documents:
                return None
            return "\n---\n".join(documents)
        except Exception as e:
            print(f"⚠️ Error en búsqueda local: {e}")
            return None

    def search_wikipedia(self, query: str):
        """Busca un resumen confiable en Wikipedia en español."""
        try:
            print(f"[i] Consultando Wikipedia para: '{query}'...")
            summary = wikipedia.summary(query, sentences=3)
            return f"[Fuente externa - Wikipedia]: {summary}"
        except wikipedia.exceptions.DisambiguationError as e:
            try:
                summary = wikipedia.summary(e.options[0], sentences=3)
                return f"[Fuente externa - Wikipedia]: {summary}"
            except Exception:
                return None
        except Exception:
            return None