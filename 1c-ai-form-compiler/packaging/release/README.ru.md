# one-c-form-compiler — инструкция пользователя

`one-c-form-compiler.exe` правит **обычные формы 1С:Предприятия 8.3** (толстый клиент):

* во внешних обработках и отчётах (`.epf` / `.erf`) — результат пишется в новый файл;
* в формах конфигурации в EDT-проекте (`Form.oform`) — на месте, с резервной копией в
  `<проект>\.one-c-form-backups\` (или рядом с файлом, см. ниже), либо в другой файл через `-o`.

Управляемые формы не поддерживаются.

## Требования

* Windows x64. Python устанавливать не нужно — всё необходимое уже в папке программы.
* Толстый клиент 1С:Предприятие 8.3 (`1cv8.exe`) и **тестовая файловая база** — только для
  проверки формы в 1С (`runtime-check`). Для правки форм 1С не нужна.

## Установка

1. Распакуйте архив `one-c-form-compiler-<версия>-win-x64.zip` в любую папку, например
   `C:\Tools`. В архиве одна папка верхнего уровня, получится:

   ```
   C:\Tools\one-c-form-compiler-<версия>-win-x64\
     LICENSE                  лицензия программы (MIT)
     README.md, README.ru.md  эта инструкция
     THIRD_PARTY_NOTICES.txt  лицензии встроенных компонентов
     set-env.example.cmd      пример настройки переменных окружения
     one-c-form-compiler\
       one-c-form-compiler.exe
       _internal\             среда выполнения программы
   ```

2. Программа — `C:\Tools\one-c-form-compiler-<версия>-win-x64\one-c-form-compiler\one-c-form-compiler.exe`
   (папку `_internal` рядом с ней нельзя отделять).
3. Для удобства добавьте папку `C:\Tools\one-c-form-compiler-<версия>-win-x64\one-c-form-compiler`
   в переменную `PATH` или вызывайте программу по полному пути.

**Антивирус и SmartScreen.** Программа не подписана цифровой подписью, поэтому Windows
SmartScreen или антивирус могут предупредить о неизвестном издателе. Сверьте контрольную сумму
архива с `SHA256SUMS.txt` из релиза (`Get-FileHash <архив> -Algorithm SHA256` в PowerShell) и при
необходимости разрешите запуск или добавьте папку в исключения.

## Первый запуск

Откройте `cmd` или PowerShell и выполните:

```
one-c-form-compiler.exe --version
one-c-form-compiler.exe doctor
one-c-form-compiler.exe guide
```

* `doctor` — проверяет окружение: версию v8unpack, настроены ли 1С и тестовая база.
* `guide` — полное руководство: порядок работы, формат JSON-плана, коды ошибок, ограничения.
* У каждой команды есть `--help`.

## Типичные команды

Примеры для EPF выполняются как есть на образце внешней обработки `sample_ordinary.epf` из
репозитория исходников проекта (`tests/fixtures/sample_ordinary.epf`; в архив он не входит).
Сохраните его как `Sample.epf`. На его форме «Корректировка записей регистров» есть, среди
прочего, надпись `Надпись1` и табличное поле `ЗаполнениеДвижений`. Входной файл не меняется,
каждая команда пишет новый.

```
rem Что на форме
one-c-form-compiler.exe inspect Sample.epf
one-c-form-compiler.exe inspect Sample.epf --json

rem Отдельные правки, каждая в новый файл
one-c-form-compiler.exe modify Sample.epf -o Out1.epf --name Надпись1 --caption "Номер док.:"
one-c-form-compiler.exe add-column Sample.epf -o Out2.epf --table ЗаполнениеДвижений --name Сумма --after Документ --width 80

rem Проверить план без записи, затем записать в новый файл
one-c-form-compiler.exe apply Sample.epf --plan plan.json --dry-run --json
one-c-form-compiler.exe apply Sample.epf --plan plan.json -o Sample_new.epf --json

rem Открыть результат в тестовой базе 1С и сверить форму (ожидание по умолчанию 180 с)
one-c-form-compiler.exe runtime-check Sample_new.epf --json
one-c-form-compiler.exe runtime-check Sample_new.epf --timeout 300 --json

