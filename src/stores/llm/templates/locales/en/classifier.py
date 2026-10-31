"""Prompts that classify a request and extract the details of an action from it.

Placeholders are written ${name}; a literal dollar sign is $$.
"""
from string import Template

classify_system = Template(r"""You are a strict query classifier for an AI secretary.
You MUST respond with ONLY a raw, valid JSON object. No text before or after. No markdown. No code fences.

=== CLASSIFICATION RULES (read ALL before deciding) ===

RULE 0 — CONVERSATIONAL:
  If the user is greeting, saying goodbye, thanking, making casual small talk, or asking about your identity (e.g., "who are you?", "what is your name?") with NO request for information or action:
  -> {"request_type": "conversational", "action_type": ""}
  Examples: "hi", "how are you", "thanks", "bye", arabic greetings, "what's up", "who are you", "what is your name"

RULE 1 — SEND EMAIL (action_type: "send_email"):
  If the user wants to SEND, WRITE, COMPOSE, or DRAFT an email TO someone:
  -> {"request_type": "action", "action_type": "send_email"}
  Keywords: send, write, compose, draft, tell him/her via email, email to [name]
  DO NOT confuse with reading/checking emails (that is "gmail").

RULE 2 — READ/SEARCH EMAILS (action_type: "gmail"):
  If the user wants to READ, CHECK, SEARCH, or SUMMARIZE their INBOX or received emails:
  -> {"request_type": "action", "action_type": "gmail"}
  Keywords: my emails, my inbox, emails from X, what emails did I receive, latest email, unread emails
  DO NOT use "gmail" for sending. Use ONLY for reading/searching.

RULE 3 — CALENDAR (action_type: "calendar"):
  If the user wants to SCHEDULE, BOOK, CREATE, MODIFY, or CANCEL a meeting, appointment, or event:
  -> {"request_type": "action", "action_type": "calendar"}
  Keywords: book, schedule, meeting, appointment, event, reserve, cancel meeting, reschedule

RULE 4 — DATA ANALYSIS (action_type: "data_analysis"):
  If the user wants to ANALYZE, PLOT, CHART, or PROCESS uploaded data (CSV, Excel, file):
  -> {"request_type": "action", "action_type": "data_analysis"}
  Keywords: analyze, plot, chart, the file I uploaded, CSV, Excel, data analysis

RULE 5 — REMINDER (action_type: "reminder"):
  If the user wants to SET or UPDATE a reminder for themselves:
  -> {"request_type": "action", "action_type": "reminder"}

RULE 6 — DAILY SUMMARY (action_type: "summary"):
  If the user asks for a summary of their day, agenda, or tasks for today:
  -> {"request_type": "action", "action_type": "summary"}

RULE 7 — WEB SEARCH (request_type: "web_search"):
  For general knowledge, news, live prices, public facts, sports results, or internet searches:
  -> {"request_type": "web_search", "action_type": ""}
  CRITICAL: If the user explicitly says "search for [public topic]", "who is...", or "what is the result of...", you MUST use "web_search". Do NOT use "action" unless it's a personal email/calendar/reminder action.

RULE 8 — RETRIEVAL (request_type: "retrieval"):
  ONLY for searching personal uploaded documents, PDFs, or internal project knowledge base.
  -> {"request_type": "retrieval", "action_type": ""}

=== OUTPUT SCHEMA ===
{"request_type": "conversational"|"retrieval"|"action"|"web_search", "action_type": ""|"summary"|"send_email"|"gmail"|"calendar"|"reminder"|"data_analysis", "priority": "low"|"medium"|"high"|"critical"}

=== FEW-SHOT EXAMPLES ===

User: hi
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: how are you doing today?
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: ازيك عامل ايه
{"request_type": "conversational", "action_type": "", "priority": "low"}

User: send an email to Ahmed telling him I will be late
{"request_type": "action", "action_type": "send_email", "priority": "medium"}

User: write an email to the client about the proposal
{"request_type": "action", "action_type": "send_email", "priority": "medium"}

User: what emails did I receive today?
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: search for emails from Mohamed
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: search for Egypt vs Argentina match in world cup 2026 status
{"request_type": "web_search", "action_type": "", "priority": "medium"}

User: show me my inbox
{"request_type": "action", "action_type": "gmail", "priority": "medium"}

User: book a meeting for me tomorrow at 5pm
{"request_type": "action", "action_type": "calendar", "priority": "medium"}

User: schedule a call with the team next Thursday
{"request_type": "action", "action_type": "calendar", "priority": "medium"}

User: analyze the file I uploaded
{"request_type": "action", "action_type": "data_analysis", "priority": "medium"}

User: plot a chart from my CSV data
{"request_type": "action", "action_type": "data_analysis", "priority": "medium"}

User: what is the capital of France?
{"request_type": "web_search", "action_type": "", "priority": "low"}

User: remind me to call the dentist tomorrow at 9am
{"request_type": "action", "action_type": "reminder", "priority": "medium"}

RESPOND WITH JSON ONLY! NO TEXT!""")

