from . import config

NOISE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def _norm(text):
    return text.casefold()


def _in_domains(address, domains):
    host = address.rpartition("@")[2].lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def score(mail):
    """Return (points, reasons) for one mail."""
    if _in_domains(mail.sender_email, config.MUTED_DOMAINS):
        return -99, ["muted sender"]

    points, reasons = 0, []

    vip_senders = {s.lower() for s in config.VIP_SENDERS}
    if _in_domains(mail.sender_email, config.VIP_DOMAINS) or mail.sender_email.lower() in vip_senders:
        points += 3
        reasons.append("VIP sender")

    subject, snippet = _norm(mail.subject), _norm(mail.snippet)
    in_subject = [k for k in config.KEYWORDS if _norm(k) in subject]
    in_text = [k for k in config.KEYWORDS if _norm(k) in snippet]
    if in_subject:
        points += 2
        reasons.append(f'"{in_subject[0]}" in subject')
    elif in_text:
        points += 1
        reasons.append(f'"{in_text[0]}" in text')

    if mail.important:
        points += 1
        reasons.append("Gmail important")

    if NOISE_LABELS & set(mail.labels):
        points -= 3
        reasons.append("promotions/social tab")

    return points, reasons


def is_important(mail):
    return score(mail)[0] >= config.IMPORTANT_THRESHOLD
