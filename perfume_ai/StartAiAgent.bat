@echo off
cd /d "%~dp0"

echo Starting Redis...
start "Redis" cmd /k "docker run -p 6379:6379 redis"

timeout /t 2 /nobreak >nul

echo Starting Celery...
start "Celery" cmd /k ".venv\Scripts\celery -A perfume_ai worker --pool=threads --concurrency=4 --loglevel=info"

exit