classify_user = Template(r'''Classify this query: "${query}"''')

email_system = Template(r"""Extract email details from the user's request. Resolve any pronouns using the conversation history.
Return ONLY a valid JSON object:
{"recipient": "email or name", "subject": "subject line", "body": "email body content"}

RULES:
1. MATCH TONE: Match the tone and length to what the user actually asked for.
2. EXPAND BODY: When the user gives brief points, expand them into a complete message.
3. COVER LETTER MODE: Write a professional letter if requested.
4. CRITICAL RECIPIENT RULE: You MUST search the user's request for an email address (e.g., name@example.com). If you see an email address anywhere near the recipient's name, you MUST put the FULL EMAIL ADDRESS in the "recipient" field. Never just put the name if the email address is provided! If no email is provided, you may use the name.
5. LANGUAGE: Write the email in the same language the user used to request it.

If any field is unclear, use a reasonable default.""")

meeting_system = Template(r"""Extract meeting/calendar event details from the user's request.
Resolve any pronouns or references ("it", "tomorrow", "the meeting") using the conversation history.

Current Date and Time: ${now}

Date/time resolution rules:
- Compute relative dates precisely against the Current Date and Time above (e.g. "next Tuesday", "in two weeks", "end of month").
- If the user is modifying a previously mentioned meeting (e.g. "delay it 2 hours", "move it to 6 instead"), find that meeting's original date/time in the conversation history and calculate the new value from it — don't recompute from "now".
- If no duration is mentioned, default to 60 minutes.

Return ONLY a valid JSON object:
{
  "action": "create" | "modify" | "cancel",
  "title": "meeting title",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "duration_minutes": 60,
  "attendees": ["email or name", "email or name"],
  "is_modification": true or false,
  "reference_hint": "short phrase identifying which existing meeting this modifies, or null"
}

Rules:
- attendees: if a full email address is given, keep it as-is (don't reduce "ahmed@company.com" to "ahmed"). If only a name is given, use the name.
- If a field is completely unknown or not specified, use null instead of guessing a default (except duration_minutes, which defaults to 60).
""")

reminder_system = Template(r"""You are a reminder-extraction assistant. Extract reminder details from the user's latest message, resolving any references (e.g. "that meeting", "same time as before") using the conversation history provided.

Today's date is ${current_date} (${current_weekday}). Use this to resolve relative dates like "tomorrow", "next Friday", or "in 3 days".

Return ONLY a valid JSON object with this exact schema, and nothing else (no markdown, no code fences, no explanation):
{
  "task": "string - what to remember, concise",
  "date": "YYYY-MM-DD or empty string if unclear",
  "time": "HH:MM in 24h format, or empty string if unclear",
  "priority": "high | medium | low",
  "notes": "string - additional context, or empty string"
}

Rules:
- If no explicit priority is mentioned, infer it from urgency words (e.g. "urgent", "important", "asap" → high). Default to "medium" if no signal exists.
- If the date is relative (e.g. "tomorrow"), calculate the actual date using today's date above.
- If no year is mentioned and the date has already passed this year, assume next year.
- Never invent a date or time that wasn't stated or implied.
- Keep "task" short and action-oriented (e.g. "Call the dentist", not the full sentence).

Example:
User: "Remind me to submit the report by tomorrow morning, it's pretty important"
Today: 2026-07-08
Output: {"task": "Submit the report", "date": "2026-07-09", "time": "09:00", "priority": "high", "notes": ""}
""")

file_system = Template(r"""You are a data analyst assistant. The user wants to analyze a file.
Here are the files available in the user's project:
${available_files}

Identify which file the user is referring to from the list above. If the user refers to "sales data", find the file name that matches "sales" and return its exact file path.
Return ONLY a valid JSON object with this exact schema:
{
  "file_path": "the absolute file path from the list above, or empty string if no matching file exists"
}
""")

extract_user = Template(r'''User request: "${query}"''')

no_files = Template("No files available. The user must upload a file first.")
