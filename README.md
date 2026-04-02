## OpenAI Testrail Refactor Priority Assistant

## TestRail Pipeline:
1. Fetches test cases from a section
2. Normalizes/refactors the text
3. Validates priorities based on Jira story + acceptance criteria
4. Creates new refactored test cases in TestRail
5. Writes reports to the `output/` directory

Main file: `OpenAI-TestRail.py`.

## Requirements

1. `Python 3.12+`
2. Packages from `requirements.txt`
3. Configured `.env` file
4. Access:
   - valid TestRail API access
   - valid Jira API access

The script performs local refactoring (`refactor_case_with_agent -> refactor_case_locally`)

## Кроки для локального розгортання проєкту:

1. **Check Python:**
   ```
   python3 --version
   ```

2. **Clone the repository:**
   ```
   git clone <YOUR_REPOSITORY_URL>
   cd <PROJECT_FOLDER_NAME>
   ```

3. **Create and activate a virtual environment:**
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```

4. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

5. **Create `.env` file:**
   ```
   cp .env.example .env
   ```

6. **Fill in `.env` file with your values:**
   ```
   TESTRAIL_URL=
   TESTRAIL_EMAIL=
   TESTRAIL_API_KEY=
   TESTRAIL_SECTION_ID=
   
   JIRA_BASE_URL=
   JIRA_USER_EMAIL=
   JIRA_API_TOKEN=
   ``` 

7. **Run the script:**
   ```bash
   python3 OpenAI-TestRail.py
   ```
