import asyncio
import os

import aiohttp
from dotenv import load_dotenv


load_dotenv()
TESTRAIL_URL = os.getenv("TESTRAIL_URL")
TESTRAIL_EMAIL = os.getenv("TESTRAIL_EMAIL")
TESTRAIL_API_KEY = os.getenv("TESTRAIL_API_KEY")
TESTRAIL_PROJECT_ID = os.getenv("TESTRAIL_PROJECT_ID")
SECTION_ID = int(os.getenv("TESTRAIL_SECTION_ID", "0"))


async def main():
    if not all([TESTRAIL_URL, TESTRAIL_EMAIL, TESTRAIL_API_KEY, SECTION_ID]):
        raise ValueError("Missing TESTRAIL_* env vars")

    auth = aiohttp.BasicAuth(TESTRAIL_EMAIL, TESTRAIL_API_KEY)
    async with aiohttp.ClientSession(auth=auth) as session:
        section_url = f"{TESTRAIL_URL}/index.php?/api/v2/get_section/{SECTION_ID}"
        async with session.get(section_url) as resp:
            resp.raise_for_status()
            section = await resp.json()

        project_id = section.get("project_id") or TESTRAIL_PROJECT_ID
        suite_id = section.get("suite_id")
        if not project_id:
            project_id = await resolve_project_id_from_suite(session, suite_id)
        if not project_id:
            raise ValueError("Could not resolve project_id from section. Set TESTRAIL_PROJECT_ID in .env")

        url = f"{TESTRAIL_URL}/index.php?/api/v2/get_cases/{project_id}&section_id={SECTION_ID}"
        if suite_id:
            url += f"&suite_id={suite_id}"

        async with session.get(url) as resp:
            resp.raise_for_status()
            payload = await resp.json()
            cases = payload.get("cases", []) if isinstance(payload, dict) else payload
            print(f"TestRail connection OK. Cases in section {SECTION_ID}: {len(cases)}")


async def resolve_project_id_from_suite(session: aiohttp.ClientSession, suite_id: int | None):
    if not suite_id:
        return None

    async with session.get(f"{TESTRAIL_URL}/index.php?/api/v2/get_projects") as resp:
        resp.raise_for_status()
        projects_payload = await resp.json()

    projects = projects_payload.get("projects", []) if isinstance(projects_payload, dict) else projects_payload
    for project in projects:
        project_id = project.get("id")
        if not project_id:
            continue

        async with session.get(f"{TESTRAIL_URL}/index.php?/api/v2/get_suites/{project_id}") as resp:
            if resp.status != 200:
                continue
            suites_payload = await resp.json()
        suites = suites_payload.get("suites", []) if isinstance(suites_payload, dict) else suites_payload

        for suite in suites:
            if suite.get("id") == suite_id:
                return project_id

    return None


if __name__ == "__main__":
    asyncio.run(main())
