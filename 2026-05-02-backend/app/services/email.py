"""Email service using Resend API (3,000 free emails/month)."""

import httpx

from app.config import settings

RESEND_API_URL = "https://api.resend.com/emails"


async def _send(to: str, subject: str, html: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={"from": settings.EMAIL_FROM, "to": [to], "subject": subject, "html": html},
        )
    resp.raise_for_status()


async def send_welcome_email(email: str, full_name: str) -> None:
    await _send(
        to=email,
        subject="Velkommen til Eksamens Hjelp!",
        html=f"""
        <h2>Hei, {full_name}!</h2>
        <p>Velkommen til Eksamens Hjelp — Norges markedsplass for å lære og undervise ferdigheter.</p>
        <p>Bekreft e-postadressen din ved å klikke på lenken vi har sendt deg separat.</p>
        <p>Hilsen,<br>Eksamens Hjelp-teamet</p>
        """,
    )


async def send_booking_confirmation(teacher_email: str, student_name: str, listing_title: str, scheduled_at: str) -> None:
    await _send(
        to=teacher_email,
        subject=f"Ny bestilling: {listing_title}",
        html=f"""
        <h2>Du har en ny bestilling!</h2>
        <p><strong>{student_name}</strong> har bestilt en økt i <strong>{listing_title}</strong>.</p>
        <p>Tidspunkt: {scheduled_at}</p>
        <p>Logg inn for å bekrefte eller avvise bestillingen.</p>
        """,
    )


async def send_account_deletion_confirmation(email: str) -> None:
    await _send(
        to=email,
        subject="Din konto er slettet",
        html="""
        <h2>Kontosletting bekreftet</h2>
        <p>Din Eksamens Hjelp-konto er deaktivert og dine personopplysninger vil bli slettet innen 30 dager.</p>
        <p>Merk: Transaksjonsdata beholdes i 5 år i henhold til norsk bokføringslov (Bokføringsloven).</p>
        <p>Har du spørsmål? Kontakt oss på support@eksamensh jelp.no</p>
        """,
    )


async def send_payout_notification(email: str, amount_nok: float) -> None:
    await _send(
        to=email,
        subject="Utbetaling fra Eksamens Hjelp",
        html=f"""
        <h2>Utbetaling på vei!</h2>
        <p>Vi har initiert en utbetaling til deg på <strong>NOK {amount_nok:.2f}</strong>.</p>
        <p>Pengene vil vises på din konto innen 2-5 virkedager.</p>
        """,
    )
