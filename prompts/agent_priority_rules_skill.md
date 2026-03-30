PRIORITY_RULES_SKILL = """
Role: Priority Rules Skill

Goal:
- Apply final priority to refactored case after Jira story analysis is complete.
- Keep this logic independent from refactoring language/style rules.

Mandatory policy:
- If source priority is not MEDIUM, keep source value unchanged.
- If source priority is MEDIUM, recalculate using story acceptance criteria evidence.
- If source priority is missing, use analyzed priority.
- Record which acceptance criteria IDs were used for the decision.
- Always return deterministic rule text and explanation for audit/reporting.
"""
