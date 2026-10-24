"""Send emails through the Gmail API (HTTPS), with mandatory user confirmation.

Railway blocks outbound SMTP ports on non-Pro plans, so the Gmail API over HTTPS is used
with the same Google OAuth credentials (scope gmail.send). Drafting and sending are separate:
email_propose stores the draft; it is only sent through confirm_action in a later turn.
"""

import base64
import re
from email.message import EmailMessage

from pydantic_ai import RunContext

from agent.deps import Deps
from tools.actions import executor, propose
from tools.google_auth import google_error, google_request, guard

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DISCLAIMER = (
    "\n\n---\nCet email a été rédigé et envoyé par un agent IA à la demande de son utilisateur. "
    "Merci de vérifier les informations avant d'agir."
)


async def email_propose(ctx: RunContext[Deps], to: list[str], subject: str, body: str) -> dict:
    """Prepare an email and ask the user to confirm it. Nothing is sent until confirmation.

    Only use recipient addresses the user gave in the chat; if missing, ask for them.
    Show the full draft (recipients, subject, body) returned in the summary, then wait.

    Args:
        to: Recipient email addresses.
        subject: Email subject.
        body: Plain-text body, written in the user's name.
    """
    recipients = [a.strip() for a in to if a.strip()]
    if not recipients:
        return {"error": "Aucun destinataire : demande l'adresse email à l'utilisateur."}
    bad = [a for a in recipients if not EMAIL_RE.match(a)]
    if bad:
        return {"error": f"Adresse(s) invalide(s) : {', '.join(bad)}."}
    if len(recipients) > 10:
        return {"error": "Maximum 10 destinataires."}
    if not subject.strip() or not body.strip():
        return {"error": "L'objet et le contenu sont obligatoires."}

    summary = f"Envoyer un email à {', '.join(recipients)}\nObjet : {subject}\n\n{body}"
    return propose(ctx, "email.send", {"to": recipients, "subject": subject, "body": body}, summary)


@executor("email.send")
@guard
async def _send(ctx: RunContext[Deps], payload: dict) -> dict:
    msg = EmailMessage()
    msg["To"] = ", ".join(payload["to"])
    msg["Subject"] = payload["subject"]
    msg.set_content(payload["body"] + DISCLAIMER)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resp = await google_request(ctx.deps.http, "POST", SEND_URL, json={"raw": raw})
    if resp.status_code != 200:
        return google_error(resp, "l'envoi de l'email")
    return {"status": "sent", "to": payload["to"], "subject": payload["subject"]}
