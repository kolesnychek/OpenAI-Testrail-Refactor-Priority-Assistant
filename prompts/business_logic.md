BUSINESS_LOGIC_DICTIONARY = """
BUSINESS LOGIC TERMINOLOGY (MANDATORY)

All terms below are business-domain terms defined by the product.
They must not be renamed, generalized, or reinterpreted.

## Apps

- 'CWA"(Consumer Web App): a Chrome-based web application for Deaf users
- "ConvoCall": an iOS and Android mobile application for Deaf users
- "VIApp": a Chrome-based web application for Video Interpreters
 

USERS AND ROLES
- "DU" (Deaf User) — a Deaf or Hard of Hearing individual eligible to use VRS services.
- "HU" (Hearing User) — a hearing person participating in calls as a voice user.
- "VI" (Video Interpreter) — an interpreter facilitating communication between DU and HU.
- "VRS" (Video Relay Service) — a regulated telecommunications service for Deaf users.
- "VRI" (Video Remote Interpreting) — on-demand interpreting service not covered by FCC.

ACCOUNTS AND ENTITIES
- "Account" — a business entity containing customer information and service state.
- "Blocked Account" — an account created but not yet registered in URD; cannot make calls.
- "Active Account" — an account eligible to make calls.
- "Enterprise" — a company or organization account (Work/Device numbers).

NUMBERS AND REGISTRATION
- "TDN" (Ten Digit Number) — a phone number assigned to an Account.
- "Personal Number" — a number registered to an individual and required to be in URD.
- "Work Number" / "Device" — a number assigned to an Enterprise and not registered in URD.
- "Registration" — a record representing an attempt to create or register an Account.
- "Pending Registration" — a registration not fully completed or validated.
- "URD" (User Registration Database) — centralized FCC-mandated registration system.
- "URDID" — an identifier assigned by RL after successful URD registration.

CALL STATES AND FLOWS
- "Missed Call" — an incoming call not answered by the recipient.
- "Abandoned Call" — a call ended before being matched to an interpreter.
- "Redirect" — automatic navigation based on a business rule.
- "Landing Page" — the first page shown after a redirect.

BUSINESS PROCESSES
- "Signup" — the process of creating an account and obtaining a number.
- "Port-in" — transferring a number from another provider to Convo.
- "Port-out" — transferring a number from Convo to another provider.
- "Termination" — removal of a number from service and URD.
- "Transfer (URD)" — change of ownership for a registered number.
- "Remediation" — corrective action required by audit or regulatory review.

STATUSES AND DECISIONS
- "Active" — allowed to perform business operations.
- "Blocked" — restricted due to missing registration or rules.
- "Pending" — created but not finalized.
- "Completed" — successfully finalized.
- "Failed" — terminated without success.
- "Eligible" — meets all required business conditions.
- "Not eligible" — does not meet required conditions.

ERROR TYPES
- "Business Error" — rejection caused by business or regulatory rules.
- "Identity Error" — validation failure during identity verification.
- "Filling Error" — incorrect or incomplete registration data.

RULES
- Use terms exactly as defined.
- Do not replace terms with synonyms.
- Do not infer permissions, rights, or outcomes.
- If a term is not listed, keep the original wording from the test case.
"""
