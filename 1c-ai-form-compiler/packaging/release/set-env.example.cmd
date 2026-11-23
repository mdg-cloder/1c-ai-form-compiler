@echo off
chcp 65001 >nul
rem ============================================================================
rem  one-c-form-compiler: пример настройки переменных окружения.
rem
rem  1. Скопируйте этот файл в set-env.cmd и замените значения-заглушки своими.
rem  2. В окне cmd выполните:  call set-env.cmd
rem     Переменные действуют только в этом окне. ИИ-агент, запущенный из этого
rem     окна, тоже их видит.
rem  Утилита читает только переменные окружения; файлы .env она не загружает.
rem  Файл сохраняйте в кодировке UTF-8 без BOM.
rem ============================================================================

rem --- Проверка формы в 1С (runtime-check) -----------------------------------
rem Полный путь к толстому клиенту 1С:Предприятие 8.3.
set "ONE_C_FORM_1CV8=C:\Program Files\1cv8\8.3.x.xxxx\bin\1cv8.exe"

rem Каталог ТЕСТОВОЙ файловой информационной базы. Рабочую базу не указывайте.
set "ONE_C_FORM_INFOBASE=C:\path\to\test-infobase"

rem Пользователь тестовой базы (оставьте пустым, если в базе нет пользователей).
rem У пользователя снимите "Защиту от опасных действий".
set "ONE_C_FORM_INFOBASE_USER="

rem ИМЯ переменной, в которой лежит пароль (не сам пароль).
rem Пароль передаётся 1cv8.exe в командной строке и виден другим процессам:
rem используйте отдельного тестового пользователя с одноразовым паролем.
set "ONE_C_FORM_INFOBASE_PASSWORD_ENV=ONE_C_FORM_TEST_PASSWORD"
set "ONE_C_FORM_TEST_PASSWORD="

rem --- Необязательно: внешний исполняемый v8unpack вместо встроенного --------
rem set "ONE_C_FORM_V8UNPACK=C:\path\to\v8unpack.exe"

rem --- Необязательно: встроенная команда "ai" --------------------------------
rem ИИ-агенту (Claude Code, Codex и др.) эти переменные не нужны.
rem В этой сборке нет Anthropic SDK: работает только провайдер openai
rem (провайдер anthropic/claude - только при установке из исходников:
rem pip install ".[claude]").
rem set "ONE_C_FORM_AI_PROVIDER=openai"
rem Модель для openai указывается обязательно.
rem set "ONE_C_FORM_AI_MODEL=<имя модели>"
rem ИМЯ переменной с ключом API (по умолчанию OPENAI_API_KEY).
rem set "ONE_C_FORM_AI_API_KEY_ENV=OPENAI_API_KEY"
rem set "OPENAI_API_KEY=<ваш ключ>"
rem Таймаут запроса в секундах: больше 0 и не больше 300, по умолчанию 30.
rem Пока ONE_C_FORM_AI_MAX_OUTPUT_TOKENS НЕ задана, команда ai сама поднимает
rem таймаут минимум до 120 секунд, а лимит ответа - до 16000 токенов.
rem Если задать ONE_C_FORM_AI_MAX_OUTPUT_TOKENS, таймаут не поднимается:
rem тогда задайте и ONE_C_FORM_AI_TIMEOUT_SECONDS (например, 120).
rem set "ONE_C_FORM_AI_TIMEOUT_SECONDS=120"
rem set "ONE_C_FORM_AI_MAX_INPUT_BYTES=256000"
rem set "ONE_C_FORM_AI_MAX_OUTPUT_BYTES=64000"
rem set "ONE_C_FORM_AI_MAX_OUTPUT_TOKENS=16000"
