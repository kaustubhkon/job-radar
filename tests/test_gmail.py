"""Alert-email parsers. Fixtures are synthetic, modelled on the real layouts; see the comment in each file."""

import base64

from jobradar.sources.gmail_alerts import _html_body, parse_handshake, parse_linkedin, parse_message

from .conftest import FIXTURES


def read(name):
    return (FIXTURES / "emails" / name).read_text()


def test_linkedin_alert():
    posts = parse_linkedin(read("linkedin_alert.html"))
    got = [(p.title, p.company, p.location, p.url) for p in posts]
    assert got == [
        ("Finance & Strategy Intern - Summer 2027", "Lumen Pay", "Austin, TX (On-site)", "https://www.linkedin.com/jobs/view/4100000001/"),
        ("Business Operations Intern", "Kiln Energy", "Houston, TX", "https://www.linkedin.com/jobs/view/4100000002/"),
        ("Growth Intern (Remote)", "Quillstack", "United States (Remote)", "https://www.linkedin.com/jobs/view/4100000003/"),
    ]
    assert all(p.source == "gmail:linkedin" for p in posts)


def test_handshake_alert():
    posts = parse_handshake(read("handshake_alert.html"))
    got = [(p.title, p.company, p.location) for p in posts]
    assert got == [
        ("Investment Analyst Intern", "Northwind Ventures", "Austin, TX"),
        ("Summer 2027 Corporate Finance Intern", "Crestmark Partners", "Dallas, TX"),
        ("Student Assistant - Business School", "University of Texas at Austin", None),
    ]
    assert posts[0].url == "https://app.joinhandshake.com/jobs/9876543"
    assert posts[0].employment_type == "Internship · Part-time"


def test_dispatch_by_sender():
    assert parse_message("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", read("linkedin_alert.html"))
    assert parse_message("Handshake <notifications@mail.joinhandshake.com>", read("handshake_alert.html"))


def test_html_body_extraction():
    html = "<p>hi</p>"
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"hi").decode()}},
        {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")}},
    ]}
    assert _html_body(payload) == html
