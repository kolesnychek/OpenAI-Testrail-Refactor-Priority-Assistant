## OpenAI Testrail Refactor Priority Assistant

Пайплайн для TestRail:
1. бере кейси з секції;
2. нормалізує/рефакторить текст;
3. перевіряє пріоритети за Jira story + acceptance criteria;
4. створює нові refactored кейси в TestRail;
5. пише звіти в `output/`.

Головний файл запуску: `OpenAI-TestRail.py`.

## Що потрібно

1. `Python 3.12+`
2. Пакети з `requirements.txt`
3. Заповнений `.env`
4. Доступи:
   - валідний доступ до TestRail API;
   - валідний доступ до Jira API.

Скрипт виконує локальний рефакторинг (`refactor_case_with_agent -> refactor_case_locally`)

## Кроки для локального розгортання проєкту:

1. **Перевірте Python:**
   ```
   python3 --version
   ```

2. **Клонуйте репозиторій:**
   ```
   git clone <URL_вашого_репозиторію>
   cd <назва_папки>
   ```

3. **Створіть та активуйте віртуальне середовище:**
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```

4. **Встановіть залежності:**
   ```
   pip install -r requirements.txt
   ```

5. **Створіть `.env`:**
   ```
   cp .env.example .env
   ```

6. **Заповніть `.env` вашими значеннями:**
   ```
   TESTRAIL_URL=
   TESTRAIL_EMAIL=
   TESTRAIL_API_KEY=
   TESTRAIL_SECTION_ID=
   JIRA_BASE_URL=
   JIRA_USER_EMAIL=
   JIRA_API_TOKEN=
   ``` 

7. **Запустіть скрипт:**
   ```bash
   python3 OpenAI-TestRail.py
   ```