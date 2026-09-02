import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
import chromadb

class LocalRAGEngine:
    def __init__(self, doc_folder="./documents", db_path="./chroma_db"):
        self.doc_folder = doc_folder
        self.db_path = db_path
        
        # Inicializar ChromaDB de forma local y persistente
        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        
        # Colecciones exclusivas separadas para evitar cruce de información
        self.col_audacia = self.chroma_client.get_or_create_collection(name="audacia_knowledge")
        self.col_universidad = self.chroma_client.get_or_create_collection(name="universidad_knowledge")
        
        # Procesar documentos automáticamente al iniciar
        self.ingest_documents_if_needed()

    def ingest_documents_if_needed(self):
        """Lee los archivos .md y los distribuye en su colección correspondiente según el nombre."""
        if not os.path.exists(self.doc_folder):
            os.makedirs(self.doc_folder)
            print(f"[i] Carpeta '{self.doc_folder}' creada.")
            return

        doc_files = [f for f in os.listdir(self.doc_folder) if f.endswith((".md", ".txt"))]
        if not doc_files:
            print("[i] No se encontraron archivos en la carpeta de documentos.")
            return

        print(f"[i] Procesando {len(doc_files)} documentos institucionales...")
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        
        for file in doc_files:
            file_path = os.path.join(self.doc_folder, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    full_text = f.read()
                
                chunks = text_splitter.split_text(full_text)
                ids = [f"{file}_chunk_{i}" for i in range(len(chunks))]
                metadatas = [{"source": file} for _ in chunks]
                
                # Enrutar a la colección correcta según el nombre del archivo
                target_collection = self.col_audacia if "audacia" in file.lower() else self.col_universidad
                
                target_collection.upsert(
                    documents=chunks,
                    ids=ids,
                    metadatas=metadatas
                )
                print(f"✅ Indexado en colección específica: {file} ({len(chunks)} fragmentos)")
            except Exception as e:
                print(f"⚠️ Error procesando el archivo {file}: {e}")

    def search_audacia_docs(self, query: str, n_results=30):
        """Busca exclusivamente en la colección de AudacIA."""
        try:
            results = self.col_audacia.query(query_texts=[query], n_results=n_results)
            documents = results.get("documents", [[]])[0]
            return "\n---\n".join(documents) if documents else None
        except Exception as e:
            print(f"⚠️ Error en búsqueda de AudacIA: {e}")
            return None

    def search_universidad_docs(self, query: str, n_results=30):
        """Busca exclusivamente en la colección general de la Universidad Simón Bolívar."""
        try:
            results = self.col_universidad.query(query_texts=[query], n_results=n_results)
            documents = results.get("documents", [[]])[0]
            return "\n---\n".join(documents) if documents else None
        except Exception as e:
            print(f"⚠️ Error en búsqueda de Universidad: {e}")
            return None