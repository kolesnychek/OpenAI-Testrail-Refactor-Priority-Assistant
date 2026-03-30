from __future__ import annotations

import os
from typing import Awaitable, Callable

import aiohttp

VERBOSE_TERMINAL = os.getenv("VERBOSE_TERMINAL", "no").lower() in {"1", "true", "yes", "y"}


def log(message: str):
    if VERBOSE_TERMINAL:
        print(message)


class JiraTicketPrioritySubagent:
    name = "jira_ticket_priority_subagent"

    def __init__(self, audit_priority_for_case: Callable[[aiohttp.ClientSession, int, dict, dict], Awaitable[dict]]):
        self._audit_priority_for_case = audit_priority_for_case

    async def analyze_case(self, session: aiohttp.ClientSession, result: dict) -> dict:
        return await self._audit_priority_for_case(
            session,
            result["case_id"],
            result["raw_case"],
            result["refactored"],
        )

    async def analyze_section(self, session: aiohttp.ClientSession, results: list[dict]) -> list[dict]:
        audits: list[dict] = []
        log("[SUBAGENT:JIRA] Analyzing Jira story refs and case impact")
        for result in results:
            if result.get("status") != "prepared_only":
                continue
            audits.append(await self.analyze_case(session, result))
        return audits
