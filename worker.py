import base64
import os
import time
from typing import Optional
from bs4 import BeautifulSoup  # pip install beautifulsoup4 (optional fallback for HTML-only mails)
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel, Field

# Import your existing classifier function
from classifier import classify_event

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TARGET_LABEL = os.getenv("GMAIL_QUERY", "is:unread")  # e.g., "is:unread label:Events"
POLL_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


# --- Pydantic Schemas for Guaranteed Valid JSON ---
class ExtractedEvent(BaseModel):
    summary: str = Field(description="Clean event title")
    organizers: list[str] = Field(description="Organizing clubs/departments, excluding Ashoka University")
    location: str = Field(description="Full venue phrase, e.g., 'AC04, HDFC Library, 2nd Floor' or 'TBD'")
    description: str = Field(description="Concise factual summary of the email without external elaboration")
    start_iso: str = Field(description="ISO 8601 start datetime in IST (+05:30), e.g., 2026-11-26T11:00:00+05:30")
    end_iso: str = Field(description="ISO 8601 end datetime in IST (+05:30). Default to start + 1 hour if unspecified.")


class ThreadUpdate(BaseModel):
    is_cancelled: bool
    has_changes: bool
    updated_summary: Optional[str] = None
    updated_location: Optional[str] = None
    updated_start_iso: Optional[str] = None
    updated_end_iso: Optional[str] = None


# --- Google Auth Helper ---
def get_google_services():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    gmail = build("gmail", "v1", credentials=creds)
    calendar = build("calendar", "v3", credentials=creds)
    return gmail, calendar


# --- Clean MIME Body & Header Extractor ---
def parse_message(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

    def extract_text(payload) -> str:
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        for part in payload.get("parts", []):
            text = extract_text(part)
            if text:
                return text
        # Fallback if email is HTML-only (common with student club Canva/Mailchimp flyers)
        if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            return BeautifulSoup(html, "html.parser").get_text(separator="\n")
        return ""

    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", "(No Subject)"),
        "date": headers.get("date", ""),
        "body": extract_text(msg["payload"]).strip(),
    }


# --- Gemini Extraction & Update Checks ---
def extract_event_details(email: dict) -> ExtractedEvent:
    prompt = f"""Extract the campus event details from this Ashoka University email.
Email Sent Timestamp: {email['date']}
From: {email['sender']}
Subject: {email['subject']}

Body:
{email['body']}"""

    resp = gemini_client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ExtractedEvent,
        },
    )
    return ExtractedEvent.model_validate_json(resp.text)


def check_thread_update(email: dict, existing_event: dict) -> ThreadUpdate:
    prompt = f"""Analyze if this follow-up email cancels or reschedules/updates the existing calendar event.
Existing Event: {existing_event.get('summary')} | Start: {existing_event['start'].get('dateTime')} | End: {existing_event['end'].get('dateTime')} | Venue: {existing_event.get('location')}
Email Sent Timestamp: {email['date']}
Subject: {email['subject']}
Body:
{email['body']}"""

    resp = gemini_client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ThreadUpdate,
        },
    )
    return ThreadUpdate.model_validate_json(resp.text)


# --- Main Processing Loop ---
def process_inbox():
    gmail, calendar = get_google_services()
    results = gmail.users().messages().list(userId="me", q=TARGET_LABEL).execute()
    messages = results.get("messages", [])

    for item in messages:
        raw_msg = gmail.users().messages().get(userId="me", id=item["id"], format="full").execute()
        email = parse_message(raw_msg)

        # 1. Check if thread already exists on Calendar
        existing_items = (
            calendar.events()
            .list(calendarId=CALENDAR_ID, privateExtendedProperty=f"threadId={email['thread_id']}")
            .execute()
            .get("items", [])
        )

        if existing_items:
            existing = existing_items[0]
            update = check_thread_update(email, existing)
            if update.is_cancelled:
                calendar.events().delete(calendarId=CALENDAR_ID, eventId=existing["id"]).execute()
                print(f"[DELETED] {existing.get('summary')}")
            elif update.has_changes:
                if update.updated_summary:
                    existing["summary"] = update.updated_summary
                if update.updated_location:
                    existing["location"] = update.updated_location
                if update.updated_start_iso and update.updated_end_iso:
                    existing["start"]["dateTime"] = update.updated_start_iso
                    existing["end"]["dateTime"] = update.updated_end_iso
                calendar.events().update(calendarId=CALENDAR_ID, eventId=existing["id"], body=existing).execute()
                print(f"[UPDATED] {existing.get('summary')}")
        else:
            # 2. Gatekeep with your fast D1 classifier
            verdict = classify_event(email["sender"], email["subject"], email["body"])
            if verdict["is_event"]:
                details = extract_event_details(email)
                body = {
                    "summary": details.summary,
                    "location": details.location,
                    "description": f"{details.description}\n\nOrganizers: {', '.join(details.organizers)}\nSource: {email['sender']}",
                    "start": {"dateTime": details.start_iso, "timeZone": "Asia/Kolkata"},
                    "end": {"dateTime": details.end_iso, "timeZone": "Asia/Kolkata"},
                    "extendedProperties": {
                        "private": {"threadId": email["thread_id"], "emailSource": email["sender"]}
                    },
                }
                calendar.events().insert(calendarId=CALENDAR_ID, body=body).execute()
                print(f"[CREATED] {details.summary} ({details.start_iso})")

        # 3. Mark email as read so it isn't reprocessed
        gmail.users().messages().modify(
            userId="me", id=email["id"], body={"removeLabelIds": ["UNREAD"]}
        ).execute()


if __name__ == "__main__":
    print("Starting Ashoka Events Gmail -> Calendar sync...")
    while True:
        try:
            process_inbox()
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(POLL_INTERVAL)
