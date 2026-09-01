Write-Host "Iniciando el Robot Expositor de AudacIA..." -ForegroundColor Cyan

# Al montar la carpeta completa (${PWD}:/app), Docker leerá tu código en vivo
# y también incluirá automáticamente las carpetas models, documents y chroma_db
docker run --gpus all -it --rm `
  -v "${PWD}:/app" `
  robot-expositor