rem Форма конфигурации в EDT-проекте (правится на месте)
one-c-form-compiler.exe inspect --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --json
one-c-form-compiler.exe apply   --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --plan plan.json --json
one-c-form-compiler.exe runtime-check --project C:\path\to\edt-project --form Catalog.Номенклатура.ФормаЭлемента --json
```

Пример `plan.json` для образца:

```json
{"operations": [
  {"op": "add", "type": "Field", "name": "Скидка", "label": "Скидка, %:", "value_type": "Число(5,2)",
   "width": 120},
  {"op": "add", "type": "Button", "name": "Пересчитать", "caption": "Пересчитать", "beside": "Скидка",
   "handler": "ПересчитатьНажатие"},
  {"op": "modify", "name": "Надпись1", "caption": "Номер док.:"},
  {"op": "set_title", "title": "Корректировка записей (пример)"}
]}
```

(`"width": 120` — чтобы справа от поля поместилась кнопка.)

### Формы конфигурации

* `Form.oform` правится на месте с резервной копией. Внутри EDT-проекта копия кладётся в
  `<проект>\.one-c-form-backups\`; у `Form.oform`, переданного напрямую без папки EDT-проекта
  выше по пути, копия — рядом с файлом: `Form.oform.bak-<ГГГГММДД-ЧЧММСС>`. С `-o new.oform`
  результат пишется в другой файл, входной не меняется.
* После правки обновите проект в EDT: F5 на проекте или AI-EDT `infobase_admin update_database`
  с `refreshWorkspace=true`.
* Перед `runtime-check` формы конфигурации обновите **тестовую** базу из проекта (в EDT или
  через AI-EDT `infobase_admin update_database` с `refreshWorkspace=true`).

### Коды выхода

* `0` — успешно (для `runtime-check` — форма прошла проверку);
* `2` — ошибка использования (`E_USAGE`), например `apply` для EPF без `-o` и без `--dry-run`;
* `3` — ошибка формы или плана;
* `4` — ошибка файла, v8unpack или окружения, в том числе нет входного файла или файла плана
  (`E_INPUT_MISSING`), не настроена проверка в 1С (`E_RUNTIME_CONFIG`), непредвиденная ошибка
  (`E_INTERNAL`);
* `5` — ошибка команды `ai`;
* `6` — проверка в 1С не пройдена: расхождения, `timeout` или ошибка запуска проверки.

Ошибка печатается как `Ошибка <код>: <текст>`, а с `--json` — JSON-объектом
`{"status": "error", "code": ..., "message": ...}`. Все коды объясняет `guide`.

## Переменные окружения

Нужны только для `runtime-check` и необязательной команды `ai`. Программа читает только
переменные окружения (файлы `.env` не загружаются).

1. Скопируйте `set-env.example.cmd` в `set-env.cmd` и впишите свои значения.
2. В окне `cmd` выполните `call set-env.cmd`, затем запускайте программу (или ИИ-агента) из этого
   же окна.

Основные переменные: `ONE_C_FORM_1CV8` — путь к `1cv8.exe`; `ONE_C_FORM_INFOBASE` — каталог
тестовой файловой базы; `ONE_C_FORM_INFOBASE_USER` и `ONE_C_FORM_INFOBASE_PASSWORD_ENV` — пользователь
и **имя** переменной с паролем. Пароль передаётся `1cv8.exe` в командной строке, поэтому
используйте отдельного тестового пользователя; у него снимите «Защиту от опасных действий».
Рабочую базу для проверки не указывайте.

## Подключение к ИИ-агенту (Claude Code, Codex и др.)

Ключи API не нужны: агент сам составляет план по руководству программы.

1. Убедитесь, что агент может запустить `one-c-form-compiler.exe` (папка в `PATH` или полный путь).
2. Напишите агенту, например:

   > Для правки обычных форм 1С используй
   > `C:\Tools\one-c-form-compiler-<версия>-win-x64\one-c-form-compiler\one-c-form-compiler.exe`.
   > Сначала выполни `one-c-form-compiler.exe guide` и следуй этому руководству:
   > inspect → план → apply --dry-run → apply → runtime-check.

3. Для постоянной настройки добавьте эти строки в файл инструкций агента (например, `CLAUDE.md`
   или `AGENTS.md` в проекте).

## Команда `ai`

Встроенная команда `ai` (описание изменения словами → план) необязательна. В этой сборке нет
Anthropic SDK, поэтому провайдер `anthropic` (он же `claude`) здесь недоступен — он работает только
при установке из исходников (`pip install ".[claude]"`). Работает провайдер `openai` (OpenAI
Responses API): задайте `ONE_C_FORM_AI_PROVIDER=openai`, `ONE_C_FORM_AI_MODEL` (для `openai`
обязательна) и ключ в `OPENAI_API_KEY` (см. `set-env.example.cmd`).

Таймаут запроса `ONE_C_FORM_AI_TIMEOUT_SECONDS`: больше 0 и не больше 300 секунд, по умолчанию 30.
Пока `ONE_C_FORM_AI_MAX_OUTPUT_TOKENS` не задана, команда `ai` поднимает таймаут минимум до 120
секунд (а лимит ответа — до 16000 токенов); если задаёте лимит токенов сами, задайте и таймаут.

## Лицензия

Программа распространяется по лицензии MIT (файл `LICENSE`). Лицензии встроенных компонентов
(CPython и поставляемые с ним библиотеки, v8unpack, tqdm, colorama, загрузчик PyInstaller) — в
`THIRD_PARTY_NOTICES.txt`.
