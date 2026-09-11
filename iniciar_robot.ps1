Write-Host "Iniciando el Robot Expositor de AudacIA..." -ForegroundColor Cyan

# Al montar la carpeta completa (${PWD}:/app), Docker lee el codigo en vivo
# e incluye automaticamente models, documents, chroma_db y la base de memoria.
# Anade -e HACU_DEBUG=1 para ver router, latencias y diagnostico en consola.
docker run --gpus all -it --rm `
  -v "${PWD}:/app" `
  robot-expositor
